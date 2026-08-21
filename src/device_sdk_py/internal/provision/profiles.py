# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
`device-sdk-go/internal/provision/profiles.go` (`LoadProfiles`).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..cache import DeviceProfile
from .common import (
    _LOGGER,
    _as_raw_map,
    _as_str,
    _as_str_list,
    _command,
    _parse_file,
    _resource,
    _scan_files,
)


def _build_profile(d: Dict[str, Any]) -> DeviceProfile:
    d = d or {}
    return DeviceProfile(
        name=_as_str(d.get("name")),
        description=_as_str(d.get("description")),
        manufacturer=_as_str(d.get("manufacturer")),
        model=_as_str(d.get("model")),
        labels=_as_str_list(d.get("labels")),
        device_resources=[_resource(r) for r in (d.get("deviceResources") or [])],
        device_commands=[_command(c) for c in (d.get("deviceCommands") or [])],
        add_tags=_as_raw_map(d.get("addTags")),
        properties=_as_raw_map(d.get("properties")),
    )


def load_profiles(path: str, logger: logging.Logger = _LOGGER) -> List[DeviceProfile]:
    """Load every DeviceProfile file in ``path`` (mirrors ``provision.LoadProfiles``)."""
    profiles: List[DeviceProfile] = []
    for file_path in _scan_files(path):
        try:
            parsed = _parse_file(file_path)
        except Exception as exc:  # noqa: BLE001 - keep one bad file from aborting start
            logger.warning("failed to parse Device Profile %s: %s", file_path, exc)
            continue
        if not isinstance(parsed, dict):
            logger.warning("Device Profile %s is not an object; skipping", file_path)
            continue
        profiles.append(_build_profile(parsed))
    logger.info("Loaded %d Device Profile(s) from %s", len(profiles), path)
    return profiles