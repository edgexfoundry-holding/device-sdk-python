# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
`device-sdk-go/internal/provision/common.go` + the shared resource-file scanning helpers.

Both JSON and YAML encodings are accepted. The EdgeX DTO files use canonical camelCase
keys (e.g. ``valueType``, ``readWrite``, ``deviceResources``); the loaders read those keys
directly so the *structural* fields map onto the Python models while *free-form* maps -
Device protocols, resource attributes, ProvisionWatcher identifiers, tags - keep their
original keys verbatim (just as the Go JSON decoder preserves ``map[string]string`` keys).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

import yaml

from ..cache import (
    AutoEvent,
    DeviceCommand,
    DeviceResource,
    ResourceOperation,
    ResourceProperties,
)

#: File extensions accepted by the scanner (mirrors Go's ``jsonExt``/``yamlExt``).
_JSON_EXT = ".json"
_YAML_EXTS = (".yaml", ".yml")
_SUPPORTED_EXTS = (_JSON_EXT, *_YAML_EXTS)

_LOGGER = logging.getLogger(__name__)


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
    `map[string]string`). Keys are preserved unchanged."""
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
    are `map[string][]string`). Keys are preserved unchanged."""
    if not isinstance(value, dict):
        return {}
    return {str(k): _as_str_list(v) for k, v in value.items()}


def _as_raw_map(value: Any) -> Dict[str, Any]:
    """Return a shallow copy of a free-form mapping, or an empty dict."""
    if not isinstance(value, dict):
        return {}
    return dict(value)


# ---------------------------------------------------------------------------
# Shared resource model builders (camelCase DTO keys)
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


def _auto_event(a: Dict[str, Any]) -> AutoEvent:
    a = a or {}
    return AutoEvent(
        source_name=_as_str(a.get("sourceName", a.get("source_name"))),
        on_change=_as_bool(a.get("onChange", False)),
        on_change_threshold=_as_float(a.get("onChangeThreshold")),
        interval=_as_str(a.get("interval", a.get("frequency", ""))),
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


def _normalize_entity_list(parsed: Any, list_key: str) -> List[Dict[str, Any]]:
    """EdgeX entity files come in three shapes: a JSON array, a YAML ``<list_key>``
    wrapper, or a bare entity object."""
    if isinstance(parsed, list):
        return [p for p in parsed if isinstance(p, dict)]
    if isinstance(parsed, dict):
        if isinstance(parsed.get(list_key), list):
            return [p for p in parsed[list_key] if isinstance(p, dict)]
        if "name" in parsed:
            return [parsed]
    return []


def _normalize_device_list(parsed: Any) -> List[Dict[str, Any]]:
    """Normalize the device file shapes (array / ``deviceList`` wrapper / bare object)."""
    return _normalize_entity_list(parsed, "deviceList")


def _normalize_watcher_list(parsed: Any) -> List[Dict[str, Any]]:
    """Normalize the watcher file shapes (array / ``provisionWatcherList`` wrapper / bare
    object)."""
    return _normalize_entity_list(parsed, "provisionWatcherList")
