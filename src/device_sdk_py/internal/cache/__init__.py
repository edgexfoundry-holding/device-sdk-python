# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The internal caches of the EdgeX Device Service SDK - ported from
`device-sdk-go/internal/cache`.

Exports:
    DeviceCache / Devices(): The thread-safe cache of Devices (keyed by name).
    DeviceProfileCache / Profiles(): The thread-safe cache of DeviceProfiles with
        DeviceResource / DeviceCommand / ResourceOperation lookup.
    ProvisionWatcherCache / ProvisionWatchers(): The thread-safe cache of
        ProvisionWatchers.
    The cache data models (Device, DeviceProfile, DeviceResource, ResourceProperties,
        ResourceOperation, DeviceCommand, ProvisionWatcher, AdminState, AutoEvent,
        ProtocolProperties).
    CacheError / CacheErrorKind / new_cache_error: The cache error types.
"""

from .devices import (
    Devices,
    NewDeviceCache,
    DeviceCache,
    check_profile_not_used,
    new_device_cache,
)
from .profiles import (
    NewProfileCache,
    Profiles,
    DeviceProfileCache,
    new_profile_cache,
)
from .providers import (
    ADMIN_STATE_LOCKED,
    ADMIN_STATE_UNLOCKED,
    AdminState,
    AutoEvent,
    CacheError,
    CacheErrorKind,
    Device,
    DeviceCommand,
    DeviceProfile,
    DeviceResource,
    ProtocolProperties,
    ProvisionWatcher,
    ResourceOperation,
    ResourceProperties,
    new_cache_error,
)
from .provisionwatchers import (
    NewProvisionWatcherCache,
    ProvisionWatchers,
    ProvisionWatcherCache,
    new_provision_watcher_cache,
)

__all__ = [
    "Devices",
    "Profiles",
    "ProvisionWatchers",
    "DeviceCache",
    "DeviceProfileCache",
    "ProvisionWatcherCache",
    "NewDeviceCache",
    "NewProfileCache",
    "NewProvisionWatcherCache",
    "new_device_cache",
    "new_profile_cache",
    "new_provision_watcher_cache",
    "check_profile_not_used",
    "AdminState",
    "ADMIN_STATE_LOCKED",
    "ADMIN_STATE_UNLOCKED",
    "AutoEvent",
    "Device",
    "DeviceCommand",
    "DeviceProfile",
    "DeviceResource",
    "ProvisionWatcher",
    "ResourceOperation",
    "ResourceProperties",
    "ProtocolProperties",
    "CacheError",
    "CacheErrorKind",
    "new_cache_error",
]
