# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
`device-sdk-go/internal/provision`.

A device service ships its DeviceProfiles, Devices and ProvisionWatchers as files under
``res/profiles``, ``res/devices`` and ``res/provisionwatchers``. At startup the SDK reads
every ``.json`` / ``.yaml`` / ``.yml`` file in those directories and populates the internal
caches with the parsed entities (the ``processProfiles`` / ``processDevices`` /
``processWatchers`` steps that seed ``cache.Profiles()`` / ``cache.Devices()`` /
``cache.ProvisionWatchers()``).

Exports:
    load_profiles: Load every DeviceProfile file in ``path``.
    load_devices: Load every Device file in ``path``.
    load_provision_watchers: Load every ProvisionWatcher file in ``path``.
"""

from .common import (  # noqa: F401 - shared helpers
    _as_bool,
    _as_float,
    _as_raw_map,
    _as_str,
    _as_str_list,
    _as_str_map,
    _as_str_map_of_lists,
    _auto_event,
    _command,
    _normalize_device_list,
    _normalize_watcher_list,
    _operation,
    _parse_file,
    _resource,
    _scan_files,
)
from .devices import _build_device, load_devices
from .profiles import load_profiles
from .provisionwatchers import load_provision_watchers

__all__ = [
    "load_profiles",
    "load_devices",
    "load_provision_watchers",
    "_build_device",
]
