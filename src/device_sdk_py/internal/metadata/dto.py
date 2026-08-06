# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
Serializers that turn the SDK's internal models (`internal.cache`) into the Core Metadata v3
request JSON.

This mirrors ``app-functions-sdk-python``'s DTO layer (the camelCase EdgeX contracts:
``AddDeviceRequest`` / ``AddDeviceProfileRequest`` / ``AddDeviceServiceRequest`` /
``AddProvisionWatcherRequest``).  Core Metadata expects a JSON *array* of request objects, each
carrying an ``apiVersion`` / ``requestId`` envelope and the entity under the matching key (e.g.
``device`` for devices, ``profile`` for profiles).  Referencing the Go ``device-sdk-go``
``processProfiles`` / ``processDevices`` / ``processWatchers`` and ``selfRegister`` flows.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from ..cache import (
    AutoEvent,
    Device,
    DeviceCommand,
    DeviceProfile,
    DeviceResource,
    ProvisionWatcher,
    ResourceOperation,
    ResourceProperties,
)

API_VERSION = "v3"


def _make_uid() -> str:
    return str(uuid.uuid4())


def _request(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap an entity under the ``{apiVersion, requestId, ...}`` request envelope."""
    return {"apiVersion": API_VERSION, "requestId": _make_uid(), **request_data}


def _resource_properties_to_dto(props: ResourceProperties) -> Dict[str, Any]:
    dto: Dict[str, Any] = {"valueType": props.value_type, "readWrite": props.read_write}
    if props.units:
        dto["units"] = props.units
    if props.minimum is not None:
        dto["minimum"] = props.minimum
    if props.maximum is not None:
        dto["maximum"] = props.maximum
    if props.default_value:
        dto["defaultValue"] = props.default_value
    if props.mask is not None:
        dto["mask"] = props.mask
    if props.shift is not None:
        dto["shift"] = props.shift
    if props.scale is not None:
        dto["scale"] = props.scale
    if props.offset is not None:
        dto["offset"] = props.offset
    if props.base is not None:
        dto["base"] = props.base
    if props.assertion:
        dto["assertion"] = props.assertion
    if props.media_type:
        dto["mediaType"] = props.media_type
    return dto


def _device_resource_to_dto(resource: DeviceResource) -> Dict[str, Any]:
    dto: Dict[str, Any] = {"name": resource.name}
    if resource.description:
        dto["description"] = resource.description
    if resource.is_hidden:
        dto["isHidden"] = True
    if resource.tag:
        dto["tag"] = resource.tag
    dto["properties"] = _resource_properties_to_dto(resource.properties)
    if resource.attributes:
        dto["attributes"] = dict(resource.attributes)
    return dto


def _operation_to_dto(op: ResourceOperation) -> Dict[str, Any]:
    dto: Dict[str, Any] = {"deviceResource": op.device_resource}
    if op.default_value:
        dto["defaultValue"] = op.default_value
    if op.mappings:
        dto["mappings"] = dict(op.mappings)
    if op.attributes:
        dto["attributes"] = dict(op.attributes)
    return dto


def _command_to_dto(command: DeviceCommand) -> Dict[str, Any]:
    dto: Dict[str, Any] = {"name": command.name}
    if command.is_hidden:
        dto["isHidden"] = True
    if command.read_write:
        dto["readWrite"] = command.read_write
    dto["resourceOperations"] = [_operation_to_dto(op) for op in command.resource_operations]
    return dto


def device_profile_to_dto(profile: DeviceProfile) -> Dict[str, Any]:
    """Serialize a DeviceProfile model into the Core Metadata ``profile`` document.

    Mirrors ``dtos.DeviceProfile`` (``dtos.FromDeviceProfileModelToDTO``).
    """
    dto: Dict[str, Any] = {"name": profile.name}
    if profile.description:
        dto["description"] = profile.description
    if profile.manufacturer:
        dto["manufacturer"] = profile.manufacturer
    if profile.model:
        dto["model"] = profile.model
    if profile.labels:
        dto["labels"] = list(profile.labels)
    dto["deviceResources"] = [_device_resource_to_dto(r) for r in profile.device_resources]
    dto["deviceCommands"] = [_command_to_dto(c) for c in profile.device_commands]
    if profile.add_tags:
        dto["addTags"] = dict(profile.add_tags)
    if profile.properties:
        dto["properties"] = dict(profile.properties)
    return dto


def add_device_profile_request(profile: DeviceProfile) -> Dict[str, Any]:
    """Build the Core request object ``{"apiVersion","requestId","profile"}``."""
    return _request({"profile": device_profile_to_dto(profile)})


def _auto_event_to_dto(event: AutoEvent) -> Dict[str, Any]:
    dto: Dict[str, Any] = {"sourceName": event.source_name}
    if event.on_change:
        dto["onChange"] = True
    if event.on_change_threshold:
        dto["onChangeThreshold"] = event.on_change_threshold
    if event.interval:
        dto["interval"] = event.interval
    return dto


def device_to_dto(device: Device) -> Dict[str, Any]:
    """Serialize a Device model into the Core ``device`` body dict.

    Mirrors ``dtos.Device`` (``dtos.FromDeviceModelToDTO``).
    """
    dto: Dict[str, Any] = {"name": device.name}
    if device.id:
        dto["id"] = device.id
    if device.description:
        dto["description"] = device.description
    if device.admin_state:
        dto["adminState"] = str(device.admin_state)
    if device.operating_state:
        dto["operatingState"] = device.operating_state
    if device.labels:
        dto["labels"] = list(device.labels)
    if device.location is not None:
        dto["location"] = device.location
    if device.service_name:
        dto["serviceName"] = device.service_name
    if device.profile_name:
        dto["profileName"] = device.profile_name
    if device.auto_events:
        dto["autoEvents"] = [_auto_event_to_dto(e) for e in device.auto_events]
    dto["protocols"] = {k: dict(v) for k, v in device.protocols.items()}
    if device.last_connected:
        dto["lastConnected"] = device.last_connected
    if device.last_reported:
        dto["lastReported"] = device.last_reported
    if device.tags:
        dto["tags"] = dict(device.tags)
    if device.properties:
        dto["properties"] = dict(device.properties)
    return dto


def add_device_request(device: Device) -> Dict[str, Any]:
    """Build the Core AdminDevice request ``{"device": {...}}`` body."""
    return _request({"device": device_to_dto(device)})


def device_service_to_dto(name: str, base_address: str, admin_state: str,
                          labels: List[str], properties: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a DeviceService onto Core Metadata (``dtos.DeviceService``).

    Mirrors Go ``selfRegister`` which builds a DeviceService with ``Name``, ``Labels``,
    ``BaseAddress``, ``AdminState`` and an empty ``Properties`` map.
    """
    dto: Dict[str, Any] = {"name": name}
    if base_address:
        dto["baseAddress"] = base_address
    if labels:
        dto["labels"] = list(labels)
    if admin_state := str(admin_state or "UNLOCKED"):
        dto["adminState"] = admin_state
    dto["properties"] = dict(properties or {})
    return dto


def add_device_service_request(service: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a DeviceService body in the Core AdminService request envelope."""
    return _request({"service": service})


def provision_watcher_to_dto(watcher: ProvisionWatcher) -> Dict[str, Any]:
    """Serialize a ProvisionWatcher into the Core ``provisionwatcher`` body dict.

    Mirrors ``dtos.ProvisionWatcher``.  Since EdgeX 4.0 the ProvisionWatcher carries a
    ``discoveredDevice`` child object (with its own ``profileName`` / ``adminState``) which
    Core Metadata validates; the model keeps a single ``profile_name`` / ``admin_state`` so the
    child is derived from them (matching the Go device-simple provision watcher file).
    """
    dto: Dict[str, Any] = {"name": watcher.name}
    if watcher.id:
        dto["id"] = watcher.id
    if watcher.description:
        dto["description"] = watcher.description
    if watcher.service_name:
        dto["serviceName"] = watcher.service_name
    if watcher.labels:
        dto["labels"] = list(watcher.labels)
    if watcher.identifiers:
        dto["identifiers"] = dict(watcher.identifiers)
    if watcher.blocking_identifiers:
        dto["blockingIdentifiers"] = {
            k: list(v) for k, v in watcher.blocking_identifiers.items()}
    if watcher.admin_state:
        dto["adminState"] = str(watcher.admin_state)
    if watcher.profile_name:
        dto["profileName"] = watcher.profile_name
    discovered: Dict[str, Any] = {"adminState": str(watcher.admin_state or "UNLOCKED")}
    if watcher.profile_name:
        discovered["profileName"] = watcher.profile_name
    dto["discoveredDevice"] = discovered
    return dto


def add_provision_watcher_request(watcher: ProvisionWatcher) -> Dict[str, Any]:
    """Build the Core ProvisionWatcher request ``{"provisionwatcher": {...}}``."""
    return _request({"provisionwatcher": provision_watcher_to_dto(watcher)})