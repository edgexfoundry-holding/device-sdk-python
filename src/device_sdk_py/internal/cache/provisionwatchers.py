# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""

`ProvisionWatcherCache` is a thread-safe in-memory store of the ProvisionWatchers used to
automatically provision discovered Devices.

A module level singleton is used to share the cache across the service, mirroring the Go
package-level `pwc *provisionWatcherCache` variable and the `cache.ProvisionWatchers()`
accessor.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

from .providers import (
    ADMIN_STATE_LOCKED,
    ADMIN_STATE_UNLOCKED,
    AdminState,
    CacheError,
    CacheErrorKind,
    ProvisionWatcher,
    create_cache_error,
)


class ProvisionWatcherCache:
    """A thread-safe cache of ProvisionWatchers keyed by watcher name.

    All access is
    guarded by a reentrant lock (`threading.RLock`, the Python counterpart of
    `sync.RWMutex`); the read methods return clones of the stored ProvisionWatchers.
    """

    def __init__(self, watchers: List[ProvisionWatcher]):
        self._pw_map: Dict[str, ProvisionWatcher] = {}
        self._mutex = threading.RLock()
        for watcher in watchers:
            self._pw_map[watcher.name] = watcher

    def for_name(self, name: str) -> Tuple[ProvisionWatcher, bool]:
        """Return a clone of the ProvisionWatcher with the given name and whether it exists.

        A clone is returned (never the
        stored instance) to avoid concurrent mutation of the cached ProvisionWatcher.
        """
        with self._mutex:
            watcher = self._pw_map.get(name)
            if watcher is None:
                return ProvisionWatcher(), False
            return watcher.clone(), True

    def all(self) -> List[ProvisionWatcher]:
        """Return clones of all ProvisionWatchers in the cache
        (mirrors `ProvisionWatcherCache.All()`)."""
        with self._mutex:
            return [watcher.clone() for watcher in self._pw_map.values()]

    def add(self, watcher: ProvisionWatcher) -> None:
        """Add a new ProvisionWatcher to the cache.

        Raises `CacheError` with kind
        `DUPLICATE_NAME` when a ProvisionWatcher with the same name already exists.
        """
        with self._mutex:
            self._add(watcher)

    def _add(self, watcher: ProvisionWatcher) -> None:
        if watcher.name in self._pw_map:
            raise create_cache_error(
                CacheErrorKind.DUPLICATE_NAME,
                f"ProvisionWatcher {watcher.name} has already existed in cache")
        self._pw_map[watcher.name] = watcher

    def update(self, watcher: ProvisionWatcher) -> None:
        """Update the ProvisionWatcher in the cache.

        Which removes the existing entry
first and then adds the new one. Raises `CacheError` with kind
        `ENTITY_DOES_NOT_EXIST` when the ProvisionWatcher is not present.
        """
        with self._mutex:
            self._remove_by_name(watcher.name)
            self._add(watcher)

    def remove_by_name(self, name: str) -> None:
        """Remove the ProvisionWatcher with the given name from the cache.

        Raises `CacheError` with kind
        `ENTITY_DOES_NOT_EXIST` when the ProvisionWatcher is not present.
        """
        with self._mutex:
            self._remove_by_name(name)

    def _remove_by_name(self, name: str) -> None:
        if name not in self._pw_map:
            raise create_cache_error(
                CacheErrorKind.ENTITY_DOES_NOT_EXIST,
                f"failed to find ProvisionWatcher {name} in cache")
        del self._pw_map[name]

    def update_admin_state(self, name: str, state: AdminState) -> None:
        """Update the admin state of the ProvisionWatcher with the given name.

        with kind `CONTRACT_INVALID` for an invalid admin state and `ENTITY_DOES_NOT_EXIST`
        when the ProvisionWatcher is not present.
        """
        if state != ADMIN_STATE_LOCKED and state != ADMIN_STATE_UNLOCKED:
            raise create_cache_error(CacheErrorKind.CONTRACT_INVALID, "invalid AdminState")
        with self._mutex:
            watcher = self._pw_map.get(name)
            if watcher is None:
                raise create_cache_error(
                    CacheErrorKind.ENTITY_DOES_NOT_EXIST,
                    f"failed to find ProvisionWatcher {name} in cache")
            watcher.admin_state = state


#: The package-level singleton variable.
_provision_watcher_cache: Optional[ProvisionWatcherCache] = None


def create_provision_watcher_cache(watchers: List[ProvisionWatcher]) -> ProvisionWatcherCache:
    """Initialize and return the provision watcher cache singleton with the given watchers.

    """
    global _provision_watcher_cache
    _provision_watcher_cache = ProvisionWatcherCache(watchers)
    return _provision_watcher_cache


def ProvisionWatchers() -> ProvisionWatcherCache:
    """Return the provision watcher cache singleton (mirrors `cache.ProvisionWatchers()`).

    The singleton must have been initialized via `create_provision_watcher_cache()` before
    calling this.
    """
    if _provision_watcher_cache is None:
        raise RuntimeError("provision watcher cache has not been initialized")
    return _provision_watcher_cache


