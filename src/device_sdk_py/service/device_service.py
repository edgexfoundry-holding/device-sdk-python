# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The DeviceService implementation.

`DeviceService` implements `interfaces.DeviceServiceSDK`. The managed Devices /
DeviceProfiles / ProvisionWatchers are held by the internal cache singletons
(`cache.Devices()`, `cache.Profiles()`, `cache.ProvisionWatchers()`). All managed-entity
CRUD operations use a cache-first + rollback pattern: the local cache is updated first,
then the write is propagated to Core Metadata via a bounded thread pool. On a metadata
failure the cache change is rolled back and the error is propagated as an `EdgexError`
(KindServerError).

The `cache` module needs to be initialized (`create_device_cache` / `create_profile_cache` /
`create_provision_watcher_cache`) before this service is used.

Errors are raised as `EdgexError` from `internal.common.utils`.
"""

from __future__ import annotations

import importlib
import logging
import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from ..internal.cache import (
    ADMIN_STATE_UNLOCKED,
    AutoEvent,
    CacheError,
    Device,
    DeviceCommand,
    DeviceProfile,
    DeviceResource,
    ProvisionWatcher,
    Devices,
    Profiles,
    ProvisionWatchers,
)
from ..internal.common.utils import (
    KIND_CONTRACT_INVALID,
    KIND_ENTITY_DOES_NOT_EXIST,
    KIND_SERVER_ERROR,
    EdgexError,
    EdgexErrorKind,
    create_edgx_error,
    make_uid,
)
from ..internal.common.consts import OPERATING_STATE_UP
from ..internal.controller.http import RestController
from ..internal.controller.messaging.client import (
    MessageBusConfig,
    MessageClient,
    HostInfo,
    create_message_client,
    AUTH_MODE_NONE,
    DEFAULT_MESSAGEBUS_TYPE,
)
from ..internal.controller.messaging.publish import (
    publish_event,
    publish_system_event,
    DEFAULT_MAX_EVENT_SIZE,
    DEVICE_SYSTEM_EVENT_TYPE,
    SYSTEM_EVENT_ACTION_PROGRESS,
)
from ..internal.controller.messaging.callback import _get_base_service_name
from ..internal.metadata import MetadataClient
from ..internal.metadata.client import MetadataError
from ..internal.provision import (
    load_devices,
    load_profiles,
    load_provision_watchers,
)
from ..internal.transformer.transform import Event
from ..internal.clients import Logger, SecretProvider, MetricsManager
from ..interfaces import DeviceServiceSDK, UpdatableConfig
from ..models import AsyncValues, DiscoveredDevice

if TYPE_CHECKING:
    from app_functions_sdk_py.contracts.clients.logger import Logger

_logger = logging.getLogger(__name__)

#: The default host / port the HTTP server binds to when the configuration does not
#: provide them (matches the EdgeX device service default port).
_DEFAULT_HTTP_HOST = "0.0.0.0"
_DEFAULT_HTTP_PORT = 59986


@dataclass
class _DeviceServiceModel:
    """The lightweight DeviceService model used for the service AdminState check."""
    name: str = ""
    admin_state: str = ADMIN_STATE_UNLOCKED


class DeviceService(DeviceServiceSDK):
    """The DeviceService implementation of `DeviceServiceSDK`.

    The managed entity CRUD methods cover Devices, DeviceProfiles, ProvisionWatchers and
    AutoEvents. Each operation uses cache-first + rollback: the local cache is updated
    first, then the write is propagated to Core Metadata. On metadata failure the cache
    change is rolled back and the error is propagated.
    """

    def __init__(self, service_key: str, service_version: str, driver: Any,
                 configuration: Any = None, logger: Optional[logging.Logger] = None,
                 send_event_handler: Optional[Callable[[Any, str], None]] = None):
        """Create a new DeviceService for the given key, version and ProtocolDriver.

        Args:
            service_key: The name of the Device Service (e.g. "device-simple").
            service_version: The version of the Device Service.
            driver: The ProtocolDriver implementation.
            configuration: The service configuration (the Python `ConfigurationStruct`
                model is ported in a later phase; the options are read defensively).
            logger: An optional logger; defaults to the module logger.
            send_event_handler: Optional ``(event, correlation_id) -> None`` callback used
                by ``RestController.send_event`` to publish an ``Event`` (the message-bus
                seam; defaults to logging only).
        """
        self.service_key = service_key
        self.service_version = service_version
        self.driver = driver
        self.configuration = configuration
        self._logger = logger or _logger
        self._send_event_handler = send_event_handler

        #: The DeviceService model used by the command controller for the service
#: AdminState check.
        self.device_service_model = _DeviceServiceModel(name=service_key)

        #: The async readings channel handed to ProtocolDrivers via `async_values_channel`.
        self._async_values_channel: queue.Queue[AsyncValues] = queue.Queue()
        #: The discovered devices channel handed to ProtocolDrivers via
        #: `discovered_device_channel`.
        self._discovered_device_channel: queue.Queue[List[DiscoveredDevice]] = queue.Queue()

        #: The HTTP controller (FastAPI) created by `run()` / `_init_http_controller`.
        self.controller: Optional[RestController] = None
        #: Custom routes registered before the controller exists, applied on `run()`.
        self._pending_custom_routes: List[Tuple[str, Callable[..., Any], List[str]]] = []
        #: The custom configuration loaded via `load_custom_config`.
        self.custom_config: Optional[UpdatableConfig] = None
        self._custom_config_loaded = False

        #: Messaging client for publishing events and subscribing to commands/system-events.
        self._messaging_client: Optional[MessageClient] = None
        #: Parsed MessageBus configuration.
        self._message_bus_config_obj: Optional[MessageBusConfig] = None
        #: Background threads for async pumps and messaging subscriptions.
        self._async_pump_thread: Optional[threading.Thread] = None
        self._discovered_pump_thread: Optional[threading.Thread] = None
        self._command_sub_thread: Optional[threading.Thread] = None
        self._system_events_thread: Optional[threading.Thread] = None
        #: Shutdown signal for background pumps.
        self._shutdown_event = threading.Event()

        #: The lazily imported AutoEvent manager (see `_auto_event_manager`).
        self._auto_event_manager_instance: Any = None

        #: The lazily created Core Metadata client (see `_metadata_client`).
        self._metadata_client_instance: Optional[MetadataClient] = None
        #: Bounded executor that runs the Core Metadata write-back calls off the caller's
        #: thread so the HTTP I/O never blocks the command controller / caller directly.
        #: Created lazily by `_run_metadata` and shut down in `_shutdown`.
        self._metadata_executor: Optional[ThreadPoolExecutor] = None

        #: Discovery / profile-scan tracking for stop handlers.
        self._discovery_stop_events: Dict[str, threading.Event] = {}
        self._profile_scan_stop_events: Dict[str, threading.Event] = {}
        self._discovery_thread: Optional[threading.Thread] = None

    # -- Device management ---------------------------------------------------

    def add_device(self, device: "Device") -> str:
        """Add a new Device to the Device Service and Core Metadata.

        A duplicate name is rejected against the cache. `device.service_name` is set to
        this service's name. The Device is validated (unless bypassed) and cached first,
        then written to Core Metadata. On metadata failure the cache entry is rolled back
        and the error is propagated. The returned id is the one assigned by Core Metadata.

        Raises:
            EdgexError: KindDuplicateName when a Device with the same name already exists.
        """
        _, exists = Devices().for_name(device.name)
        if exists:
            raise create_edgx_error(
                EdgexErrorKind.DUPLICATE_NAME,
                f"name conflicted, Device {device.name} exists")

        device.service_name = self.service_key
        self._logger.debug("Adding managed Device %s", device.name)
        return self._add_device_to_metadata(device, bypass_validation=False)

    def add_device_without_validation(self, device: "Device") -> str:
        """Add a new Device to the Device Service and Core Metadata with
        bypassValidation=true to skip device validation.

        The Device is cached first, then written to Core Metadata with validation
        bypassed. On metadata failure the cache entry is rolled back and the error is
        propagated.
        """
        _, exists = Devices().for_name(device.name)
        if exists:
            raise create_edgx_error(
                EdgexErrorKind.DUPLICATE_NAME,
                f"name conflicted, Device {device.name} exists")

        device.service_name = self.service_key
        self._logger.debug("Adding managed Device %s without validation", device.name)
        return self._add_device_to_metadata(device, bypass_validation=True)

    def devices(self) -> List["Device"]:
        """Return all managed Devices from cache.

        (returns `cache.Devices().All()`).
        """
        return Devices().all()

    def get_device_by_name(self, name: str) -> "Device":
        """Return the Device by its name if it exists in the cache.


        Raises:
            EdgexError: KindEntityDoesNotExist when the Device is not in the cache.
        """
        device, ok = Devices().for_name(name)
        if not ok:
            message = f"failed to find Device {name} in cache"
            self._logger.error(message)
            raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, message)
        return device

    def update_device(self, device: "Device") -> None:
        """Update the Device in Core Metadata and the local cache.

        The cache is updated first (with validation), then Core Metadata is patched.
        On metadata failure the cache is restored from a snapshot and the error is
        propagated.

        Raises:
            EdgexError: When the Device is not in the cache.
        """
        self.get_device_by_name(device.name)
        updates = {
            "description": device.description,
            "admin_state": device.admin_state,
            "operating_state": device.operating_state,
            "profile_name": device.profile_name,
            "labels": device.labels,
            "location": device.location,
            "auto_events": device.auto_events,
            "protocols": device.protocols,
            "tags": device.tags,
            "properties": device.properties,
        }
        self._patch_device_in_metadata(device.name, updates, bypass_validation=False)

    def update_device_without_validation(self, device: "Device") -> None:
        """Update the Device in Core Metadata and the local cache with
        bypassValidation=true to skip device validation.

        The cache is updated first (validation skipped), then Core Metadata is patched.
        On metadata failure the cache is restored from a snapshot and the error is
        propagated.
        """
        self.get_device_by_name(device.name)
        updates = {
            "description": device.description,
            "admin_state": device.admin_state,
            "operating_state": device.operating_state,
            "profile_name": device.profile_name,
            "labels": device.labels,
            "location": device.location,
            "auto_events": device.auto_events,
            "protocols": device.protocols,
            "tags": device.tags,
            "properties": device.properties,
        }
        self._patch_device_in_metadata(device.name, updates, bypass_validation=True)

    def patch_device(self, update_device: "UpdateDevice") -> None:
        """Patch the specified device properties in Core Metadata and the local cache.

        Device name is required in the UpdateDevice. All properties are optional; None
        values are ignored. The cache is updated first (with validation), then Core
        Metadata is patched. On metadata failure the cache is restored from a snapshot
        and the error is propagated.

        Raises:
            EdgexError: KindContractInvalid when the Device name is missing;
                KindEntityDoesNotExist when the Device is not in the cache.
        """
        self._patch_device_impl(update_device, bypass_validation=False)

    def patch_device_without_validation(self, update_device: "UpdateDevice") -> None:
        """Patch the specified device properties in Core Metadata and the local cache
        with bypassValidation=true to skip device validation.

        The cache is updated first (validation skipped), then Core Metadata is patched.
        On metadata failure the cache is restored from a snapshot and the error is
        propagated.
        """
        self._patch_device_impl(update_device, bypass_validation=True)

    def remove_device_by_name(self, name: str) -> None:
        """Remove the specified Device by name from the cache and ensure that the
        instance in Core Metadata is also removed.

        The
        cache copy is not removed here (as in Go).

        Raises:
            EdgexError: When the Device is not in the cache.
        """
        self.get_device_by_name(name)
        self._logger.debug("Removing managed Device %s", name)
        self._delete_device_from_metadata(name)

    def device_exists_for_name(self, name: str) -> bool:
        """Return True if a Device exists in cache with the specified name.

        (returns `_, ok :=
        cache.Devices().ForName(name)`).
        """
        _, ok = Devices().for_name(name)
        return ok

    def update_device_operating_state(self, name: str, state: str) -> None:
        """Update the OperatingState for the Device with the given name in Core Metadata.

        which patches the Device (with bypassValidation=true) with only the
        OperatingState.
        """
        self.get_device_by_name(name)
        self._patch_device_in_metadata(
            name, {"operating_state": str(state)}, bypass_validation=True)

    def _patch_device_impl(self, update_device: "UpdateDevice",
                           bypass_validation: bool) -> None:
        """Shared implementation of `patch_device` / `patch_device_without_validation`.

        The Device name is required, the Device must exist in the cache and only the
        non-None fields of the UpdateDevice are applied.
        """
        if isinstance(update_device, dict):
            name = update_device.get("name")
        else:
            name = getattr(update_device, "name", None)
        if name is None:
            message = "missing device name for patch device call"
            self._logger.error(message)
            raise create_edgx_error(KIND_CONTRACT_INVALID, message)

        self.get_device_by_name(name)

        if isinstance(update_device, dict):
            updates = {key: value for key, value in update_device.items()
                       if value is not None and key != "name"}
        else:
            updates = {key: value for key, value in vars(update_device).items()
                       if value is not None and key != "name"}

        if bypass_validation:
            self._logger.debug("Patching managed Device %s without validation", name)
        else:
            self._logger.debug("Patching managed Device %s", name)
        self._patch_device_in_metadata(name, updates, bypass_validation=bypass_validation)

    # -- Device Profile management --------------------------------------------

    def add_device_profile(self, profile: "DeviceProfile") -> str:
        """Add a new DeviceProfile to the Device Service and Core Metadata.

        In
        On success the Profile is also added to the profile cache.

        Returns:
            The new DeviceProfile id.

        Raises:
            EdgexError: KindDuplicateName when a Profile with the same name exists.
        """
        _, exists = Profiles().for_name(profile.name)
        if exists:
            raise create_edgx_error(
                EdgexErrorKind.DUPLICATE_NAME,
                f"name conflicted, Profile {profile.name} exists")

        self._logger.debug("Adding managed Profile %s", profile.name)
        return self._add_profile_to_metadata(profile)

    def device_profiles(self) -> List["DeviceProfile"]:
        """Return all managed DeviceProfiles from cache.

        (returns
        `cache.Profiles().All()`).
        """
        return Profiles().all()

    def get_profile_by_name(self, name: str) -> "DeviceProfile":
        """Return the Profile by its name if it exists in the cache.


        Raises:
            EdgexError: KindEntityDoesNotExist when the Profile is not in the cache.
        """
        profile, ok = Profiles().for_name(name)
        if not ok:
            message = f"failed to find Profile {name} in cache"
            self._logger.error(message)
            raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, message)
        return profile

    def update_device_profile(self, profile: "DeviceProfile") -> None:
        """Update the DeviceProfile in Core Metadata.

        In
The cache copy is not updated
        here (as in Go).

        Raises:
            EdgexError: When the Profile is not in the cache.
        """
        _, ok = Profiles().for_name(profile.name)
        if not ok:
            message = f"failed to find Profile {profile.name} in cache"
            self._logger.error(message)
            raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, message)

        self._logger.debug("Updating managed Profile %s", profile.name)
        self._update_profile_in_metadata(profile)

    def remove_device_profile_by_name(self, name: str) -> None:
        """Remove the specified DeviceProfile by name from the cache and ensure that the
        instance in Core Metadata is also removed.

        The cache entry is removed first, then Core Metadata is deleted. On metadata
        failure the cache entry is restored and the error is propagated.

        Raises:
            EdgexError: When the Profile is not in the cache.
        """
        profile, ok = Profiles().for_name(name)
        if not ok:
            message = f"failed to find Profile {name} in cache"
            self._logger.error(message)
            raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, message)

        self._logger.debug("Removing managed Profile %s", profile.name)
        self._delete_profile_from_metadata(name)

    # -- Provision Watcher management ------------------------------------------

    def add_provision_watcher(self, watcher: "ProvisionWatcher") -> str:
        """Add a new Watcher to the cache and Core Metadata.

        The cache entry is added first, then Core Metadata is written. On metadata
        failure the cache entry is rolled back and the error is propagated.

        Returns:
            The new Watcher id.

        Raises:
            EdgexError: KindDuplicateName when a Watcher with the same name exists.
        """
        _, exists = ProvisionWatchers().for_name(watcher.name)
        if exists:
            raise create_edgx_error(
                EdgexErrorKind.DUPLICATE_NAME,
                f"name conflicted, ProvisionWatcher {watcher.name} exists")

        # The ServiceName must be this service's (base) name since ProvisionWatchers are
        # used by all instances of the Device Service.
        watcher.service_name = self.service_key
        self._logger.debug("Adding managed ProvisionWatcher %s", watcher.name)
        return self._add_provision_watcher_to_metadata(watcher)

    def provision_watchers(self) -> List["ProvisionWatcher"]:
        """Return all managed Watchers from cache.

        (returns
        `cache.ProvisionWatchers().All()`).
        """
        return ProvisionWatchers().all()

    def get_provision_watcher_by_name(self, name: str) -> "ProvisionWatcher":
        """Return the Watcher by its name if it exists in the cache.


        Raises:
            EdgexError: KindEntityDoesNotExist when the Watcher is not in the cache.
        """
        watcher, ok = ProvisionWatchers().for_name(name)
        if not ok:
            message = f"failed to find ProvisionWatcher {name} in cache"
            self._logger.error(message)
            raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, message)
        return watcher

    def update_provision_watcher(self, watcher: "ProvisionWatcher") -> None:
        """Update the Watcher in Core Metadata and the local cache.

        The cache is updated first, then Core Metadata. On metadata failure the cache
        is restored from a snapshot and the error is propagated.

        Raises:
            EdgexError: When the Watcher is not in the cache.
        """
        _, ok = ProvisionWatchers().for_name(watcher.name)
        if not ok:
            message = f"failed to find ProvisionWatcher {watcher.name} in cache"
            self._logger.error(message)
            raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, message)

        self._logger.debug("Updating managed ProvisionWatcher: %s", watcher.name)
        self._update_provision_watcher_in_metadata(watcher)

    def remove_provision_watcher(self, name: str) -> None:
        """Remove the specified Watcher by name from the cache and ensure that the
        instance in Core Metadata is also removed.

        The cache entry is removed first, then Core Metadata is deleted. On metadata
        failure the cache entry is restored and the error is propagated.

        Raises:
            EdgexError: When the Watcher is not in the cache.
        """
        watcher, ok = ProvisionWatchers().for_name(name)
        if not ok:
            message = f"failed to find ProvisionWatcher {name} in cache"
            self._logger.error(message)
            raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, message)

        self._logger.debug("Removing managed ProvisionWatcher: %s", watcher.name)
        self._delete_provision_watcher_from_metadata(name)

    # -- Device resource / command lookup ---------------------------------------

    def device_resource(self, device_name: str,
                        device_resource: str) -> Tuple["DeviceResource", bool]:
        """Retrieve the specific DeviceResource instance from cache according to the
        Device name and Device Resource name.
        """
        device, ok = Devices().for_name(device_name)
        if not ok:
            self._logger.error("failed to find device %s in cache", device_name)
            return DeviceResource(), False

        device_resource_instance, ok = Profiles().device_resource(
            device.profile_name, device_resource)
        if not ok:
            return device_resource_instance, False
        return device_resource_instance, True

    def device_command(self, device_name: str,
                       command_name: str) -> Tuple["DeviceCommand", bool]:
        """Retrieve the specific DeviceCommand instance from cache according to the
        Device name and Command name.
        """
        device, ok = Devices().for_name(device_name)
        if not ok:
            self._logger.error("failed to find device %s in cache", device_name)
            return DeviceCommand(), False

        device_command_instance, ok = Profiles().device_command(
            device.profile_name, command_name)
        if not ok:
            return device_command_instance, False
        return device_command_instance, True

    # -- AutoEvents --------------------------------------------------------------

    def add_device_auto_event(self, device_name: str, event: AutoEvent) -> None:
        """Add a new AutoEvent to the Device with the given name.

        In
When an AutoEvent with the same source name already exists
        its interval / on_change are updated (as in Go, this update is not persisted to
        the cache); otherwise the event is appended and the Device cache entry is updated.
        The AutoEvent executor for the Device is restarted afterwards.

        Raises:
            EdgexError: When the Device is not in the cache.
        """
        device, ok = Devices().for_name(device_name)
        if not ok:
            message = f"failed to find device {device_name} in cache"
            self._logger.error(message)
            raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, message)

        found = False
        for auto_event in device.auto_events:
            if auto_event.source_name == event.source_name:
                self._logger.debug("Updating existing AutoEvent %s for device %s",
                                   auto_event.source_name, device_name)
                auto_event.interval = event.interval
                auto_event.on_change = event.on_change
                found = True
                break

        if not found:
            self._logger.debug("Adding new AutoEvent %s to device %s",
                               event.source_name, device_name)
            device.auto_events.append(event)
            Devices().update(device)

        self._restart_auto_events(device_name)

    def remove_device_auto_event(self, device_name: str, event: AutoEvent) -> None:
        """Remove an AutoEvent from the Device with the given name.

        In
The matching AutoEvent is removed from the Device's
        auto_events and the Device cache entry is updated; the AutoEvent executor for the
        Device is restarted afterwards.

        Raises:
            EdgexError: When the Device is not in the cache.
        """
        device, ok = Devices().for_name(device_name)
        if not ok:
            message = f"failed to find device {device_name} cannot in cache"
            self._logger.error(message)
            raise create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, message)

        for index, auto_event in enumerate(device.auto_events):
            if auto_event.source_name == event.source_name:
                self._logger.debug("Removing AutoEvent %s for device %s",
                                   auto_event.source_name, device_name)
                del device.auto_events[index]
                break

        Devices().update(device)
        self._restart_auto_events(device_name)

    # -- Lifecycle / runtime ------------------------------------------------------

    def initialize_resources(self, res_root: Optional[str] = None,
                             profile_names: Optional[List[str]] = None,
                             device_names: Optional[List[str]] = None,
                             watcher_names: Optional[List[str]] = None) -> Dict[str, int]:
        """Load the pre-defined DeviceProfiles, Devices and ProvisionWatchers shipped under
        ``res/profiles``, ``res/devices`` and ``res/provisionwatchers`` and register them
        with the internal caches.

        ``provision.LoadDevices`` / ``provision.LoadProvisionWatchers`` followed by
        ``processProfiles`` / ``processDevices`` / ``processWatchers`` which populate the
        internal caches (``cache.Profiles()`` / ``cache.Devices()`` /
``cache.ProvisionWatchers()``). The Core Metadata side of each step is a placeholder
        until the app-functions-sdk-python metadata clients are ported; the cache is seeded
        directly so the controller serves the loaded entities without a round-trip to Core
Metadata (, where Core Metadata already holds the entities at
        runtime).

        Each ``*_names`` argument, when provided, restricts the loaded set to the listed
        file basenames (without extension), letting a service that ships a shared ``res``
tree select a subset. A missing ``res`` directory is not an error - a warning is
        logged and no entities are loaded (matches the Go behaviour where an empty/missing
        resources path yields zero profiles and devices).

        Args:
            res_root: Root directory of the ``res`` tree (default: ``res`` subdirectory of
                the current working directory, overridable via ``configuration.paths``).
            profile_names, device_names, watcher_names: Optional basename allow-lists used
                to load a subset of the resource files.

        Returns:
            A dict with the keys ``profiles``, ``devices`` and ``watchers`` reporting how
            many entities were loaded and registered.
        """
        res_root = self._resolve_res_root(res_root)
        self._logger.debug("Loading pre-defined resources from %s", res_root)

        counts = {"profiles": 0, "devices": 0, "watchers": 0}
        if not os.path.isdir(res_root):
            self._logger.debug("Resources directory %s does not exist; skipping "
                               "pre-defined profile/device/watcher loading", res_root)
            return counts

        # Profiles first - devices reference them by name.
        loaded_profiles = list(load_profiles(
            os.path.join(res_root, "profiles"), self._logger))
        if profile_names is not None:
            loaded_profiles = [p for p in loaded_profiles
                               if p.name in profile_names]
        for profile in loaded_profiles:
            if Profiles().for_name(profile.name)[1]:
                self._logger.debug("Profile %s already present in cache; not re-adding",
                                   profile.name)
                continue
            Profiles().add(profile.clone())
            counts["profiles"] += 1

        loaded_devices = list(load_devices(
            os.path.join(res_root, "devices"), self._logger))
        if device_names is not None:
            loaded_devices = [d for d in loaded_devices if d.name in device_names]
        for device in loaded_devices:
            # Mirror Go processDevices: default the OperatingState to Enabled ("UP") and
            # attach the device to this service.
            if not device.operating_state:
                device.operating_state = OPERATING_STATE_UP
            if not device.service_name:
                device.service_name = self.service_key
            if Devices().device_exists(device.name):
                self._logger.debug("Device %s already present in cache; not re-adding",
                                   device.name)
                continue
            Devices().add(device.clone())
            counts["devices"] += 1

        loaded_watchers = list(load_provision_watchers(
            os.path.join(res_root, "provisionwatchers"), self._logger))
        if watcher_names is not None:
            loaded_watchers = [w for w in loaded_watchers
                               if w.name in watcher_names]
        for watcher in loaded_watchers:
            if not watcher.service_name:
                watcher.service_name = self.service_key
            if ProvisionWatchers().for_name(watcher.name)[1]:
                self._logger.debug("ProvisionWatcher %s already present in cache; not "
                                   "re-adding", watcher.name)
                continue
            ProvisionWatchers().add(watcher.clone())
            counts["watchers"] += 1

        self._logger.info(
            "Pre-defined resources loaded: %d profile(s), %d device(s), %d watcher(s)",
            counts["profiles"], counts["devices"], counts["watchers"])

        self._register_resources_to_metadata(
            loaded_profiles, loaded_devices, loaded_watchers)
        return counts

    def _resolve_res_root(self, res_root: Optional[str]) -> str:
        """Resolve the root ``res`` directory.

        Resolution order: explicit ``res_root`` argument; ``configuration.paths`` (read
        defensively as ``res_root`` / ``res``); finally the ``res`` directory of the
        current working directory.
        """
        if res_root:
            return os.path.abspath(res_root)
        paths = getattr(self.configuration, "paths", None)
        configured = None
        if paths is not None:
            configured = getattr(paths, "res_root", None) or getattr(paths, "res", None)
        if configured:
            return os.path.abspath(configured)
        return os.path.join(os.getcwd(), "res")

    # -- Core Metadata registration --------------------------------------------

    def _metadata_base_url(self) -> Optional[str]:
        """Resolve the Core Metadata base URL from the configuration.

        BootstrapContainer.DeviceServiceClientFrom(dic.Get)``: the Device Service
talks to Core Metadata to self-register and provision its resources. The base URL is
        read defensively from ``configuration.clients`` (an EdgeX-style ``clients`` map) with
        the ``core-metadata`` key, falling back to the ``EDGEX_CORE_METADATA_HOST`` /
``EDGEX_CORE_METADATA_PORT`` environment variables, then the localhost default. When no
        clients are configured the registration step is skipped entirely (the service still
        starts with its local caches).
        """
        clients = getattr(self.configuration, "clients", None)
        core_metadata = None
        if isinstance(clients, dict):
            core_metadata = clients.get("core-metadata") or clients.get("core_metadata")
        elif clients is not None:
            core_metadata = getattr(clients, "core_metadata", None) \
                or getattr(clients, "core-metadata", None)

        if core_metadata is not None:
            if isinstance(core_metadata, dict):
                host = core_metadata.get("host", "")
                port = core_metadata.get("port", "")
                base_url = core_metadata.get("base_url", "")
            else:
                host = getattr(core_metadata, "host", "")
                port = getattr(core_metadata, "port", "")
                base_url = getattr(core_metadata, "base_url", "")
            if base_url:
                return base_url
            if host and port:
                return f"http://{host}:{port}"

        host = os.environ.get("EDGEX_CORE_METADATA_HOST")
        port = os.environ.get("EDGEX_CORE_METADATA_PORT")
        if host and port:
            return f"http://{host}:{port}"
        return None

    def _metadata_client(self) -> Optional[MetadataClient]:
        """Lazily create the Core Metadata client from the configured base URL."""
        base_url = self._metadata_base_url()
        if not base_url:
            return None
        if self._metadata_client_instance is None:
            self._metadata_client_instance = MetadataClient(
                base_url=base_url, logger=self._logger)
            self._logger.debug("Core Metadata client configured at %s", base_url)
        return self._metadata_client_instance

    def _run_metadata(self, fn: Callable[[], Any]) -> Any:
        """Run a Core Metadata write-back call and wait for its result.

        The write runs on the bounded `_metadata_executor` so the HTTP I/O happens off the
        caller's thread, but the result (and any `MetadataError`) is surfaced synchronously
        so the caller can roll its local caches back on failure. When Core Metadata is not
        configured the operation is skipped and ``None`` returned.
        """
        client = self._metadata_client()
        if client is None:
            return None
        if self._metadata_executor is None:
            self._metadata_executor = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="metadata")
        return self._metadata_executor.submit(fn).result()

    def _validate_device(self, device: Device, bypass_validation: bool) -> None:
        """Run the ProtocolDriver's in-process device validation when required.

        Mirrors the Core Metadata validation round-trip (which would otherwise block on
        the message-bus subscription) with a direct ``driver.validate_device`` call. Any
        exception from the driver aborts the operation before the cache / metadata write.
        """
        if bypass_validation:
            self._logger.debug("Skipping device validation for %s (bypassValidation)",
                               device.name)
            return
        self._logger.debug("Validating Device %s", device.name)
        validate_device = getattr(self.driver, "validate_device", None)
        if validate_device is None:
            return
        try:
            validate_device(device)
        except Exception as exc: # pylint: disable=broad-except
            message = f"device validation failed for {device.name}: {exc}"
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc

    def _register_resources_to_metadata(self, profiles: List[Any],
                                        devices: List[Any],
                                        watchers: List[Any]) -> None:
        """Register the Device Service and its pre-defined resources with Core Metadata.

        Self-registration happens first, then profiles / devices / provision watchers are
        loaded from the ``res`` tree. Each step is best-effort - a missing / down Core
        Metadata (or a rejected entity) is logged and does not stop the Device Service
        from starting with its local caches.

        Args:
            profiles: The DeviceProfile models loaded from ``res/profiles``.
            devices: The Device models loaded from ``res/devices``.
            watchers: The ProvisionWatcher models loaded from ``res/provisionwatchers``.
        """
        client = self._metadata_client()
        if client is None:
            self._logger.debug(
                "Core Metadata not configured (no clients section); resources are only "
                "kept in the local caches")
            return

        self._register_device_service(client)
        self._add_missing_profiles(client, profiles)
        self._add_missing_devices(client, devices)
        self._add_missing_watchers(client, watchers)

    def _register_device_service(self, client: MetadataClient) -> None:
        """Self-register the Device Service onto Core Metadata.

        Creates the DeviceService when it does not exist yet; otherwise updates its
        ``baseAddress`` from the local configuration. The ``baseAddress`` is the
        URL other EdgeX services (notably core-command) use to reach this Device Service, so it
        must be reachable from them - never ``0.0.0.0`` (a bind address). See
        :meth:`_advertised_host`.
        """
        name = self.service_key
        port = self._http_host_port()[1]
        base_address = "http://{host}:{port}".format(host=self._advertised_host(), port=port)
        labels = self._device_labels()
        service = {
            "name": name,
            "baseAddress": base_address,
            "adminState": str(self.device_service_model.admin_state),
            "labels": labels,
            "properties": {},
        }
        try:
            existing = client.device_service_by_name(name)
        except MetadataError as exc:
            self._logger.warning("failed to check DeviceService %s on Core Metadata: %s",
                                 name, exc)
            return
        try:
            if existing is None:
                client.add_device_service(service)
                self._logger.info("Registered DeviceService %s on Core Metadata at %s",
                                  name, base_address)
            else:
                client.update_device_service(service)
                self._logger.info("Updated DeviceService %s on Core Metadata", name)
        except MetadataError as exc:
            self._logger.error("failed to register DeviceService %s on Core Metadata: %s",
                               name, exc)

    def _advertised_host(self) -> str:
        """Return the host to advertise in the DeviceService ``baseAddress``.

        Resolution order: ``configuration.service.base_address`` (a full URL) or
        ``configuration.service.advertised_host``; the ``EDGEX_SERVICE_ADDRESS`` /
        ``EDGEX_SERVICE_HOST`` environment variables; the configured bind ``host`` when it is a
        concrete address (not ``0.0.0.0`` / ``::`` / empty); otherwise the machine's primary
hostname IP. Extended Core services (core-command) reach this address to execute
        commands, so it must differ from the ``0.0.0.0`` bind host.
        """
        service = getattr(self.configuration, "service", None)
        if service is not None:
            base = getattr(service, "base_address", None) or getattr(service, "advertised_host", None)
            if base:
                return base
        for env_name in ("EDGEX_SERVICE_ADDRESS", "EDGEX_SERVICE_HOST"):
            host = os.environ.get(env_name)
            if host:
                return host
        if service is not None:
            host = getattr(service, "host", None)
            if host and host not in ("0.0.0.0", "::", "0.0.0.0:0"):
                return host
# Fall back to the primary non-loopback IP of the machine's outbound route. This is
        # typically the LAN address, which containerised EdgeX core services (core-command)
        # can reach to execute commands against this Device Service.
        try:
            import socket
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            probe.close()
            if address and not address.startswith("127."):
                return address
        except Exception:  # noqa: BLE001
            pass
        return "localhost"

    def _device_labels(self) -> List[str]:
        """Return the Device labels from the configuration."""
        device = getattr(self.configuration, "device", None)
        labels = getattr(device, "labels", None) if device is not None else None
        return list(labels) if labels else []

    def _add_missing_profiles(self, client: MetadataClient, profiles: List[Any]) -> None:
        """Add the pre-defined DeviceProfiles that are missing from Core Metadata."""
        for profile in profiles:
            try:
                if client.device_profile_by_name(profile.name) is not None:
                    self._logger.debug("DeviceProfile %s already registered in Core Metadata",
                                       profile.name)
                    continue
                client.add_device_profiles([profile])
                self._logger.info("Registered DeviceProfile %s on Core Metadata",
                                  profile.name)
            except MetadataError as exc:
                self._logger.error("failed to register DeviceProfile %s on Core Metadata: %s",
                                   profile.name, exc)

    def _add_missing_devices(self, client: MetadataClient, devices: List[Any]) -> None:
        """Add the pre-defined Devices that are missing from Core Metadata."""
        for device in devices:
            try:
                if client.device_by_name(device.name) is not None:
                    self._logger.debug("Device %s already registered in Core Metadata",
                                       device.name)
                    continue
                client.add_devices([device])
                self._logger.info("Registered Device %s on Core Metadata", device.name)
            except MetadataError as exc:
                self._logger.error("failed to register Device %s on Core Metadata: %s",
                                   device.name, exc)

    def _add_missing_watchers(self, client: MetadataClient, watchers: List[Any]) -> None:
        """Add the pre-defined ProvisionWatchers that are missing from Core Metadata."""
        for watcher in watchers:
            try:
                if client.provision_watcher_by_name(watcher.name) is not None:
                    self._logger.debug(
                        "ProvisionWatcher %s already registered in Core Metadata", watcher.name)
                    continue
                client.add_provision_watchers([watcher])
                self._logger.info("Registered ProvisionWatcher %s on Core Metadata",
                                  watcher.name)
            except MetadataError as exc:
                self._logger.error(
                    "failed to register ProvisionWatcher %s on Core Metadata: %s",
                    watcher.name, exc)

    # -- Message-bus device validation -------------------------------------------

    def _message_bus_config(self) -> MessageBusConfig:
        """Return the complete ``MessageBusConfig`` parsed from configuration (EdgeX
        ``[MessageBus]`` section) or environment variables, with sensible defaults.
        """
        mq = getattr(self.configuration, "message_bus", None)
        broker_host = None
        broker_port = None
        base_topic_prefix = None
        message_bus_type = None
        publish_topic_prefix = None
        optional: Dict[str, str] = {}
        auth_mode = AUTH_MODE_NONE
        if mq is not None:
            if isinstance(mq, dict):
                broker_host = mq.get("host")
                broker_port = mq.get("port")
                base_topic_prefix = mq.get("base_topic_prefix")
                message_bus_type = mq.get("type")
                publish_topic_prefix = mq.get("publish_topic_prefix")
                opt = mq.get("optional") or {}
                optional = {k: str(v) for k, v in opt.items()}
                auth_mode = mq.get("auth_mode", AUTH_MODE_NONE)
            else:
                broker_host = getattr(mq, "host", None)
                broker_port = getattr(mq, "port", None)
                base_topic_prefix = getattr(mq, "base_topic_prefix", None)
                message_bus_type = getattr(mq, "type", None)
                publish_topic_prefix = getattr(mq, "publish_topic_prefix", None)
                opt = getattr(mq, "optional", None) or {}
                optional = {k: str(v) for k, v in opt.items()}
                auth_mode = getattr(mq, "auth_mode", AUTH_MODE_NONE)
        broker_host = broker_host or os.environ.get("EDGEX_MESSAGEBUS_HOST") or "127.0.0.1"
        broker_port = int(broker_port or os.environ.get("EDGEX_MESSAGEBUS_PORT") or 1883)
        base_topic_prefix = (base_topic_prefix
                             or os.environ.get("EDGEX_MESSAGEBUS_TOPIC")
                             or "edgex")
        message_bus_type = message_bus_type or DEFAULT_MESSAGEBUS_TYPE
        publish_topic_prefix = publish_topic_prefix or "events"
        return MessageBusConfig(
            broker_info=HostInfo(protocol="tcp", host=broker_host, port=broker_port),
            message_bus_type=message_bus_type,
            auth_mode=auth_mode,
            optional=optional,
            base_topic_prefix=base_topic_prefix,
            publish_topic_prefix=publish_topic_prefix,
        )

    def _init_messaging_client(self) -> None:
        """Initialize the messaging client and wire the send_event handler."""
        if self._messaging_client is not None:
            return
        cfg = self._message_bus_config()
        self._message_bus_config_obj = cfg
        self._logger.debug("Initializing messaging client: %s://%s:%s",
                           cfg.broker_info.protocol, cfg.broker_info.host, cfg.broker_info.port)
        self._messaging_client = create_message_client(cfg)
        try:
            self._messaging_client.connect()
        except Exception as exc: # pylint: disable=broad-except
            self._logger.warning("Failed to connect to message bus: %s; event publishing will be unavailable",
                                 exc)
            self._messaging_client = None
            return
        # Wire the real send_event handler (replaces the no-op logging handler)
        self._send_event_handler = self._make_send_event_handler()

    def _make_send_event_handler(self) -> Callable[[Event, str], None]:
        """Return a handler that publishes an Event via the messaging client."""
        def handler(event: Event, correlation_id: str) -> None:
            if self._messaging_client is None:
                return
            try:
                publish_event(
                    client=self._messaging_client,
                    event=event,
                    correlation_id=correlation_id,
                    base_topic_prefix=self._message_bus_config_obj.base_topic_prefix,
                    service_name=self.name(),
                    profile_name=event.profile_name,
                    device_name=event.device_name,
                    source_name=event.source_name,
                    max_event_size=DEFAULT_MAX_EVENT_SIZE,
                    logger=self._logger,
                )
            except Exception as exc: # pylint: disable=broad-except
                self._logger.error("Failed to publish event to message bus: %s", exc)
        return handler

    def _start_device_validation_handler(self) -> None:
        """Subscribe to Core Metadata's device-validation topic so device create / update calls
do not time out.

        Runs on a background thread; a missing / down broker is logged and does not stop the
service. Exposes nothing until ``run()`` so unit tests stay free of the network.
        """
        if getattr(self, "_validation_handler", None) is not None:
            return
        try:
            from ..internal.controller.messaging.validation import subscribe_device_validation
        except ImportError as exc:  # pragma: no cover - paho-mqtt absent
            self._logger.debug("device validation handler is not available: %s", exc)
            return
        if self._message_bus_config_obj is None:
            self._init_messaging_client()
        cfg = self._message_bus_config_obj
        if cfg is None or self._messaging_client is None:
            self._logger.debug("messaging client not available; skipping validation handler")
            return
        self._logger.debug("Starting device validation handler on topic base %s",
                           cfg.base_topic_prefix)
        self._validation_handler = subscribe_device_validation(
            service_name=self.name(), driver=self.driver,
            base_topic_prefix=cfg.base_topic_prefix,
            broker_host=cfg.broker_info.host, broker_port=cfg.broker_info.port, logger=self._logger)

    def run(self) -> None:
        """Start this Device Service. This should not be called directly by a device
        service; instead call the bootstrap entry point.

        Starts the AutoEvent manager, initializes and
        registers the REST controller, starts the ProtocolDriver and finally serves the
FastAPI application with uvicorn (blocking). The HTTP serving depends on uvicorn;
        when it is not available the driver / AutoEvents are still started and a warning
        is logged.
        """
        # Initialize messaging client first (needed for send_event, validation, commands, system events)
        self._init_messaging_client()

        self._start_auto_events()
        self._init_http_controller()
        self._start_device_validation_handler()

        # Start async pumps (consume async_values_channel and discovered_device_channel)
        self._start_async_pumps()

        # Start command and system-events subscriptions (if messaging client available)
        self._start_command_subscription()
        self._start_system_events_subscription()

        if self.driver is not None:
            self.driver.start()

        self._logger.info("Device Service %s (v%s) started",
                          self.name(), self.version())

        host, port = self._http_host_port()
        try:
            import uvicorn
        except ImportError:
            self._logger.warning(
                "uvicorn is not installed; the HTTP server will not be served. "
                "Install it to expose the REST API on %s:%s.", host, port)
            return
        try:
            uvicorn.run(self.controller.app(), host=host, port=port, log_level="info")
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        """Signal background pumps/subscriptions to stop and cleanup."""
        self._shutdown_event.set()
        for t in (self._async_pump_thread, self._discovered_pump_thread,
                  self._command_sub_thread, self._system_events_thread):
            if t is not None and t.is_alive():
                t.join(timeout=2.0)
        if self._metadata_executor is not None:
            self._metadata_executor.shutdown(wait=False, cancel_futures=True)
            self._metadata_executor = None
        if self._messaging_client is not None:
            try:
                self._messaging_client.disconnect()
            except Exception as exc: # pylint: disable=broad-except
                self._logger.debug("Error disconnecting messaging client: %s", exc)
        self._logger.info("Device Service %s shutdown complete", self.name())

    # -- Async pumps (mirrors Go processAsyncResults / processAsyncFilterAndAdd) ---------

    def _start_async_pumps(self) -> None:
        """Start background threads to consume async values and discovered devices channels.

(discovered devices). The pumps run until `_shutdown_event` is set.
        """
        if self._messaging_client is None or self._message_bus_config_obj is None:
            self._logger.debug("messaging client not available; async pumps disabled")
            return

        cfg = self._message_bus_config_obj

        def async_pump() -> None:
            """Consume AsyncValues from channel, transform to Event, publish to message bus."""
            self._logger.debug("Async values pump started")
            while not self._shutdown_event.is_set():
                try:
                    acv = self._async_values_channel.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self._process_async_values(acv, cfg)
                except Exception as exc: # pylint: disable=broad-except
                    self._logger.error("Async pump error: %s", exc)
            self._logger.debug("Async values pump stopped")

        def discovered_pump() -> None:
            """Consume discovered devices, match against ProvisionWatchers, register via metadata."""
            self._logger.debug("Discovered devices pump started")
            while not self._shutdown_event.is_set():
                try:
                    devices = self._discovered_device_channel.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self._process_discovered_devices(devices)
                except Exception as exc: # pylint: disable=broad-except
                    self._logger.error("Discovered pump error: %s", exc)
            self._logger.debug("Discovered devices pump stopped")

        self._async_pump_thread = threading.Thread(target=async_pump, daemon=True, name="async-pump")
        self._discovered_pump_thread = threading.Thread(target=discovered_pump, daemon=True, name="discovered-pump")
        self._async_pump_thread.start()
        self._discovered_pump_thread.start()
        self._logger.info("Async pumps started")

    def _process_async_values(self, acv: AsyncValues, cfg: MessageBusConfig) -> None:
        """Transform AsyncValues to Event and publish (mirrors Go sendAsyncValues)."""
        if not acv.command_values:
            self._logger.warning("Skip sending AsyncValues: CommandValues is empty")
            return
        if len(acv.command_values) > 1 and not acv.source_name:
            self._logger.warning("Skip sending AsyncValues: SourceName is empty for multi-reading")
            return
        # Use first reading's resource name as source if single reading and source_name empty
        if len(acv.command_values) == 1 and not acv.source_name:
            acv.source_name = acv.command_values[0].device_resource_name

        # Update lastConnected
        Devices().set_last_connected_by_name(acv.device_name)

        # Transform to Event
        data_transform = True
        device_opt = getattr(self.configuration, "device", None)
        if device_opt is not None:
            data_transform = getattr(device_opt, "data_transform", True)

        from ..transformer.transform import command_values_to_event
        try:
            event = command_values_to_event(
                acv.command_values, acv.device_name, acv.source_name, data_transform
            )
        except Exception as exc:
            self._logger.error("Failed to transform AsyncValues to Event: %s", exc)
            return

        if event is None:
            return

        # Publish via send_event handler (which uses messaging client)
        if self._send_event_handler is not None:
            self._send_event_handler(event, "")

    def _process_discovered_devices(self, devices: List[DiscoveredDevice]) -> None:
        """Match discovered devices against ProvisionWatchers and register via Core Metadata.

        For each discovered device that matches an unlocked ProvisionWatcher, a Device
        is created and registered with Core Metadata using bypassValidation=true (the
        Device Service's validation subscription is not yet ported). The local cache is
        updated first, then the metadata write is propagated; on failure the cache is
        rolled back.
        """
        watchers = ProvisionWatchers().all()
        if not watchers:
            return
        for d in devices:
            for pw in watchers:
                if getattr(pw, "admin_state", "") == "LOCKED":
                    continue
                if self._match_provision_watcher(d, pw):
                    if Devices().for_name(d.name)[1]:
                        self._logger.debug("Candidate discovered device %s already existed", d.name)
                        break
                    self._logger.info("Adding discovered device %s to Metadata", d.name)
                    # Create Device model from DiscoveredDevice + ProvisionWatcher
                    from ..internal.cache import Device
                    device = Device(
                        name=d.name,
                        description=d.description,
                        profile_name=pw.discovered_device.profile_name,
                        protocols=d.protocols,
                        labels=d.labels,
                        service_name=self.service_key,
                        admin_state=pw.discovered_device.admin_state,
                        operating_state="UP",
                        auto_events=pw.discovered_device.auto_events,
                        properties=pw.discovered_device.properties,
                    )
                    # Register with Core Metadata using bypassValidation=true
                    try:
                        self.add_device_without_validation(device)
                    except Exception as exc:  # pylint: disable=broad-except
                        self._logger.error("Failed to add discovered device %s: %s", d.name, exc)
                        break
                    # Publish system event for discovery progress
                    self._publish_discovery_progress(100, 1, f"Discovered device {d.name} added")
                    break

    def _match_provision_watcher(self, device: DiscoveredDevice, pw: Any) -> bool:
        """Check if discovered device matches ProvisionWatcher allow/block lists.

        """
        identifiers = getattr(pw, "identifiers", {})
        blocking = getattr(pw, "blocking_identifiers", {})
        if not identifiers:
            return False
        for protocol_name, protocol_props in device.protocols.items():
            matched = 0
            for name, regex in identifiers.items():
                value = protocol_props.get(name)
                if value is None:
                    continue
                value_str = str(value)
                if not value_str:
                    continue
                import re
                if re.match(regex, value_str):
                    matched += 1
            if matched == len(identifiers):
                # Check block list
                blocked = False
                for name, block_list in blocking.items():
                    value = protocol_props.get(name)
                    if value is None:
                        continue
                    value_str = str(value)
                    if value_str in block_list:
                        blocked = True
                        break
                if not blocked:
                    return True
        return False

    def _publish_discovery_progress(self, progress: int, count: int, message: str) -> None:
        """Publish device discovery progress system event."""
        if self._messaging_client is None or self._message_bus_config_obj is None:
            return
        try:
            publish_system_event(
                client=self._messaging_client,
                service_name=self.name(),
                event_type=DEVICE_SYSTEM_EVENT_TYPE,
                action=SYSTEM_EVENT_ACTION_PROGRESS,
                details={"progress": progress, "discoveredDeviceCount": count, "message": message},
                base_topic_prefix=self._message_bus_config_obj.base_topic_prefix,
                logger=self._logger,
            )
        except Exception as exc: # pylint: disable=broad-except
            self._logger.error("Failed to publish discovery progress: %s", exc)

    def _publish_profile_scan_progress(self, request_id: str, progress: int, message: str) -> None:
        """Publish profile scan progress system event."""
        if self._messaging_client is None or self._message_bus_config_obj is None:
            return
        try:
            publish_system_event(
                client=self._messaging_client,
                service_name=self.name(),
event_type=DEVICE_SYSTEM_EVENT_TYPE, # profile-scan uses device type in Go
                action=SYSTEM_EVENT_ACTION_PROGRESS,
                details={"requestId": request_id, "progress": progress, "message": message},
                base_topic_prefix=self._message_bus_config_obj.base_topic_prefix,
                logger=self._logger,
            )
        except Exception as exc: # pylint: disable=broad-except
            self._logger.error("Failed to publish profile scan progress: %s", exc)

    # -- Discovery / Profile Scan stop handlers ----------------------------------------

    def _device_discovery_stop_handler(self, request_id: str, options: Dict[str, Any]) -> None:
        """Stop a running device discovery by request_id.

        Sets the stop event for the discovery, signaling the driver's discover()
        to terminate early if it checks the event. The discovery progress is
        published as -1% (error) when stopped.
        """
        self._logger.info("Stopping device discovery %s", request_id)
        event = self._discovery_stop_events.pop(request_id, None)
        if event is not None:
            event.set()
            self._publish_discovery_progress(-1, 0, f"Discovery {request_id} stopped")
        if self._discovery_thread is not None:
            self._discovery_thread.join(timeout=2.0)
            self._discovery_thread = None

    def _profile_scan_handler(self, device_name: str, profile_name: str,
                              request_id: str, options: Dict[str, Any]) -> None:
        """Trigger a profile scan for the given device.

        Creates a DeviceProfile by inspecting the device's protocol properties
        and registers it with Core Metadata. The scan runs in a background thread
        and publishes progress events (0% start, 100% complete, -1% error).
        """
        self._logger.info("Starting profile scan for device %s -> profile %s (request %s)",
                          device_name, profile_name, request_id)

        # Signal stop event for this scan
        stop_event = threading.Event()
        self._profile_scan_stop_events[request_id] = stop_event

        def run_scan():
            try:
                # Check for early stop
                if stop_event.is_set():
                    self._logger.info("Profile scan %s stopped before start", request_id)
                    return

                device, ok = Devices().for_name(device_name)
                if not ok:
                    self._logger.error("Device %s not found for profile scan", device_name)
                    self._publish_profile_scan_progress(request_id, -1, f"Device {device_name} not found")
                    return

                # Build a basic DeviceProfile from the device's protocols
                from ..internal.cache import DeviceProfile, DeviceResource, ResourceProperties
                profile = DeviceProfile(
                    name=profile_name,
                    description=f"Auto-generated profile for {device_name}",
                    device_resources=[],
                    device_commands=[],
                )
                # For each protocol, add a device resource
                for proto_name, proto_props in device.protocols.items():
                    for prop_name, prop_value in proto_props.items():
                        profile.device_resources.append(DeviceResource(
                            name=prop_name,
                            description=f"Auto-discovered from {proto_name}",
                            properties=ResourceProperties(value_type="String"),
                        ))

                # Register with Core Metadata (cache-first + rollback)
                self.add_device_profile(profile)
                self._logger.info("Profile scan %s completed for %s", request_id, device_name)
                self._publish_profile_scan_progress(request_id, 100, "Profile scan completed")

            except Exception as exc: # pylint: disable=broad-except
                self._logger.exception("Profile scan %s failed: %s", request_id, exc)
                self._publish_profile_scan_progress(request_id, -1, f"Profile scan failed: {exc}")
            finally:
                self._profile_scan_stop_events.pop(request_id, None)

        thread = threading.Thread(target=run_scan, daemon=True, name=f"profile-scan-{request_id}")
        thread.start()

    def _profile_scan_stop_handler(self, device_name: str, options: Dict[str, Any]) -> None:
        """Stop a running profile scan for the given device.

        Finds the scan by device_name (or request_id in options) and signals it to stop.
        """
        self._logger.info("Stopping profile scan for device %s", device_name)
        # Look for scan by device_name in options or by any active scan for this device
        request_id = options.get("requestId", [""])[0] if isinstance(options.get("requestId"), list) else options.get("requestId", "")
        if request_id:
            event = self._profile_scan_stop_events.pop(request_id, None)
            if event:
                event.set()
                self._publish_profile_scan_progress(request_id, -1, f"Profile scan for {device_name} stopped")
                return
        # Fallback: stop all scans for this device (simplified)
        for rid, event in list(self._profile_scan_stop_events.items()):
            event.set()
            self._publish_profile_scan_progress(rid, -1, f"Profile scan for {device_name} stopped")

    # -- Command subscription (mirrors Go messaging.SubscribeCommands) --------------------

    def _start_command_subscription(self) -> None:
        """Subscribe to command requests on the message bus (EdgeX v4).

        Topic: `<basePrefix>/command/request/<serviceName>/#`
        Response: `<basePrefix>/response/<serviceName>/<requestId>`
        Concurrency limited to MaxConcurrentCommands (default 32).
        """
        if self._messaging_client is None or self._message_bus_config_obj is None:
            self._logger.debug("messaging client not available; command subscription disabled")
            return

        cfg = self._message_bus_config_obj
        max_concurrent = 32
        device_opt = getattr(self.configuration, "device", None)
        if device_opt is not None:
            max_concurrent = getattr(device_opt, "max_concurrent_commands", 32) or 32

        from ..internal.controller.messaging.command import subscribe_commands
        self._command_sub_thread = subscribe_commands(
            ctx_cancel=self._shutdown_event,
            client=self._messaging_client,
            base_topic_prefix=cfg.base_topic_prefix,
            service_name=self.name(),
            driver=self.driver,
            configuration=self.configuration,
            device_service=self,
            logger=self._logger,
            max_concurrent=max_concurrent,
        )
        self._logger.info("Command subscription started (max concurrent: %d)", max_concurrent)

    def _start_system_events_subscription(self) -> None:
        """Subscribe to Metadata system events (device/profile/watcher/service).

        Topics:
        - `<basePrefix>/system-events/<serviceName>/#`
        - `<basePrefix>/system-events/device-profile/delete/#`
        - Instance name: `<basePrefix>/system-events/provision-watcher/<baseServiceName>/#`
        """
        if self._messaging_client is None or self._message_bus_config_obj is None:
            self._logger.debug("messaging client not available; system events subscription disabled")
            return

        cfg = self._message_bus_config_obj
        base_service_name = _get_base_service_name(self.name())

        from ..internal.controller.messaging.callback import subscribe_system_events
        self._system_events_thread = subscribe_system_events(
            ctx_cancel=self._shutdown_event,
            client=self._messaging_client,
            base_topic_prefix=cfg.base_topic_prefix,
            service_name=self.name(),
            base_service_name=base_service_name,
            add_device=self._on_device_added,
            update_device=self._on_device_updated,
            delete_device=self._on_device_deleted,
            add_profile=self._on_profile_added,
            update_profile=self._on_profile_updated,
            delete_profile=self._on_profile_deleted,
            add_watcher=self._on_watcher_added,
            update_watcher=self._on_watcher_updated,
            delete_watcher=self._on_watcher_deleted,
            update_service=self._on_service_updated,
            logger=self._logger,
        )
        self._logger.info("System events subscription started (base service: %s)", base_service_name)

    # -- System event callbacks (wired to MetadataSystemEventsCallback) ----------------

    def _on_device_added(self, details: Dict[str, Any]) -> None:
        """Handle Device add system event."""
        from ..internal.cache import Device
        device = Device(
            name=details.get("name", ""),
            description=details.get("description", ""),
            profile_name=details.get("profileName", ""),
            protocols=details.get("protocols", {}),
            labels=details.get("labels", []),
            service_name=self.service_key,
            admin_state=details.get("adminState", "UNLOCKED"),
            operating_state=details.get("operatingState", "UP"),
            auto_events=details.get("autoEvents", []),
            properties=details.get("properties", {}),
        )
        if device.id:
            device.id = details.get("id", "")
        Devices().add(device)
        self._logger.info("Device %s added via system event", device.name)

    def _on_device_updated(self, name: str, updates: Dict[str, Any]) -> None:
        """Handle Device update system event."""
        # Use existing patch logic
        from ..internal.cache import UpdateDevice
        update = UpdateDevice(name=name)
        for k, v in updates.items():
            if hasattr(update, k):
                setattr(update, k, v)
        self._patch_device_impl(update, bypass_validation=True)
        self._logger.info("Device %s updated via system event", name)

    def _on_device_deleted(self, name: str) -> None:
        """Handle Device delete system event."""
        self.remove_device_by_name(name)
        self._logger.info("Device %s deleted via system event", name)

    def _on_profile_added(self, details: Dict[str, Any]) -> None:
        """Handle DeviceProfile add system event (no-op per Go SDK)."""
        self._logger.debug("DeviceProfile add ignored (no-op): %s", details.get("name"))

    def _on_profile_updated(self, details: Dict[str, Any]) -> None:
        """Handle DeviceProfile update system event."""
        from ..internal.cache import DeviceProfile
        profile = DeviceProfile(
            name=details.get("name", ""),
            description=details.get("description", ""),
            manufacturer=details.get("manufacturer", ""),
            model=details.get("model", ""),
            labels=details.get("labels", []),
            device_resources=details.get("deviceResources", []),
            device_commands=details.get("deviceCommands", []),
            resources=details.get("resources", []),
        )
        Profiles().update(profile)
        self._logger.info("DeviceProfile %s updated via system event", profile.name)

    def _on_profile_deleted(self, name: str) -> None:
        """Handle DeviceProfile delete system event."""
        Profiles().remove_by_name(name)
        self._logger.info("DeviceProfile %s deleted via system event", name)

    def _on_watcher_added(self, details: Dict[str, Any]) -> None:
        """Handle ProvisionWatcher add system event."""
        from ..internal.cache import ProvisionWatcher
        watcher = ProvisionWatcher(
            name=details.get("name", ""),
            identifiers=details.get("identifiers", {}),
            blocking_identifiers=details.get("blockingIdentifiers", {}),
            profile_name=details.get("profileName", ""),
            admin_state=details.get("adminState", "UNLOCKED"),
            created=details.get("created", 0),
            modified=details.get("modified", 0),
            origin=details.get("origin", 0),
        )
        ProvisionWatchers().add(watcher)
        self._logger.info("ProvisionWatcher %s added via system event", watcher.name)

    def _on_watcher_updated(self, name: str, updates: Dict[str, Any]) -> None:
        """Handle ProvisionWatcher update system event."""
        watcher, ok = ProvisionWatchers().for_name(name)
        if not ok:
            self._logger.warning("ProvisionWatcher %s not found for update", name)
            return
        for k, v in updates.items():
            if hasattr(watcher, k):
                setattr(watcher, k, v)
        ProvisionWatchers().update(watcher)
        self._logger.info("ProvisionWatcher %s updated via system event", name)

    def _on_watcher_deleted(self, name: str) -> None:
        """Handle ProvisionWatcher delete system event."""
        ProvisionWatchers().remove_by_name(name)
        self._logger.info("ProvisionWatcher %s deleted via system event", name)

    def _on_service_updated(self, details: Dict[str, Any]) -> None:
        """Handle DeviceService update system event."""
        # Update the service model
        for k, v in details.items():
            if hasattr(self.device_service_model, k):
                setattr(self.device_service_model, k, v)
        self._logger.info("DeviceService %s updated via system event", self.name())

    def name(self) -> str:
        """Return the name of this Device Service.

        """
        return self.service_key

    def version(self) -> str:
        """Return the version number of this Device Service.

        """
        return self.service_version

    # -- Async readings / discovery channels ---------------------------------------

    def async_readings_enabled(self) -> bool:
        """Return a bool value indicating whether the asynchronous reading is enabled.

        .
        """
        device = getattr(self.configuration, "device", None)
        if device is None:
            return False
        return bool(getattr(device, "async_readings_enabled", False))

    def async_values_channel(self) -> "queue.Queue[AsyncValues]":
        """Return the channel a developer can use to send asynchronous readings back to
        the SDK.

        """
        return self._async_values_channel

    def discovered_device_channel(self) -> "queue.Queue[List[DiscoveredDevice]]":
        """Return the channel a developer can use to send discovered devices back to the
        SDK.

        """
        return self._discovered_device_channel

    def device_discovery_enabled(self) -> bool:
        """Return a bool value indicating whether device discovery is enabled.

        .
        """
        device = getattr(self.configuration, "device", None)
        if device is None:
            return False
        discovery = getattr(device, "discovery", None)
        if discovery is None:
            return False
        return bool(getattr(discovery, "enabled", False))

    # -- Config / routes / logging / secrets / metrics ------------------------------

    def driver_configs(self) -> dict:
        """Retrieve the driver specific configuration.

        (returns `s.config.Driver`).
        """
        configs = getattr(self.configuration, "driver", None)
        return dict(configs) if configs else {}

    def add_custom_route(self, route: str, authentication: bool,
                         handler: Callable[..., Any],
                         methods: List[str] = ("GET",)) -> None:
        """Leverage the existing internal web server to add routes specific to this
        Device Service.

        When the route is added before `run()` it is queued and registered once the HTTP
        controller exists. The `authentication` flag is currently accepted but the
        authentication hook is not wired yet.

        Raises:
            EdgexError: When the route path is reserved by the SDK (once the controller is
                initialized).
        """
        if self.controller is None:
            self._pending_custom_routes.append((route, handler, list(methods)))
            self._logger.debug("Custom route %s queued until the HTTP controller is "
                               "initialized", route)
            return
        self.controller.add_route(route, handler, list(methods))

    def load_custom_config(self, custom_config: "UpdatableConfig",
                           section_name: str) -> None:
        """Load the service's custom configuration, processing it in the same manner as
        the standard configuration.

        In
Stores the custom configuration so it can be included in the /config
        endpoint response; the Configuration Provider processing is not ported yet.
        """
        self.custom_config = custom_config
        self._custom_config_loaded = True
        if self.controller is not None:
            self.controller.set_custom_config_info(custom_config)
        self._logger.debug("Loaded custom configuration section %s", section_name)

    def listen_for_custom_config_changes(self, config_to_watch: Any, section_name: str,
                                         changed_callback: Callable[[Any], None]) -> None:
        """Listen for changes to the specified custom configuration section.
        `load_custom_config` must have been called previously.

        The Configuration Provider watcher is not wired yet, so only the precondition is
        enforced.

        Raises:
            RuntimeError: When `load_custom_config` has not been called for this section.
        """
        if not self._custom_config_loaded:
            raise RuntimeError(
                f"custom configuration must be loaded before '{section_name}' section "
                f"can be watched for changes")
        self._logger.debug("TODO: listening for changes to custom configuration section "
                           "%s; changedCallback will be invoked by the config processor",
                           section_name)

    def logging_client(self) -> Logger:
        """Return the zero-dependency logging client.

        The client wraps stdlib logging and provides the EdgeX Logger interface
        (Debug, Info, Warn, Error, WithField, WithFields, SetLevel).
        """
        if not hasattr(self, "_logger_client"):
            self._logger_client = Logger(self.name())
        return self._logger_client

    def secret_provider(self) -> SecretProvider:
        """Return the zero-dependency secret provider.

        The provider uses in-memory storage and provides the EdgeX SecretProvider
        interface (StoreSecret, GetSecret, GetAllSecrets, DeleteSecret).
        """
        if not hasattr(self, "_secret_provider"):
            self._secret_provider = SecretProvider()
        return self._secret_provider

    def metrics_manager(self) -> MetricsManager:
        """Return the zero-dependency metrics manager.

        The manager uses in-memory storage and provides the EdgeX MetricsManager
        interface (Counter, Gauge, GaugeFloat64, Timer).
        """
        if not hasattr(self, "_metrics_manager"):
            self._metrics_manager = MetricsManager()
        return self._metrics_manager

# -- System events ------------------------------------------------------------

    def publish_device_discovery_progress_system_event(self, progress: int,
                                                        discovered_device_count: int,
                                                        message: str) -> None:
        """Publish a device discovery progress system event through the EdgeX message bus.

        Delegates to the internal progress helper which uses the message bus client.
        """
        self._publish_discovery_progress(progress, discovered_device_count, message)

    def publish_profile_scan_progress_system_event(self, req_id: str, progress: int,
                                                    message: str) -> None:
        """Publish a profile scan progress system event through the EdgeX message bus.

        Delegates to the internal progress helper which uses the message bus client.
        """
        self._publish_profile_scan_progress(req_id, progress, message)

    def publish_generic_system_event(self, event_type: str, action: str,
                                      details: Any) -> None:
        """Publish a generic system event through the EdgeX message bus.

        This is a general-purpose method for publishing custom system events.
        The topic will be: `<baseTopicPrefix>/system-events/<serviceName>/<event_type>/<action>`
        """
        if self._messaging_client is None or self._message_bus_config_obj is None:
            self._logger.debug("No messaging client configured; system event %s/%s only logged",
                               event_type, action)
            return
        try:
            publish_system_event(
                client=self._messaging_client,
                service_name=self.name(),
                event_type=event_type,
                action=action,
                details=details,
                base_topic_prefix=self._message_bus_config_obj.base_topic_prefix,
                logger=self._logger,
            )
        except Exception as exc: # pylint: disable=broad-except
            self._logger.error("Failed to publish system event %s/%s: %s", event_type, action, exc)

    # -- internal helpers --------------------------------------------------------

    def _add_device_to_metadata(self, device: Device, bypass_validation: bool) -> str:
        """Add the Device to the local cache first, then Core Metadata.

        Cache-first: the Device is validated (unless bypassed) and cached before the Core
        Metadata write. On a metadata failure the cache entry is rolled back and the error
        is propagated as an `EdgexError` (KindServerError) so the caller sees the write
        did not succeed.
        """
        self._validate_device(device, bypass_validation)
        if not device.id:
            device.id = make_uid()
        if not device.service_name:
            device.service_name = self.service_key
        Devices().add(device)
        try:
            client = self._metadata_client()
            if client is None:
                return device.id
            new_id = client.add_device(device, bypass_validation=bypass_validation)
            if new_id:
                device.id = new_id
        except MetadataError as exc:
            Devices().remove_by_name(device.name)
            message = f"failed to add Device {device.name} to Core Metadata: {exc}"
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc
        return device.id

    def _patch_device_in_metadata(self, name: str, updates: Dict[str, Any],
                                  bypass_validation: bool) -> None:
        """Patch the Device in Core Metadata, refreshing the local cache first.

        Cache-first: the update map is applied to the cached Device (and validated unless
        bypassed) before the Core Metadata patch. On a metadata failure the cached Device
        is restored from a snapshot and the error is propagated as an `EdgexError`.
        """
        device, ok = Devices().for_name(name)
        if not ok:
            self._logger.debug("Device %s not in cache during metadata patch; skipping "
                               "cache refresh", name)
            return
        snapshot = device.clone()
        for key, value in updates.items():
            if value is None:
                continue
            if hasattr(device, key):
                setattr(device, key, value)
        self._validate_device(device, bypass_validation)
        Devices().update(device)
        try:
            client = self._metadata_client()
            if client is None:
                return
            client.patch_device(name, updates, bypass_validation=bypass_validation)
        except MetadataError as exc:
            Devices().update(snapshot)
            message = f"failed to patch Device {name} in Core Metadata: {exc}"
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc

    def _delete_device_from_metadata(self, name: str) -> None:
        """Delete the Device from Core Metadata, removing the local cache entry first.

        Cache-first: the cache entry is removed before the Core Metadata delete. On a
        metadata failure the cache entry is restored and the error is propagated as an
        `EdgexError`.
        """
        device, ok = Devices().for_name(name)
        if not ok:
            return
        Devices().remove_by_name(name)
        try:
            client = self._metadata_client()
            if client is None:
                return
            client.delete_device(name)
        except MetadataError as exc:
            Devices().add(device)
            message = f"failed to delete Device {name} from Core Metadata: {exc}"
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc

    def _add_profile_to_metadata(self, profile: DeviceProfile) -> str:
        """Add the DeviceProfile to the local cache first, then Core Metadata.

        On a metadata failure the cache entry is rolled back and the error is propagated
        as an `EdgexError`. The returned id is the one assigned by Core Metadata (a
        generated UUID when Core Metadata is not configured).
        """
        if not profile.id:
            profile.id = make_uid()
        Profiles().add(profile)
        try:
            client = self._metadata_client()
            if client is None:
                return profile.id
            new_id = client.add_device_profile(profile)
            if new_id:
                profile.id = new_id
        except MetadataError as exc:
            Profiles().remove_by_name(profile.name)
            message = f"failed to add Profile {profile.name} to Core Metadata: {exc}"
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc
        return profile.id

    def _update_profile_in_metadata(self, profile: DeviceProfile) -> None:
        """Update the DeviceProfile in Core Metadata, refreshing the local cache first.

        On a metadata failure the cached Profile is restored from a snapshot and the error
        is propagated as an `EdgexError`.
        """
        snapshot, ok = Profiles().for_name(profile.name)
        if not ok:
            return
        Profiles().update(profile)
        try:
            client = self._metadata_client()
            if client is None:
                return
            client.update_device_profile(profile)
        except MetadataError as exc:
            Profiles().update(snapshot)
            message = f"failed to update Profile {profile.name} in Core Metadata: {exc}"
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc

    def _delete_profile_from_metadata(self, name: str) -> None:
        """Delete the DeviceProfile from Core Metadata, removing the cache entry first.

        On a metadata failure the cache entry is restored and the error is propagated as
        an `EdgexError`.
        """
        profile, ok = Profiles().for_name(name)
        if not ok:
            return
        Profiles().remove_by_name(name)
        try:
            client = self._metadata_client()
            if client is None:
                return
            client.delete_device_profile(name)
        except MetadataError as exc:
            Profiles().add(profile)
            message = f"failed to delete Profile {name} from Core Metadata: {exc}"
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc

    def _add_provision_watcher_to_metadata(self, watcher: ProvisionWatcher) -> str:
        """Add the ProvisionWatcher to the local cache first, then Core Metadata.

        On a metadata failure the cache entry is rolled back and the error is propagated
        as an `EdgexError`. The returned id is the one assigned by Core Metadata.
        """
        if not watcher.id:
            watcher.id = make_uid()
        if not watcher.service_name:
            watcher.service_name = self.service_key
        ProvisionWatchers().add(watcher)
        try:
            client = self._metadata_client()
            if client is None:
                return watcher.id
            new_id = client.add_provision_watcher(watcher)
            if new_id:
                watcher.id = new_id
        except MetadataError as exc:
            ProvisionWatchers().remove_by_name(watcher.name)
            message = (f"failed to add ProvisionWatcher {watcher.name} "
                       f"to Core Metadata: {exc}")
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc
        return watcher.id

    def _update_provision_watcher_in_metadata(self, watcher: ProvisionWatcher) -> None:
        """Update the ProvisionWatcher in Core Metadata, refreshing the local cache first.

        On a metadata failure the cached Watcher is restored from a snapshot and the error
        is propagated as an `EdgexError`.
        """
        snapshot, ok = ProvisionWatchers().for_name(watcher.name)
        if not ok:
            return
        ProvisionWatchers().update(watcher)
        try:
            client = self._metadata_client()
            if client is None:
                return
            client.update_provision_watcher(watcher)
        except MetadataError as exc:
            ProvisionWatchers().update(snapshot)
            message = (f"failed to update ProvisionWatcher {watcher.name} "
                       f"in Core Metadata: {exc}")
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc

    def _delete_provision_watcher_from_metadata(self, name: str) -> None:
        """Delete the ProvisionWatcher from Core Metadata, removing the cache first.

        On a metadata failure the cache entry is restored and the error is propagated as
        an `EdgexError`.
        """
        watcher, ok = ProvisionWatchers().for_name(name)
        if not ok:
            return
        ProvisionWatchers().remove_by_name(name)
        try:
            client = self._metadata_client()
            if client is None:
                return
            client.delete_provision_watcher(name)
        except MetadataError as exc:
            ProvisionWatchers().add(watcher)
            message = f"failed to delete ProvisionWatcher {name} from Core Metadata: {exc}"
            self._logger.error(message)
            raise create_edgx_error(KIND_SERVER_ERROR, message) from exc

    def _start_auto_events(self) -> None:
        """Start the AutoEvent manager.

        The manager module (`internal.autoevent.manager`) is not ported yet, so a lazy
        import is attempted and its absence is only logged.
        """
        manager = self._auto_event_manager()
        if manager is None:
            self._logger.debug("AutoEvent manager is not available; auto events will not "
                               "be started")
            return
        self._logger.debug("Starting AutoEvents")
        manager.start_auto_events()

    def _restart_auto_events(self, device_name: str) -> None:
        """Restart the AutoEvent executors of the Device with the given name (Go
        `s.autoEventManager.RestartForDevice(deviceName)`).
        """
        manager = self._auto_event_manager()
        if manager is None:
            self._logger.debug("AutoEvent manager is not available; skipping restart for "
                               "device %s", device_name)
            return
        manager.restart_for_device(device_name)

    def _auto_event_manager(self) -> Any:
        """Lazily import and return the AutoEvent manager singleton.

        The `internal.autoevent.manager` module is ported in a later phase; until then
        this returns None. Any import / attribute error is logged and treated as "not
        ported yet" so the service keeps running.
        """
        if self._auto_event_manager_instance is None:
            try:
                module = importlib.import_module(
                    "device_sdk_py.internal.autoevent.manager")
                self._auto_event_manager_instance = module.AutoEventManager(self)
            except (ImportError, AttributeError, TypeError) as exc:
                self._logger.debug("AutoEvent manager is not available yet: %s", exc)
        return self._auto_event_manager_instance

    def _init_http_controller(self) -> None:
        """Initialize the REST controller, register the SDK reserved routes and any queued
        custom routes.
        """
        if self.controller is not None:
            return
        self.controller = RestController(
            service_name=self.name(),
            service_version=self.version(),
            logger=self._logger,
            configuration=self.configuration,
            driver=self.driver,
            device_service=self,  # full DeviceService for discovery/profile-scan tracking
            send_event_handler=self._send_event_handler,
            device_discovery_stop_handler=self._device_discovery_stop_handler,
            profile_scan_handler=self._profile_scan_handler,
            profile_scan_stop_handler=self._profile_scan_stop_handler)
        self.controller.init_rest_routes()

        for route, handler, methods in self._pending_custom_routes:
            try:
                self.controller.add_route(route, handler, methods)
            except EdgexError as exc:
                self._logger.warning("failed to register custom route %s: %s", route, exc)
        self._pending_custom_routes.clear()

        if self.custom_config is not None:
            self.controller.set_custom_config_info(self.custom_config)

    def _http_host_port(self) -> Tuple[str, int]:
        """Return the host / port the HTTP server binds to, read defensively from the
        configuration (the Python `ConfigurationStruct` model is not ported yet).
        """
        service = getattr(self.configuration, "service", None)
        host = getattr(service, "host", _DEFAULT_HTTP_HOST) if service is not None \
            else _DEFAULT_HTTP_HOST
        port = getattr(service, "port", _DEFAULT_HTTP_PORT) if service is not None \
            else _DEFAULT_HTTP_PORT
        return host, int(port)


def create_device_service(service_key: str, service_version: str, driver: Any,
                          configuration: Any = None,
                          logger: Optional[logging.Logger] = None) -> DeviceService:
    """Create a `DeviceService` for the specified key, version and driver."""
    return DeviceService(service_key, service_version, driver, configuration, logger)
