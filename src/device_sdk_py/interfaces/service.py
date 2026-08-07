# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The DeviceServiceSDK interface.

``DeviceServiceSDK`` is the abstract interface an EdgeX Device Service is built on.
The EdgeX contracts (models.Device, models.DeviceProfile, models.ProvisionWatcher,
models.AutoEvent, models.DeviceResource, models.DeviceCommand and the UpdateDevice DTO)
are supplied by the reused ``app_functions_sdk_py`` package; only type annotations
reference them so this module does not require that package at runtime.
"""

from __future__ import annotations

import queue
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, List, Tuple

from ..models import AsyncValues, DiscoveredDevice

if TYPE_CHECKING:
    from app_functions_sdk_py.contracts.clients.logger import Logger
    from app_functions_sdk_py.contracts.dtos.autoevent import AutoEvent
    from app_functions_sdk_py.contracts.dtos.device import Device, UpdateDevice
    from app_functions_sdk_py.contracts.dtos.devicecommand import DeviceCommand
    from app_functions_sdk_py.contracts.dtos.deviceprofile import DeviceProfile
    from app_functions_sdk_py.contracts.dtos.deviceresource import DeviceResource
    from app_functions_sdk_py.contracts.dtos.provisionwatcher import ProvisionWatcher

#: Route authentication markers.
UNAUTHENTICATED = False
AUTHENTICATED = True


class UpdatableConfig(ABC):
    """Marker ABC for services using custom configuration.

    Services using custom configuration must implement this on their custom config,
    even if they do not use the Configuration Provider.
    """


class DeviceServiceSDK(ABC):
    """The interface for an EdgeX Device Service SDK."""

    # -- Device management ---------------------------------------------------

    @abstractmethod
    def add_device(self, device: "Device") -> str:
        """Add a new Device to the Device Service and Core Metadata.

        Returns:
            The new Device id.
        """

    @abstractmethod
    def add_device_without_validation(self, device: "Device") -> str:
        """Add a new Device with device validation skipped.

        Returns:
            The new Device id.
        """

    @abstractmethod
    def devices(self) -> List["Device"]:
        """Return all managed Devices from cache."""

    @abstractmethod
    def get_device_by_name(self, name: str) -> "Device":
        """Return the Device by its name if it exists in the cache, or raise an error."""

    @abstractmethod
    def update_device(self, device: "Device") -> None:
        """Update the Device in the cache and ensure that the copy in Core Metadata is
        also updated."""

    @abstractmethod
    def update_device_without_validation(self, device: "Device") -> None:
        """Update the Device in Core Metadata with device validation skipped."""

    @abstractmethod
    def remove_device_by_name(self, name: str) -> None:
        """Remove the specified Device by name from the cache and ensure that the
        instance in Core Metadata is also removed."""

    @abstractmethod
    def device_exists_for_name(self, name: str) -> bool:
        """Return True if a Device exists in cache with the specified name."""

    @abstractmethod
    def patch_device(self, update_device: "UpdateDevice") -> None:
        """Patch the specified device properties in Core Metadata. Device name is required
        to be provided in the UpdateDevice. All properties of UpdateDevice are optional;
        anything that is None will not modify the Device. Arrays and Maps are applied as
        an overwrite operation (send the whole new value)."""

    @abstractmethod
    def patch_device_without_validation(self, update_device: "UpdateDevice") -> None:
        """Patch the specified device properties in Core Metadata with device validation
        skipped. Device name is required in the UpdateDevice; None properties are not
        modified; arrays and maps are overwritten."""

    @abstractmethod
    def update_device_operating_state(self, name: str, state: str) -> None:
        """Update the OperatingState for the Device with the given name in Core Metadata."""

    # -- Device Profile management --------------------------------------------

    @abstractmethod
    def add_device_profile(self, profile: "DeviceProfile") -> str:
        """Add a new DeviceProfile to the Device Service and Core Metadata.

        Returns:
            The new DeviceProfile id.
        """

    @abstractmethod
    def device_profiles(self) -> List["DeviceProfile"]:
        """Return all managed DeviceProfiles from cache."""

    @abstractmethod
    def get_profile_by_name(self, name: str) -> "DeviceProfile":
        """Return the Profile by its name if it exists in the cache, or raise an error."""

    @abstractmethod
    def update_device_profile(self, profile: "DeviceProfile") -> None:
        """Update the DeviceProfile in the cache and ensure that the copy in Core
        Metadata is also updated."""

    @abstractmethod
    def remove_device_profile_by_name(self, name: str) -> None:
        """Remove the specified DeviceProfile by name from the cache and ensure that the
        instance in Core Metadata is also removed."""

    # -- Provision Watcher management ------------------------------------------

    @abstractmethod
    def add_provision_watcher(self, watcher: "ProvisionWatcher") -> str:
        """Add a new Watcher to the cache and Core Metadata.

        Returns:
            The new Watcher id.
        """

    @abstractmethod
    def provision_watchers(self) -> List["ProvisionWatcher"]:
        """Return all managed Watchers from cache."""

    @abstractmethod
    def get_provision_watcher_by_name(self, name: str) -> "ProvisionWatcher":
        """Return the Watcher by its name if it exists in the cache, or raise an error."""

    @abstractmethod
    def update_provision_watcher(self, watcher: "ProvisionWatcher") -> None:
        """Update the Watcher in the cache and ensure that the copy in Core Metadata is
        also updated."""

    @abstractmethod
    def remove_provision_watcher(self, name: str) -> None:
        """Remove the specified Watcher by name from the cache and ensure that the
        instance in Core Metadata is also removed."""

    # -- Device resource / command lookup ---------------------------------------

    @abstractmethod
    def device_resource(self, device_name: str, device_resource: str) -> Tuple["DeviceResource", bool]:
        """Retrieve the specific DeviceResource instance from cache according to the
        Device name and Device Resource name."""

    @abstractmethod
    def device_command(self, device_name: str, command_name: str) -> Tuple["DeviceCommand", bool]:
        """Retrieve the specific DeviceCommand instance from cache according to the
        Device name and Command name."""

    # -- AutoEvents --------------------------------------------------------------

    @abstractmethod
    def add_device_auto_event(self, device_name: str, event: "AutoEvent") -> None:
        """Add a new AutoEvent to the Device with the given name."""

    @abstractmethod
    def remove_device_auto_event(self, device_name: str, event: "AutoEvent") -> None:
        """Remove an AutoEvent from the Device with the given name."""

    # -- Lifecycle / runtime ------------------------------------------------------

    @abstractmethod
    def run(self) -> None:
        """Start this Device Service. This should not be called directly by a device
        service; instead call the bootstrap entry point."""

    @abstractmethod
    def stop(self) -> None:
        """Gracefully stop this Device Service.

        Stops all background pumps, subscriptions, and threads, disconnects the
        messaging client, shuts down the metadata executor, and cleans up resources.
        This method is idempotent and safe to call multiple times.
        """

    @abstractmethod
    def name(self) -> str:
        """Return the name of this Device Service."""

    @abstractmethod
    def version(self) -> str:
        """Return the version number of this Device Service."""

    # -- Async readings / discovery channels ---------------------------------------

    @abstractmethod
    def async_readings_enabled(self) -> bool:
        """Return a bool value indicating whether the asynchronous reading is enabled."""

    @abstractmethod
    def async_values_channel(self) -> "queue.Queue[AsyncValues]":
        """Return the queue used to send asynchronous readings back to the SDK."""

    @abstractmethod
    def discovered_device_channel(self) -> "queue.Queue[List[DiscoveredDevice]]":
        """Return the queue used to send discovered devices back to the SDK."""

    @abstractmethod
    def device_discovery_enabled(self) -> bool:
        """Return a bool value indicating whether device discovery is enabled."""

    # -- Config / routes / logging / secrets / metrics ------------------------------

    @abstractmethod
    def driver_configs(self) -> dict:
        """Retrieve the driver specific configuration."""

    @abstractmethod
    def add_custom_route(self, route: str, authentication: bool,
                         handler: Callable[..., Any], methods: List[str] = ("GET",)) -> None:
        """Leverage the existing internal web server to add routes specific to this
        Device Service."""

    @abstractmethod
    def load_custom_config(self, custom_config: "UpdatableConfig", section_name: str) -> None:
        """Load the service's custom configuration, processing it in the same manner as
        the standard configuration."""

    @abstractmethod
    def listen_for_custom_config_changes(self, config_to_watch: Any, section_name: str,
                                         changed_callback: Callable[[Any], None]) -> None:
        """Listen for changes to the specified custom configuration section.
        `load_custom_config` must have been called previously."""

    @abstractmethod
    def logging_client(self) -> "Logger":
        """Return the logging client."""

    @abstractmethod
    def secret_provider(self) -> Any:
        """Return the secret provider."""

    @abstractmethod
    def metrics_manager(self) -> Any:
        """Return the Metrics Manager used to register counter, gauge, gaugeFloat64 or
        timer metric types."""

    # -- System events ------------------------------------------------------------

    @abstractmethod
    def publish_device_discovery_progress_system_event(self, progress: int,
                                                       discovered_device_count: int,
                                                       message: str) -> None:
        """Publish a device discovery progress system event through the EdgeX message bus."""

    @abstractmethod
    def publish_profile_scan_progress_system_event(self, req_id: str, progress: int,
                                                   message: str) -> None:
        """Publish a profile scan progress system event through the EdgeX message bus."""

    @abstractmethod
    def publish_generic_system_event(self, event_type: str, action: str,
                                     details: Any) -> None:
        """Publish a generic system event through the EdgeX message bus."""
