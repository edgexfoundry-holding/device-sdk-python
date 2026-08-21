# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/v4/internal/application/profilescan.go`.

Profile scan handler for device profile scanning.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from ..cache import Devices
    from ..common.configuration import ConfigurationStruct
    from ...interfaces import ExtendedProtocolDriver
    from app_functions_sdk_py.contracts.dtos.requests.profilescan import ProfileScanRequest

from ..common.utils import (
    EdgexError,
    KIND_ENTITY_DOES_NOT_EXIST,
    KIND_NOT_IMPLEMENTED,
    KIND_SERVICE_LOCKED,
    create_edgx_error,
)


class ProfileScanLocker:
    """Thread-safe locker for profile scan operations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy_map: dict[str, bool] = {}

    def acquire(self, device_name: str) -> bool:
        """Try to acquire the lock for a device.

        Returns:
            True if lock was acquired, False if already busy.
        """
        with self._lock:
            if self._busy_map.get(device_name, False):
                return False
            self._busy_map[device_name] = True
            return True

    def release(self, device_name: str) -> None:
        """Release the lock for a device."""
        with self._lock:
            self._busy_map[device_name] = False

    def is_busy(self, device_name: str) -> bool:
        """Check if a device has an active profile scan."""
        with self._lock:
            return self._busy_map.get(device_name, False)


_locker = ProfileScanLocker()


def _publish_progress(dic: dict, request_id: str, progress: int, message: str,
                      device_name: str = "") -> None:
    """Publish a profile scan progress system event (best effort)."""
    try:
        from ..controller.messaging.publish import publish_system_event
        client = dic.get("message_client")
        if client is None:
            return
        service_name = getattr(dic.get("device_service"), "name", "device-service")
        publish_system_event(
            client=client,
            service_name=service_name,
            event_type="deviceprofile",
            action="progress",
            details={
                "requestId": request_id,
                "profileName": "",
                "deviceName": device_name,
                "progress": progress,
                "message": message,
            },
            base_topic_prefix=getattr(dic.get("configuration"), "base_topic_prefix", "edgex")
            or "edgex",
        )
    except Exception:  # pragma: no cover - best effort publish
        pass


def profile_scan_wrapper(
    busy_result: List[bool],
    extdriver: ExtendedProtocolDriver,
    req: Any,
    dic: dict,
) -> None:
    """Wrapper for profile scan operation with locking and progress reporting.

    Args:
        busy_result: List receiving the busy status (appended before the scan runs so
            the caller can detect a concurrent scan).
        extdriver: Extended protocol driver.
        req: Profile scan request (with ``device_name`` / ``profile_name`` /
            ``request_id`` / ``options``).
        dic: DI container.
    """
    acquired = _locker.acquire(req.device_name)
    busy_result.append(acquired)
    if not acquired:
        return

    lc = dic.get("logging_client")
    try:
        _publish_progress(dic, req.request_id, 0, "", req.device_name)
        lc.debug("Profile scan triggered with device name '%s' and profile name '%s', "
                 "Correlation Id: %s", req.device_name, req.profile_name, req.request_id)

        profile = extdriver.profile_scan(req.device_name, req.profile_name,
                                         req.request_id, req.options)

        dpc = dic.get("device_profile_client")
        if dpc is not None:
            dpc.add_device_profile(profile)
        else:
            from ..cache import create_profile_cache
            profiles = dic.get("profiles")
            if profiles is not None:
                profiles.add(profile)

        dc = dic.get("device_client")
        if dc is not None and hasattr(dc, "update_device"):
            dc.update_device({"Name": req.device_name, "ProfileName": profile.name})

        _publish_progress(dic, req.request_id, 100, "", req.device_name)
        lc.debug("Profile scan completed for device '%s'", req.device_name)
    except Exception as exc:
        err_msg = f"Failed to trigger profile scan: {exc}, Correlation Id: {req.request_id}"
        _publish_progress(dic, req.request_id, -1, err_msg, req.device_name)
        lc.error(err_msg)
    finally:
        _locker.release(req.device_name)


def stop_profile_scan(dic: dict, device_name: str, options: dict) -> Optional[EdgexError]:
    """Stop a running profile scan operation.

    Args:
        dic: DI container.
        device_name: Name of the device whose profile scan should stop.
        options: Additional stop options.

    Returns:
        EdgexError if an error occurred, None if successful.
    """
    lc = dic.get("logging_client")
    extdriver = dic.get("extended_protocol_driver")
    if extdriver is None:
        return create_edgx_error(KIND_NOT_IMPLEMENTED,
                                 "Stop profile scan is not implemented")

    device_service = dic.get("device_service")
    if device_service is not None and str(getattr(device_service, "admin_state", "")) == "LOCKED":
        return create_edgx_error(KIND_SERVICE_LOCKED, "Service locked")

    devices = dic.get("devices")
    if devices is not None:
        _, exist = devices.for_name(device_name)
        if not exist:
            return create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST,
                                     f"Device {device_name} not found")

    if not _locker.is_busy(device_name):
        lc.debug("No active profile scan process was found")
        return None

    lc.debug("Stopping profile scan for device - %s", device_name)
    if hasattr(extdriver, "stop_profile_scan"):
        extdriver.stop_profile_scan(device_name, options)
    lc.debug("Profile scan for device - %s stop signal is sent", device_name)
    return None