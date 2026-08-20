# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/v4/internal/application/callback.go`.

Callback handlers for Core Metadata events (profile/device/provision watcher updates).
The Go functions receive `requests.*Request` DTOs from the DI container; the Python port
accepts the corresponding cache model objects (``Device`` / ``DeviceProfile`` /
``ProvisionWatcher``) as done by `dtos.To*Model` in Go.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from ..common.utils import (
    EdgexError,
    KIND_CONTRACT_INVALID,
    KIND_ENTITY_DOES_NOT_EXIST,
    KIND_SERVER_ERROR,
)

if TYPE_CHECKING:
    from ..cache import Profiles, Devices, ProvisionWatchers
    from ..common.configuration import ConfigurationStruct
    from ...interfaces import ProtocolDriver


def update_profile(profile: Any, dic: dict) -> Optional[EdgexError]:
    """Handle profile update from Core Metadata.

    Args:
        profile: The updated DeviceProfile model.
        dic: DI container.

    Returns:
        EdgexError if failed, None if successful.
    """
    lc = dic.get("logging_client")
    profiles: Profiles = dic.get("profiles")

    profile_name = getattr(profile, "name", "")
    if not profile_name:
        return EdgexError(KIND_CONTRACT_INVALID, "Profile name is required")

    _, ok = profiles.for_name(profile_name)
    if not ok:
        return EdgexError(KIND_CONTRACT_INVALID, f"Failed to find profile {profile_name}")

    try:
        profiles.update(profile)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR, f"Failed to update profile {profile_name}")

    lc.debug("Profile %s updated", profile_name)

    driver: ProtocolDriver = dic.get("protocol_driver")
    devices: Devices = dic.get("devices")
    for d in devices.all():
        if d.profile_name == profile_name:
            try:
                driver.update_device(d.name, d.protocols, d.admin_state)
                lc.debug("Invoked driver.UpdateDevice callback for %s", d.name)
            except Exception:
                return EdgexError(
                    KIND_SERVER_ERROR, f"driver.UpdateDevice callback failed for {d.name}")
    return None


def delete_profile(profile_name: str, dic: dict) -> Optional[EdgexError]:
    """Handle profile deletion from Core Metadata.

    Args:
        profile_name: Name of the profile to delete.
        dic: DI container.

    Returns:
        EdgexError if failed, None if successful.
    """
    lc = dic.get("logging_client")
    profiles: Profiles = dic.get("profiles")
    devices: Devices = dic.get("devices")

    if not any(d.profile_name == profile_name for d in devices.all()):
        try:
            profiles.remove_by_name(profile_name)
            lc.debug("Profile %s removed from cache", profile_name)
        except Exception:
            return EdgexError(
                KIND_SERVER_ERROR, f"Failed to remove device profile {profile_name}")
    else:
        lc.warning(
            "Received Profile Deletion System Event for %s, but the profile is still "
            "used by some devices", profile_name)
    return None


def add_device(device: Any, dic: dict) -> Optional[EdgexError]:
    """Handle device addition from Core Metadata.

    Args:
        device: The Device model to add.
        dic: DI container.

    Returns:
        EdgexError if failed, None if successful.
    """
    lc = dic.get("logging_client")
    devices: Devices = dic.get("devices")
    device_name = getattr(device, "name", "")

    profile_name = getattr(device, "profile_name", "")
    if profile_name:
        err = update_associated_profile(profile_name, dic)
        if err:
            return err

    try:
        devices.add(device)
        lc.debug("Device %s added", device_name)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR, f"Failed to add device {device_name}")

    driver: ProtocolDriver = dic.get("protocol_driver")
    try:
        driver.add_device(device_name, device.protocols, device.admin_state)
        lc.debug("Invoked driver.AddDevice callback for %s", device_name)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR,
                          f"driver.AddDevice callback failed for {device_name}")

    config: ConfigurationStruct = dic.get("configuration")
    fails_tracker = dic.get("allowed_request_failures_tracker")
    if config is not None:
        allowed_fails = getattr(getattr(config, "device", None), "allowed_fails", 0)
        if fails_tracker is not None:
            fails_tracker.set(device_name, int(allowed_fails or 0))

    lc.debug("Starting AutoEvents for device %s", device_name)
    auto_event_manager = dic.get("auto_event_manager")
    if auto_event_manager is not None:
        auto_event_manager.restart_for_device(device_name)
    return None


def update_device(device: Any, dic: dict) -> Optional[EdgexError]:
    """Handle device update from Core Metadata.

    Args:
        device: The updated Device model.
        dic: DI container.

    Returns:
        EdgexError if failed, None if successful.
    """
    lc = dic.get("logging_client")
    devices: Devices = dic.get("devices")
    device_service = dic.get("device_service")

    device_name = getattr(device, "name", "")
    if not device_name:
        return EdgexError(KIND_CONTRACT_INVALID, "Device name is required")

    _, exist = devices.for_name(device_name)
    if not exist:
        # Device migrating from another service to here.
        service_name = getattr(device, "service_name", "")
        if getattr(device_service, "name", None) == service_name:
            return add_device(device, dic)
        return EdgexError(KIND_ENTITY_DOES_NOT_EXIST, f"Failed to find device {service_name}")

    # Device moving to another service.
    service_name = getattr(device, "service_name", "")
    if getattr(device_service, "name", None) != service_name:
        return delete_device(device_name, dic)

    profile_name = getattr(device, "profile_name", "")
    if profile_name:
        err = update_associated_profile(profile_name, dic)
        if err:
            return err

    try:
        devices.update(device)
        lc.debug("Device %s updated", device_name)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR, f"Failed to update device {device_name}")

    driver: ProtocolDriver = dic.get("protocol_driver")
    try:
        driver.update_device(device_name, device.protocols, device.admin_state)
        lc.debug("Invoked driver.UpdateDevice callback for %s", device_name)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR,
                          f"driver.UpdateDevice callback failed for {device_name}")

    auto_event_manager = dic.get("auto_event_manager")
    if auto_event_manager is not None:
        if str(getattr(device, "admin_state", "")) == "LOCKED":
            lc.debug("Stopping AutoEvents for the locked device %s", device_name)
            auto_event_manager.stop_for_device(device_name)
        else:
            lc.debug("Starting AutoEvents for device %s", device_name)
            auto_event_manager.restart_for_device(device_name)
    return None


def delete_device(name: str, dic: dict) -> Optional[EdgexError]:
    """Handle device deletion from Core Metadata.

    Args:
        name: Device name to delete.
        dic: DI container.

    Returns:
        EdgexError if failed, None if successful.
    """
    lc = dic.get("logging_client")
    devices: Devices = dic.get("devices")

    device, ok = devices.for_name(name)
    if not ok:
        return EdgexError(KIND_CONTRACT_INVALID, f"Failed to find device {name}")

    lc.debug("Stopping AutoEvents for device %s", name)
    auto_event_manager = dic.get("auto_event_manager")
    if auto_event_manager is not None:
        auto_event_manager.stop_for_device(name)

    try:
        devices.remove_by_name(name)
        lc.debug("Removed device: %s", name)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR, f"Failed to remove device {name}")

    driver: ProtocolDriver = dic.get("protocol_driver")
    try:
        driver.remove_device(name, device.protocols)
        lc.debug("Invoked driver.RemoveDevice callback for %s", name)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR,
                          f"driver.RemoveDevice callback failed for {name}")

    fails_tracker = dic.get("allowed_request_failures_tracker")
    if fails_tracker is not None:
        fails_tracker.remove(name)
    return None


def add_provision_watcher(watcher: Any, dic: dict) -> Optional[EdgexError]:
    """Handle provision watcher addition from Core Metadata."""
    lc = dic.get("logging_client")
    provision_watchers = dic.get("provision_watchers")
    watcher_name = getattr(watcher, "name", "")

    profile_name = getattr(watcher, "profile_name", "")
    if profile_name:
        err = update_associated_profile(profile_name, dic)
        if err:
            return err

    try:
        provision_watchers.add(watcher)
        lc.debug("Provision watcher %s added", watcher_name)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR,
                          f"Failed to add provision watcher {watcher_name}")
    return None


def update_provision_watcher(watcher: Any, dic: dict) -> Optional[EdgexError]:
    """Handle provision watcher update from Core Metadata."""
    lc = dic.get("logging_client")
    provision_watchers = dic.get("provision_watchers")
    device_service = dic.get("device_service")

    watcher_name = getattr(watcher, "name", "")
    if not watcher_name:
        return EdgexError(KIND_CONTRACT_INVALID, "Provision watcher name is required")

    _, exist = provision_watchers.for_name(watcher_name)
    if not exist:
        service_name = getattr(watcher, "service_name", "")
        if getattr(device_service, "name", None) == service_name:
            return add_provision_watcher(watcher, dic)
        return delete_provision_watcher(watcher_name, dic)

    service_name = getattr(watcher, "service_name", "")
    if getattr(device_service, "name", None) != service_name:
        return delete_provision_watcher(watcher_name, dic)

    profile_name = getattr(watcher, "profile_name", "")
    if profile_name:
        err = update_associated_profile(profile_name, dic)
        if err:
            return err

    try:
        provision_watchers.update(watcher)
        lc.debug("Provision watcher %s updated", watcher_name)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR,
                          f"Failed to update provision watcher {watcher_name}")
    return None


def delete_provision_watcher(name: str, dic: dict) -> Optional[EdgexError]:
    """Handle provision watcher deletion from Core Metadata."""
    lc = dic.get("logging_client")
    provision_watchers = dic.get("provision_watchers")
    try:
        provision_watchers.remove_by_name(name)
        lc.debug("Removed provision watcher %s", name)
    except Exception:
        return EdgexError(KIND_CONTRACT_INVALID, f"Failed to remove provision watcher {name}")
    return None


def update_device_service(service: Any, dic: dict) -> Optional[EdgexError]:
    """Handle device service update from Core Metadata."""
    lc = dic.get("logging_client")
    device_service = dic.get("device_service")

    service_name = getattr(service, "name", "")
    if getattr(device_service, "name", None) != service_name:
        return EdgexError(KIND_ENTITY_DOES_NOT_EXIST,
                          f"Failed to identify device service {service_name}")

    admin_state = getattr(service, "admin_state", None)
    if admin_state:
        device_service.admin_state = admin_state

    labels = getattr(service, "labels", None)
    if labels is not None:
        device_service.labels = labels

    lc.debug("Device service updated")
    return None


def update_associated_profile(profile_name: str, dic: dict) -> Optional[EdgexError]:
    """Update the profile specified in device / watcher requests to stay consistent with
    Core Metadata.

    Uses the SDK's own Core Metadata client (``MetadataClient.device_profile_by_name``).
    When no metadata client is configured the profile sync is skipped (the local cache is
    the source of truth in hermetic mode).
    """
    lc = dic.get("logging_client")
    profiles: Profiles = dic.get("profiles")
    dpc = dic.get("device_profile_client")

    if dpc is None:
        lc.debug("No device profile client configured; skipping profile sync for %s",
                 profile_name)
        return None

    try:
        res = dpc.device_profile_by_name(profile_name)
    except Exception:
        return EdgexError(
            KIND_CONTRACT_INVALID, f"Failed to retrieve profile {profile_name} from metadata")

    profile = res.get("profile") if isinstance(res, dict) else res
    if profile is None:
        return EdgexError(
            KIND_CONTRACT_INVALID, f"Failed to retrieve profile {profile_name} from metadata")

    _, exist = profiles.for_name(profile_name)
    try:
        if not exist:
            profiles.add(profile)
        else:
            profiles.update(profile)
    except Exception:
        return EdgexError(KIND_SERVER_ERROR, f"Failed to update profile {profile_name} in cache")
    return None