# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
Pre-defined resource loading - ported from `device-sdk-go/internal/provision`
(`LoadProfiles` / `LoadDevices` / `LoadProvisionWatchers`).

A device service ships its DeviceProfiles, Devices and ProvisionWatchers as files under
``res/profiles``, ``res/devices`` and ``res/provisionwatchers``.  At startup the SDK reads
every ``.json`` / ``.yaml`` / ``.yml`` file in those directories and populates the internal
caches with the parsed entities (mirroring the Go ``processProfiles`` / ``processDevices`` /
``processWatchers`` steps that seed ``cache.Profiles()`` / ``cache.Devices()`` /
``cache.ProvisionWatchers()``).

Both JSON and YAML encodings are accepted.  The EdgeX DTO files use canonical camelCase
keys (e.g. ``valueType``, ``readWrite``, ``deviceResources``); the loaders read those keys
directly so the *structural* fields map onto the Python models while *free-form* maps -
Device protocols, resource attributes, ProvisionWatcher identifiers, tags - keep their
original keys verbatim (just as the Go JSON decoder preserves ``map[string]string`` keys).

Parse errors on a single file are logged and skipped so one bad file never aborts the
service start.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

import yaml

from .cache import (
    ADMIN_STATE_UNLOCKED,
    AutoEvent,
    Device,
    DeviceCommand,
    DeviceProfile,
    DeviceResource,
    ProvisionWatcher,
    ResourceOperation,
    ResourceProperties,
)

__all__ = [
    "load_profiles",
    "load_devices",
    "load_provision_watchers",
]

_LOGGER = logging.getLogger(__name__)

#: File extensions accepted by the scanner (mirrors Go's ``jsonExt``/``yamlExt``).
_JSON_EXT = ".json"
_YAML_EXTS = (".yaml", ".yml")
_SUPPORTED_EXTS = (_JSON_EXT, *_YAML_EXTS)


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if value is None:
        return default
    return bool(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _as_str_map(value: Any) -> Dict[str, str]:
    """Coerce a mapping's values to strings (EdgeX protocol properties / identifiers are
    `map[string]string`).  Keys are preserved unchanged."""
    if not isinstance(value, dict):
        return {}
    return {str(k): ("" if v is None else str(v)) for k, v in value.items()}


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(v) for v in value]
    except TypeError:
        return [str(value)]


def _as_str_map_of_lists(value: Any) -> Dict[str, List[str]]:
    """Coerce a mapping's values to string lists (ProvisionWatcher blocking identifiers
    are `map[string][]string`).  Keys are preserved unchanged."""
    if not isinstance(value, dict):
        return {}
    return {str(k): _as_str_list(v) for k, v in value.items()}


def _as_raw_map(value: Any) -> Dict[str, Any]:
    """Return a shallow copy of a free-form mapping, or an empty dict."""
    if not isinstance(value, dict):
        return {}
    return dict(value)


# ---------------------------------------------------------------------------
# Model builders (read canonical camelCase DTO keys directly)
# ---------------------------------------------------------------------------

def _resource_properties(d: Dict[str, Any]) -> ResourceProperties:
    d = d or {}
    return ResourceProperties(
        value_type=_as_str(d.get("valueType")),
        read_write=_as_str(d.get("readWrite")),
        units=_as_str(d.get("units")),
        minimum=d.get("minimum"),
        maximum=d.get("maximum"),
        default_value=_as_str(d.get("defaultValue")),
        mask=d.get("mask"),
        shift=d.get("shift"),
        scale=d.get("scale"),
        offset=d.get("offset"),
        base=d.get("base"),
        assertion=_as_str(d.get("assertion")),
        media_type=_as_str(d.get("mediaType")),
    )


def _resource(r: Dict[str, Any]) -> DeviceResource:
    r = r or {}
    return DeviceResource(
        name=_as_str(r.get("name")),
        description=_as_str(r.get("description")),
        is_hidden=_as_bool(r.get("isHidden", False)),
        tag=_as_str(r.get("tag")),
        properties=_resource_properties(r.get("properties") or {}),
        attributes=_as_raw_map(r.get("attributes")),
    )


def _operation(op: Dict[str, Any]) -> ResourceOperation:
    op = op or {}
    return ResourceOperation(
        device_resource=_as_str(op.get("deviceResource")),
        default_value=_as_str(op.get("defaultValue")),
        mappings=_as_str_map(op.get("mappings")),
        attributes=_as_raw_map(op.get("attributes")),
    )


def _command(c: Dict[str, Any]) -> DeviceCommand:
    c = c or {}
    return DeviceCommand(
        name=_as_str(c.get("name")),
        is_hidden=_as_bool(c.get("isHidden", False)),
        read_write=_as_str(c.get("readWrite")),
        resource_operations=[_operation(o) for o in (c.get("resourceOperations") or [])],
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


def _auto_event(a: Dict[str, Any]) -> AutoEvent:
    a = a or {}
    return AutoEvent(
        source_name=_as_str(a.get("sourceName", a.get("source_name"))),
        on_change=_as_bool(a.get("onChange", False)),
        on_change_threshold=_as_float(a.get("onChangeThreshold")),
        interval=_as_str(a.get("interval", a.get("frequency", ""))),
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


def _build_provision_watcher(d: Dict[str, Any]) -> ProvisionWatcher:
    d = d or {}
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
    )


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def _scan_files(path: str) -> List[str]:
    """Return the sorted list of supported resource files under ``path``."""
    if not path:
        return []
    abs_path = os.path.abspath(path)
    if not os.path.isdir(abs_path):
        _LOGGER.debug("resource directory not found, skipping: %s", abs_path)
        return []
    files: List[str] = []
    for entry in sorted(os.listdir(abs_path)):
        full = os.path.join(abs_path, entry)
        if not os.path.isfile(full):
            continue
        if os.path.splitext(entry)[1].lower() in _SUPPORTED_EXTS:
            files.append(full)
    return files


def _parse_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    if path.lower().endswith(_JSON_EXT):
        return json.loads(raw)
    loaded = yaml.safe_load(raw)
    return loaded if loaded is not None else {}


def _normalize_device_list(parsed: Any) -> List[Dict[str, Any]]:
    """EdgeX device files come in three shapes: a JSON array, a YAML ``deviceList``
    wrapper, or a bare device object."""
    if isinstance(parsed, list):
        return [p for p in parsed if isinstance(p, dict)]
    if isinstance(parsed, dict):
        if isinstance(parsed.get("deviceList"), list):
            return [p for p in parsed["deviceList"] if isinstance(p, dict)]
        if "name" in parsed:
            return [parsed]
    return []


def _normalize_watcher_list(parsed: Any) -> List[Dict[str, Any]]:
    if isinstance(parsed, list):
        return [p for p in parsed if isinstance(p, dict)]
    if isinstance(parsed, dict):
        if isinstance(parsed.get("provisionWatcherList"), list):
            return [p for p in parsed["provisionWatcherList"] if isinstance(p, dict)]
        if "name" in parsed:
            return [parsed]
    return []


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


def load_provision_watchers(path: str, logger: logging.Logger = _LOGGER) -> List[ProvisionWatcher]:
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
