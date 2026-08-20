# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
`device-sdk-go/internal/provision/devices.go` (`LoadDevices`).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..cache import (
    ADMIN_STATE_UNLOCKED,
    Device,
)
from .common import (
    _LOGGER,
    _as_float,
    _as_raw_map,
    _as_str,
    _as_str_list,
    _as_str_map,
    _auto_event,
    _normalize_device_list,
    _parse_file,
    _scan_files,
)


def _build_device(d: Dict[str, Any]) -> Device:
    d = d or {}
    protocols: Dict[str, Dict[str, str]] = {}
    raw_protocols = d.get("protocols") or {}
    if isinstance(raw_protocols, dict):
        for pname, props in raw_protocols.items():
            protocols[str(pname)] = _as_str_map(props) if isinstance(props, dict) \
                else {"": "" if props is None else str(props)}
    return Device(
        id=_as_str(d.get("id")),
        name=_as_str(d.get("name")),
        description=_as_str(d.get("description")),
        admin_state=_as_str(d.get("adminState", ADMIN_STATE_UNLOCKED)),
        operating_state=_as_str(d.get("operatingState")),
        service_name=_as_str(d.get("serviceName")),
        profile_name=_as_str(d.get("profileName")),
        labels=_as_str_list(d.get("labels")),
        location=d.get("location"),
        auto_events=[_auto_event(a) for a in (d.get("autoEvents") or [])],
        protocols=protocols,
        last_connected=int(_as_float(d.get("lastConnected", 0))),
        last_reported=int(_as_float(d.get("lastReported", 0))),
        tags=_as_raw_map(d.get("tags")),
        properties=_as_raw_map(d.get("properties")),
    )


def load_devices(path: str, logger: logging.Logger = _LOGGER) -> List[Device]:
    """Load every Device file in ``path`` (mirrors ``provision.LoadDevices``)."""
    devices: List[Device] = []
    for file_path in _scan_files(path):
        try:
            parsed = _parse_file(file_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to parse Device %s: %s", file_path, exc)
            continue
        for item in _normalize_device_list(parsed):
            devices.append(_build_device(item))
    logger.info("Loaded %d Device(s) from %s", len(devices), path)
    return devices