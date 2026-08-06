# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The Device cache - ported from `device-sdk-go/internal/cache/devices.go`.

`DeviceCache` is a thread-safe in-memory store of the Devices managed by the Device Service.
It is initialized from Core Metadata and kept in sync as Devices are added / updated /
removed.  Read access hands out clones of the stored Devices so that callers cannot mutate
the cached instance (the Go implementation returns `device.Clone()` for the same reason).

A module level singleton is used to share the cache across the service, mirroring the Go
package-level `dc *deviceCache` variable and the `cache.Devices()` accessor.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .providers import (
    ADMIN_STATE_LOCKED,
    ADMIN_STATE_UNLOCKED,
    AdminState,
    CacheError,
    CacheErrorKind,
    Device,
    new_cache_error,
)

#: Last connected metric name prefix (Go `lastConnectedPrefix`).
LAST_CONNECTED_PREFIX = "LastConnected-{DeviceName}"


def _current_timestamp() -> int:
    """Return the current timestamp in nanoseconds (mirrors the Go `currentTimestamp`
    function which returns `time.Now().UnixNano()`)."""
    return time.time_ns()


class DeviceCache:
    """A thread-safe cache of Devices keyed by Device name.

    Corresponds to `cache.DeviceCache` in devices.go.  All mutating methods are guarded by a
    reentrant lock (`threading.RLock`, the Python counterpart of `sync.RWMutex`); the read
    methods return clones of the stored Devices.
    """

    def __init__(self, devices: List[Device]):
        self._device_map: Dict[str, Device] = {}
        self._mutex = threading.RLock()
        self._last_connected: Dict[str, int] = {}
        for device in devices:
            self._device_map[device.name] = device
            self._last_connected[device.name] = 0

    def for_name(self, name: str) -> Tuple[Device, bool]:
        """Return a clone of the Device with the given name and whether it exists.

        Mirrors `DeviceCache.ForName(name)`.  A clone is returned (never the stored
        instance) to avoid concurrent mutation of the cached Device.
        """
        with self._mutex:
            device = self._device_map.get(name)
            if device is None:
                return Device(), False
            return device.clone(), True

    def all(self) -> List[Device]:
        """Return clones of all Devices in the cache (mirrors `DeviceCache.All()`)."""
        with self._mutex:
            return [device.clone() for device in self._device_map.values()]

    def add(self, device: Device) -> None:
        """Add a new Device to the cache.

        Mirrors `DeviceCache.Add(device)`.  Raises `CacheError` with kind
        `DUPLICATE_NAME` when a Device with the same name already exists.
        """
        with self._mutex:
            self._add(device)

    def _add(self, device: Device) -> None:
        if device.name in self._device_map:
            raise new_cache_error(
                CacheErrorKind.DUPLICATE_NAME,
                f"Device {device.name} has already existed in cache")
        self._device_map[device.name] = device
        self._last_connected[device.name] = 0

    def update(self, device: Device) -> None:
        """Update the Device in the cache.

        Mirrors `DeviceCache.Update(device)` which removes the existing entry first and
        then adds the new one.  Raises `CacheError` with kind `ENTITY_DOES_NOT_EXIST`
        when the Device is not present.
        """
        with self._mutex:
            self._remove_by_name(device.name)
            self._add(device)

    def remove_by_name(self, name: str) -> None:
        """Remove the Device with the given name from the cache.

        Mirrors `DeviceCache.RemoveByName(name)`.  Raises `CacheError` with kind
        `ENTITY_DOES_NOT_EXIST` when the Device is not present.
        """
        with self._mutex:
            self._remove_by_name(name)

    def _remove_by_name(self, name: str) -> None:
        if name not in self._device_map:
            raise new_cache_error(
                CacheErrorKind.ENTITY_DOES_NOT_EXIST,
                f"failed to find Device {name} in cache")
        del self._device_map[name]
        self._last_connected.pop(name, None)

    def update_admin_state(self, name: str, state: AdminState) -> None:
        """Update the admin state of the Device with the given name.

        Mirrors `DeviceCache.UpdateAdminState(name, state)`.  Raises `CacheError` with kind
        `CONTRACT_INVALID` for an invalid admin state and `ENTITY_DOES_NOT_EXIST` when the
        Device is not present.
        """
        if state != ADMIN_STATE_LOCKED and state != ADMIN_STATE_UNLOCKED:
            raise new_cache_error(CacheErrorKind.CONTRACT_INVALID, "invalid AdminState")
        with self._mutex:
            device = self._device_map.get(name)
            if device is None:
                raise new_cache_error(
                    CacheErrorKind.ENTITY_DOES_NOT_EXIST,
                    f"failed to find Device {name} in cache")
            device.admin_state = state

    def update_operating_state(self, name: str, state: str) -> None:
        """Update the operating state of the Device with the given name.

        Mirrors `DeviceCache.UpdateOperatingState(name, state)` in Go.
        Valid states: "UP", "DOWN", "DISABLED".
        """
        valid_states = {"UP", "DOWN", "DISABLED"}
        if state not in valid_states:
            raise new_cache_error(CacheErrorKind.CONTRACT_INVALID, "invalid OperatingState")
        with self._mutex:
            device = self._device_map.get(name)
            if device is None:
                raise new_cache_error(
                    CacheErrorKind.ENTITY_DOES_NOT_EXIST,
                    f"failed to find Device {name} in cache")
            device.operating_state = state

    def device_exists(self, name: str) -> bool:
        """Return True when a Device with the given name exists in the cache.

        Python counterpart of the Go SDK's `DeviceExistsForName(name)` which is implemented
        as `_, ok := cache.Devices().ForName(name); return ok`.
        """
        with self._mutex:
            return name in self._device_map

    def set_last_connected_by_name(self, name: str) -> None:
        """Record the current timestamp as the last connected time for the Device.

        Mirrors `DeviceCache.SetLastConnectedByName(name)`.  In Go this updates a metrics
        gauge; here the value is kept in a plain dictionary so the cache has no dependency
        on the metrics manager.
        """
        with self._mutex:
            if name in self._last_connected:
                self._last_connected[name] = _current_timestamp()

    def get_last_connected_by_name(self, name: str) -> int:
        """Return the last connected time (nanoseconds) for the Device.

        Mirrors `DeviceCache.GetLastConnectedByName(name)`.  Returns 0 for a Device that
        never connected or is not present in the cache.
        """
        with self._mutex:
            return self._last_connected.get(name, 0)


#: The package-level singleton mirroring the Go `dc *deviceCache` variable.
_device_cache: Optional[DeviceCache] = None


def new_device_cache(devices: List[Device]) -> DeviceCache:
    """Initialize and return the device cache singleton with the given Devices.

    Python counterpart of `cache.newDeviceCache(devices, dic)` in devices.go.  The DI
    container and the metrics registration are omitted since the Python cache keeps the
    last connected times in memory.
    """
    global _device_cache
    _device_cache = DeviceCache(devices)
    return _device_cache


def Devices() -> DeviceCache:
    """Return the device cache singleton (mirrors `cache.Devices()`).

    The singleton must have been initialized via `new_device_cache()` before calling this.
    """
    if _device_cache is None:
        raise RuntimeError("device cache has not been initialized")
    return _device_cache


def check_profile_not_used(profile_name: str) -> bool:
    """Return True when no Device in the cache uses the given Profile name.

    Mirrors `cache.CheckProfileNotUsed(profileName)` in devices.go.
    """
    global _device_cache
    if _device_cache is None:
        return True
    with _device_cache._mutex:
        return all(device.profile_name != profile_name
                   for device in _device_cache._device_map.values())


# PascalCase aliases kept for parity with the Go exported identifiers.
NewDeviceCache = new_device_cache
