# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
`device-sdk-go/internal/provision/provisionwatchers.go` (`LoadProvisionWatchers`).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..cache import (
    ADMIN_STATE_UNLOCKED,
    AutoEvent,
    ProvisionWatcher,
    ProvisionWatcherDiscoveredDevice,
)
from .common import (
    _LOGGER,
    _as_bool,
    _as_raw_map,
    _as_str,
    _as_str_list,
    _as_str_map,
    _as_str_map_of_lists,
    _normalize_watcher_list,
    _parse_file,
    _scan_files,
)


def _build_provision_watcher(d: Dict[str, Any]) -> ProvisionWatcher:
    d = d or {}
    discovered = d.get("discoveredDevice", {})
    return ProvisionWatcher(
        id=_as_str(d.get("id")),
        name=_as_str(d.get("name")),
        description=_as_str(d.get("description")),
        service_name=_as_str(d.get("serviceName")),
        labels=_as_str_list(d.get("labels")),
        identifiers=_as_str_map(d.get("identifiers")),
        blocking_identifiers=_as_str_map_of_lists(d.get("blockingIdentifiers")),
        admin_state=_as_str(d.get("adminState", ADMIN_STATE_UNLOCKED)),
        profile_name=_as_str(d.get("profileName")),
        discovered_device=ProvisionWatcherDiscoveredDevice(
            profile_name=_as_str(discovered.get("profileName")),
            admin_state=_as_str(discovered.get("adminState", "UNLOCKED")),
            labels=_as_str_list(discovered.get("labels")),
            auto_events=[AutoEvent(
                source_name=_as_str(ae.get("sourceName")),
                on_change=_as_bool(ae.get("onChange")),
                on_change_threshold=_as_str(ae.get("onChangeThreshold")),
                interval=_as_str(ae.get("interval")),
            ) for ae in discovered.get("autoEvents", [])],
            properties=_as_raw_map(discovered.get("properties")),
        ),
    )


def load_provision_watchers(path: str,
                            logger: logging.Logger = _LOGGER) -> List[ProvisionWatcher]:
    """Load every ProvisionWatcher file in ``path`` (mirrors
    ``provision.LoadProvisionWatchers``)."""
    watchers: List[ProvisionWatcher] = []
    for file_path in _scan_files(path):
        try:
            parsed = _parse_file(file_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to parse ProvisionWatcher %s: %s", file_path, exc)
            continue
        for item in _normalize_watcher_list(parsed):
            watchers.append(_build_provision_watcher(item))
    logger.info("Loaded %d ProvisionWatcher(s) from %s", len(watchers), path)
    return watchers