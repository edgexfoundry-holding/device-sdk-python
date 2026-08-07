# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The internal caches of the EdgeX Device Service SDK - Exports:
    DeviceCache / Devices(): The thread-safe cache of Devices (keyed by name).
    DeviceProfileCache / Profiles(): The thread-safe cache of DeviceProfiles with
        DeviceResource / DeviceCommand / ResourceOperation lookup.
    ProvisionWatcherCache / ProvisionWatchers(): The thread-safe cache of
        ProvisionWatchers.
    The cache data models (Device, DeviceProfile, DeviceResource, ResourceProperties,
        ResourceOperation, DeviceCommand, ProvisionWatcher, AdminState, AutoEvent,
        ProtocolProperties).
    CacheError / CacheErrorKind / create_cache_error: The cache error types.
"""

from .devices import (
    Devices,
    DeviceCache,
    check_profile_not_used,
    create_device_cache,
)
from .profiles import (
    Profiles,
    DeviceProfileCache,
    create_profile_cache,
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
    ProvisionWatcherDiscoveredDevice,
    ResourceOperation,
    ResourceProperties,
    create_cache_error,
)
from .provisionwatchers import (
    ProvisionWatchers,
    ProvisionWatcherCache,
    create_provision_watcher_cache,
)

__all__ = [
    "Devices",
    "Profiles",
    "ProvisionWatchers",
    "DeviceCache",
    "DeviceProfileCache",
    "ProvisionWatcherCache",
    "create_device_cache",
    "create_profile_cache",
    "create_provision_watcher_cache",
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
    "ProvisionWatcherDiscoveredDevice",
    "ResourceOperation",
    "ResourceProperties",
    "ProtocolProperties",
    "CacheError",
    "CacheErrorKind",
    "create_cache_error",
]
