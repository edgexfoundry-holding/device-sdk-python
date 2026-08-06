# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The DeviceProfile cache - ported from `device-sdk-go/internal/cache/profiles.go`.

`DeviceProfileCache` is a thread-safe in-memory store of the DeviceProfiles managed by the
Device Service, together with two derived lookup maps (DeviceResource by name and
DeviceCommand by name) so resources / commands can be resolved quickly by the transformer
and the command controller.

A module level singleton is used to share the cache across the service, mirroring the Go
package-level `pc *profileCache` variable and the `cache.Profiles()` accessor.
"""

from __future__ import annotations

import re
import threading
from typing import Dict, List, Optional, Tuple

from .providers import (
    CacheError,
    CacheErrorKind,
    DeviceCommand,
    DeviceProfile,
    DeviceResource,
    ResourceOperation,
    new_cache_error,
)


class DeviceProfileCache:
    """A thread-safe cache of DeviceProfiles keyed by Profile name.

    Corresponds to `cache.ProfileCache` in profiles.go.  All access is guarded by a
    reentrant lock (`threading.RLock`, the Python counterpart of `sync.RWMutex`); the read
    methods return clones of the stored Profiles.
    """

    def __init__(self, profiles: List[DeviceProfile]):
        self._device_profile_map: Dict[str, DeviceProfile] = {}
        self._device_resource_map: Dict[str, Dict[str, DeviceResource]] = {}
        self._device_command_map: Dict[str, Dict[str, DeviceCommand]] = {}
        self._mutex = threading.RLock()
        for profile in profiles:
            self._device_profile_map[profile.name] = profile
            self._device_resource_map[profile.name] = \
                self._device_resource_slice_to_map(profile.device_resources)
            self._device_command_map[profile.name] = \
                self._device_command_slice_to_map(profile.device_commands)

    @staticmethod
    def _device_resource_slice_to_map(
            device_resources: List[DeviceResource]) -> Dict[str, DeviceResource]:
        result: Dict[str, DeviceResource] = {}
        for device_resource in device_resources:
            result[device_resource.name] = device_resource
        return result

    @staticmethod
    def _device_command_slice_to_map(
            device_commands: List[DeviceCommand]) -> Dict[str, DeviceCommand]:
        result: Dict[str, DeviceCommand] = {}
        for device_command in device_commands:
            result[device_command.name] = device_command
        return result

    def for_name(self, name: str) -> Tuple[DeviceProfile, bool]:
        """Return a clone of the Profile with the given name and whether it exists.

        Mirrors `ProfileCache.ForName(name)`.  A clone is returned (never the stored
        instance) to avoid concurrent mutation of the cached Profile.
        """
        with self._mutex:
            profile = self._device_profile_map.get(name)
            if profile is None:
                return DeviceProfile(), False
            return profile.clone(), True

    def all(self) -> List[DeviceProfile]:
        """Return clones of all Profiles in the cache (mirrors `ProfileCache.All()`)."""
        with self._mutex:
            return [profile.clone() for profile in self._device_profile_map.values()]

    def add(self, profile: DeviceProfile) -> None:
        """Add a new Profile to the cache.

        Mirrors `ProfileCache.Add(profile)`.  Raises `CacheError` with kind
        `DUPLICATE_NAME` when a Profile with the same name already exists.
        """
        with self._mutex:
            self._add(profile)

    def check_and_add(self, profile: DeviceProfile) -> None:
        """Add the Profile to the cache unless it already exists (no-op then).

        Mirrors `ProfileCache.CheckAndAdd(profile)` which returns without error when the
        Profile is already present.
        """
        with self._mutex:
            if profile.name not in self._device_profile_map:
                self._add(profile)

    def _add(self, profile: DeviceProfile) -> None:
        if profile.name in self._device_profile_map:
            raise new_cache_error(
                CacheErrorKind.DUPLICATE_NAME,
                f"Profile {profile.name} has already existed in cache")
        self._device_profile_map[profile.name] = profile
        self._device_resource_map[profile.name] = \
            self._device_resource_slice_to_map(profile.device_resources)
        self._device_command_map[profile.name] = \
            self._device_command_slice_to_map(profile.device_commands)

    def update(self, profile: DeviceProfile) -> None:
        """Update the Profile in the cache.

        Mirrors `ProfileCache.Update(profile)` which removes the existing entry first and
        then adds the new one.  Raises `CacheError` with kind `ENTITY_DOES_NOT_EXIST`
        when the Profile is not present.
        """
        with self._mutex:
            self._remove_by_name(profile.name)
            self._add(profile)

    def remove_by_name(self, name: str) -> None:
        """Remove the Profile with the given name from the cache.

        Mirrors `ProfileCache.RemoveByName(name)`.  Raises `CacheError` with kind
        `ENTITY_DOES_NOT_EXIST` when the Profile is not present.
        """
        with self._mutex:
            self._remove_by_name(name)

    def _remove_by_name(self, name: str) -> None:
        if name not in self._device_profile_map:
            raise new_cache_error(
                CacheErrorKind.ENTITY_DOES_NOT_EXIST,
                f"failed to find Profile {name} in cache")
        del self._device_profile_map[name]
        del self._device_resource_map[name]
        del self._device_command_map[name]

    def device_resource(self, profile_name: str,
                        resource_name: str) -> Tuple[DeviceResource, bool]:
        """Return the DeviceResource with the given resource name in the Profile.

        Mirrors `ProfileCache.DeviceResource(profileName, resourceName)`.
        """
        with self._mutex:
            resources = self._device_resource_map.get(profile_name)
            if resources is None:
                return DeviceResource(), False
            device_resource = resources.get(resource_name)
            if device_resource is None:
                return DeviceResource(), False
            return device_resource, True

    def device_resources_by_regex(
            self, profile_name: str, regex: re.Pattern) -> Tuple[List[DeviceResource], bool]:
        """Return the DeviceResources matching the given regex pattern in the Profile.

        Mirrors `ProfileCache.DeviceResourcesByRegex(profileName, regex)`.  A resource
        matches when its name is either equal to the regex pattern string or fully matched
        by the pattern.
        """
        with self._mutex:
            resources = self._device_resource_map.get(profile_name)
            if resources is None:
                return [], False
            matched: List[DeviceResource] = []
            for device_resource in resources.values():
                # Go first checks the resource name against the regex source string, then
                # checks whether the leftmost match of the pattern spans the whole name.
                if device_resource.name == regex.pattern:
                    matched.append(device_resource)
                    continue
                match = regex.search(device_resource.name)
                if match is not None and match.group(0) == device_resource.name:
                    matched.append(device_resource)
            return matched, True

    def device_command(self, profile_name: str,
                       command_name: str) -> Tuple[DeviceCommand, bool]:
        """Return the DeviceCommand with the given command name in the Profile.

        Mirrors `ProfileCache.DeviceCommand(profileName, commandName)`.
        """
        with self._mutex:
            commands = self._device_command_map.get(profile_name)
            if commands is None:
                return DeviceCommand(), False
            device_command = commands.get(command_name)
            if device_command is None:
                return DeviceCommand(), False
            return device_command, True

    def resource_operation(self, profile_name: str, resource_name: str) -> ResourceOperation:
        """Return the first ResourceOperation whose DeviceResource matches the given name.

        Mirrors `ProfileCache.ResourceOperation(profileName, deviceResource)`.  Raises
        `CacheError` with kind `ENTITY_DOES_NOT_EXIST` when the Profile is missing or no
        ResourceOperation matches.
        """
        with self._mutex:
            self._verify_profile_exists(profile_name)
            for device_command in self._device_command_map[profile_name].values():
                for resource_operation in device_command.resource_operations:
                    if resource_operation.device_resource == resource_name:
                        return resource_operation
            raise new_cache_error(
                CacheErrorKind.ENTITY_DOES_NOT_EXIST,
                f"failed to find ResourceOpertaion with DeviceResource {resource_name} "
                f"in Profile {profile_name}")

    def _verify_profile_exists(self, profile_name: str) -> None:
        if profile_name not in self._device_profile_map:
            raise new_cache_error(
                CacheErrorKind.ENTITY_DOES_NOT_EXIST,
                f"failed to find Profile {profile_name} in cache")


#: The package-level singleton mirroring the Go `pc *profileCache` variable.
_profile_cache: Optional[DeviceProfileCache] = None


def new_profile_cache(profiles: List[DeviceProfile]) -> DeviceProfileCache:
    """Initialize and return the profile cache singleton with the given Profiles.

    Python counterpart of `cache.newProfileCache(profiles)` in profiles.go.
    """
    global _profile_cache
    _profile_cache = DeviceProfileCache(profiles)
    return _profile_cache


def Profiles() -> DeviceProfileCache:
    """Return the profile cache singleton (mirrors `cache.Profiles()`).

    The singleton must have been initialized via `new_profile_cache()` before calling this.
    """
    if _profile_cache is None:
        raise RuntimeError("profile cache has not been initialized")
    return _profile_cache


# PascalCase aliases kept for parity with the Go exported identifiers.
NewProfileCache = new_profile_cache
