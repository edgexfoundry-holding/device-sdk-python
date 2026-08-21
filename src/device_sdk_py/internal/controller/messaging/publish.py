# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
`device-sdk-go/internal/common/utils.go` (`SendEvent`).

Builds the EdgeX v4 publish topic, chooses JSON/CBOR encoding, wraps the
Event in an AddEventRequest envelope, and enforces MaxEventSize.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import cbor2

from device_sdk_py.models import VALUETYPE_BINARY
from device_sdk_py.internal.transformer.transform import Event, Reading
from device_sdk_py.internal.clients.metrics import MetricsManager
from device_sdk_py.internal.controller.messaging.client import (
    MessageClient,
    MessageEnvelope,
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_CBOR,
    marshal_payload,
)

__all__ = [
    "build_event_publish_topic",
    "build_system_event_publish_topic",
    "publish_event",
    "publish_system_event",
    "create_add_event_request",
    "encode_event_request",
    "DEFAULT_MAX_EVENT_SIZE",
]

# Default max event size (bytes) - mirrors Go constant
DEFAULT_MAX_EVENT_SIZE = 0 # 0 = unlimited

# Topic path segments (mirrors go-mod-core-contracts/common)
EVENTS_PUBLISH_TOPIC = "events"
DEVICE_SERVICE_EVENT_PREFIX = "device"
SYSTEM_EVENTS_PUBLISH_TOPIC = "system-events"
DEVICE_SYSTEM_EVENT_TYPE = "device"
DEVICE_PROFILE_SYSTEM_EVENT_TYPE = "deviceprofile"
PROVISION_WATCHER_SYSTEM_EVENT_TYPE = "provisionwatcher"
DEVICE_SERVICE_SYSTEM_EVENT_TYPE = "deviceservice"
SYSTEM_EVENT_ACTION_ADD = "add"
SYSTEM_EVENT_ACTION_UPDATE = "update"
SYSTEM_EVENT_ACTION_DELETE = "delete"
# Per EdgeX v4.0.2, progress actions are "discovery", "profilescan", or "custom"
# The legacy "progress" action is deprecated
SYSTEM_EVENT_ACTION_PROGRESS = "progress"  # deprecated, kept for backward compat
SYSTEM_EVENT_ACTION_DISCOVERY = "discovery"
SYSTEM_EVENT_ACTION_PROFILESCAN = "profilescan"


def build_event_publish_topic(
    base_topic_prefix: str,
    service_name: str,
    profile_name: str,
    device_name: str,
    source_name: str,
) -> str:
    """
    Build the EdgeX v4 event publish topic:
    `<baseTopicPrefix>/events/device/<serviceName>/<profileName>/<deviceName>/<sourceName>`

    With name field escaping.
    """
    # Simple path join; Go version does RFC3986 escaping for name fields.
    # For now we assume names are already valid topic segments (alphanumeric, dash, underscore).
    parts = [
        base_topic_prefix.strip("/"),
        EVENTS_PUBLISH_TOPIC,
        DEVICE_SERVICE_EVENT_PREFIX,
        service_name,
        profile_name,
        device_name,
        source_name,
    ]
    return "/".join(parts)


def build_system_event_publish_topic(
    base_topic_prefix: str,
    service_name: str,
    event_type: str,  # device, deviceprofile, provisionwatcher, deviceservice
    action: str,  # add, update, delete, progress
    owner: Optional[str] = None,
) -> str:
    """
    Build system event publish topic (mirrors Go ``PublishGenericSystemEvent``):
    `<baseTopicPrefix>/system-events/<source>/<eventType>/<action>/<owner>`
    where source and owner default to the device service name.
    """
    owner = owner or service_name
    parts = [
        base_topic_prefix.strip("/"),
        SYSTEM_EVENTS_PUBLISH_TOPIC,
        service_name,
        event_type,
        action,
        owner,
    ]
    return "/".join(parts)


def _event_has_binary_reading(event: Event) -> bool:
    """Return True if any reading in the event is binary (triggers CBOR encoding)."""
    for reading in event.readings:
        if reading.value_type == VALUETYPE_BINARY:
            return True
    return False


def create_add_event_request(event: Event) -> Dict[str, Any]:
    """Create the EdgeX v3 AddEventRequest DTO as a dict (mirrors `requests.NewAddEventRequest`)."""
    # The request wraps the Event directly under "event" key
    return {"apiVersion": "v3", "event": _event_to_dict(event)}


def _event_to_dict(event: Event) -> Dict[str, Any]:
    """Serialize Event to dict compatible with go-mod-core-contracts v3 DTO (omitempty)."""
    def omit_empty(d: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}

    readings = []
    for r in event.readings:
        rd = omit_empty({
            "id": r.reading_id,
            "origin": r.origin,
            "deviceName": r.device_name,
            "resourceName": r.resource_name,
            "profileName": r.profile_name,
            "valueType": r.value_type,
            "units": r.units,
            "value": r.value,
            "binaryValue": r.binary_value,
            "objectValue": r.object_value,
            "mediaType": r.media_type,
            "tags": r.tags,
        })
        readings.append(rd)

    return omit_empty({
        "id": event.event_id,
        "deviceName": event.device_name,
        "profileName": event.profile_name,
        "sourceName": event.source_name,
        "origin": event.origin,
        "readings": readings,
        "tags": event.tags,
    })


def encode_event_request(event: Event) -> tuple[bytes, str]:
    """
    Encode AddEventRequest to bytes, choosing CBOR if binary reading present else JSON.
    Returns (payload_bytes, content_type).
    """
    req = create_add_event_request(event)
    if _event_has_binary_reading(event):
        return cbor2.dumps(req), CONTENT_TYPE_CBOR
    return json.dumps(req, separators=(",", ":")).encode("utf-8"), CONTENT_TYPE_JSON


def _check_max_event_size(payload: bytes, max_event_size: int) -> None:
    """Raise ValueError if payload exceeds max_event_size (0 = unlimited)."""
    if max_event_size > 0 and len(payload) > max_event_size:
        raise ValueError(
            f"Event size {len(payload)} exceeds MaxEventSize {max_event_size}"
        )


def publish_event(
    client: MessageClient,
    event: Event,
    correlation_id: str,
    base_topic_prefix: str,
    service_name: str,
    profile_name: str,
    device_name: str,
    source_name: str,
    max_event_size: int = DEFAULT_MAX_EVENT_SIZE,
    logger: Optional[logging.Logger] = None,
    metrics_manager: Optional[MetricsManager] = None,
) -> None:
    """
    Publish an Event to the EdgeX message bus.

    :
    - builds topic `<baseTopicPrefix>/events/device/<svc>/<profile>/<device>/<source>`
    - encodes AddEventRequest (CBOR if binary reading, else JSON)
    - wraps in MessageEnvelope with correlation_id
    - enforces MaxEventSize
    - publishes via MessageClient
    - mirrors Go ``SendEvent``: increments the EventsSent / ReadingsSent metrics
    """
    log = logger or logging.getLogger(__name__)

    topic = build_event_publish_topic(
        base_topic_prefix, service_name, profile_name, device_name, source_name
    )

    payload_bytes, content_type = encode_event_request(event)

    # MaxEventSize check (mirrors Go PublishWithSizeLimit)
    _check_max_event_size(payload_bytes, max_event_size)

    envelope = MessageEnvelope(
        correlation_id=correlation_id,
        request_id=str(uuid.uuid4()),
        content_type=content_type,
        payload=payload_bytes,
    )

    log.debug(
        "Publishing event to topic %s (correlation-id: %s, size: %d, encoding: %s)",
        topic,
        correlation_id,
        len(payload_bytes),
        content_type,
    )

    client.publish(envelope, topic)

    # Go SendEvent increments these after a successful publish
    if metrics_manager is not None:
        metrics_manager.new_counter("EventsSent").inc(1)
        metrics_manager.new_counter("ReadingsSent").inc(len(event.readings))


def publish_system_event(
    client: MessageClient,
    service_name: str,
    event_type: str,  # device, deviceprofile, provisionwatcher, deviceservice
    action: str,  # add, update, delete, progress
    details: Any,
    correlation_id: Optional[str] = None,
    base_topic_prefix: str = "edgex",
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    Publish a SystemEvent to the EdgeX message bus.

    Topic: `<baseTopicPrefix>/system-events/<serviceName>/<eventType>/<action>/<serviceName>`
    Payload: SystemEvent DTO (v3), mirroring `go-mod-core-contracts` ``dtos.SystemEvent``
    (`apiVersion`, `type`, `action`, `source`, `owner`, `tags`, `details`, `timestamp`).
    """
    log = logger or logging.getLogger(__name__)

    topic = build_system_event_publish_topic(
        base_topic_prefix, service_name, event_type, action
    )

    # Build SystemEvent DTO (mirrors Go `dtos.NewSystemEvent`: source = owner = service name)
    sys_event = {
        "apiVersion": "v3",
        "type": event_type,
        "action": action,
        "source": service_name,
        "owner": service_name,
        "tags": None,
        "details": details,
        "timestamp": int(1e9 * __import__("time").time()),  # nanoseconds
    }

    payload_bytes = json.dumps(sys_event, separators=(",", ":")).encode("utf-8")

    envelope = MessageEnvelope(
        correlation_id=correlation_id or str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        content_type=CONTENT_TYPE_JSON,
        payload=payload_bytes,
    )

    log.debug("Publishing system event to topic %s (type=%s, action=%s)", topic, event_type, action)

    client.publish(envelope, topic)
