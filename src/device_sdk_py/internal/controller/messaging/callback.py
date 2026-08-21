# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
`device-sdk-go/internal/controller/messaging/callback.go` (`MetadataSystemEventsCallback`).

Subscribes to system event topics and dispatches to the appropriate handler:
- Device add/update/delete
- DeviceProfile update/delete
- ProvisionWatcher add/update/delete
- DeviceService update

Topics (per EdgeX v4, mirrors `MetadataSystemEventsCallback`):
- `<basePrefix>/system-events/core-metadata/+/+/<serviceName>/#` (Device, DeviceProfile, DeviceService)
- `<basePrefix>/system-events/core-metadata/deviceprofile/delete/#` (Profile delete special)
- Instance name scenario: `<basePrefix>/system-events/core-metadata/provisionwatcher/+/<baseServiceName>/#`
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from device_sdk_py.internal.controller.messaging.client import (
    MessageClient,
    TopicMessageQueue,
)
from device_sdk_py.internal.common.consts import (
    DEVICE_SYSTEM_EVENT_TYPE,
    DEVICE_PROFILE_SYSTEM_EVENT_TYPE,
    PROVISION_WATCHER_SYSTEM_EVENT_TYPE,
    DEVICE_SERVICE_SYSTEM_EVENT_TYPE,
    SYSTEM_EVENT_ACTION_ADD,
    SYSTEM_EVENT_ACTION_UPDATE,
    SYSTEM_EVENT_ACTION_DELETE,
    SYSTEM_EVENTS_PUBLISH_TOPIC,
    METADATA_SYSTEM_EVENT_SUBSCRIBE_TOPIC,
    CORE_METADATA_SERVICE_KEY,
)
from device_sdk_py.internal.common.utils import EdgexError


#: Maximum number of queued system events before the queue drops new messages.
_MAX_SYSTEM_EVENTS_QUEUE = 256


def _decode_system_event(envelope: "MessageEnvelope") -> Optional[Dict[str, Any]]:
    """Decode the SystemEvent from the message envelope payload."""
    payload = envelope.payload
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except Exception:
            return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    if isinstance(payload, dict):
        return payload
    return None


def _handle_device_system_event(
    sys_event: Dict[str, Any],
    service_name: str,
    add_device: Callable,
    update_device: Callable,
    delete_device: Callable,
    logger: logging.Logger,
) -> None:
    """Handle Device system event (add/update/delete)."""
    action = sys_event.get("action")
    details = sys_event.get("details", {})
    device_name = details.get("name", "")

    if not device_name:
        logger.error("Device system event missing device name: %s", sys_event)
        return

    try:
        if action == SYSTEM_EVENT_ACTION_ADD:
            add_device(details)
            logger.info("Added device %s from system event", device_name)
        elif action == SYSTEM_EVENT_ACTION_UPDATE:
            update_device(device_name, details)
            logger.info("Updated device %s from system event", device_name)
        elif action == SYSTEM_EVENT_ACTION_DELETE:
            delete_device(device_name)
            logger.info("Deleted device %s from system event", device_name)
        else:
            logger.warning("Unknown device system event action: %s", action)
    except Exception as exc:
        logger.error("Failed to handle device system event %s for %s: %s", action, device_name, exc)


def _handle_device_profile_system_event(
    sys_event: Dict[str, Any],
    service_name: str,
    update_profile: Callable,
    delete_profile: Callable,
    logger: logging.Logger,
) -> None:
    """Handle DeviceProfile system event (update/delete; add is no-op)."""
    action = sys_event.get("action")
    details = sys_event.get("details", {})
    profile_name = details.get("name", "")

    if not profile_name:
        logger.error("DeviceProfile system event missing profile name: %s", sys_event)
        return

    # Owner check: Core Metadata sends Profile Delete with owner=core-metadata
    owner = sys_event.get("owner")
    if action == SYSTEM_EVENT_ACTION_DELETE and owner != CORE_METADATA_SERVICE_KEY:
        logger.warning("Ignoring device-profile delete from non-core-metadata owner: %s", owner)
        return

    try:
        if action == SYSTEM_EVENT_ACTION_UPDATE:
            update_profile(details)
            logger.info("Updated device profile %s from system event", profile_name)
        elif action == SYSTEM_EVENT_ACTION_DELETE:
            delete_profile(profile_name)
            logger.info("Deleted device profile %s from system event", profile_name)
        elif action == SYSTEM_EVENT_ACTION_ADD:
            # Profile add is no-op for Device Service (per Go SDK)
            logger.debug("Ignored device profile add (no-op): %s", profile_name)
        else:
            logger.warning("Unknown device profile system event action: %s", action)
    except Exception as exc:
        logger.error("Failed to handle device profile system event %s for %s: %s", action, profile_name, exc)


def _handle_provision_watcher_system_event(
    sys_event: Dict[str, Any],
    service_name: str,
    add_watcher: Callable,
    update_watcher: Callable,
    delete_watcher: Callable,
    logger: logging.Logger,
) -> None:
    """Handle ProvisionWatcher system event (add/update/delete)."""
    action = sys_event.get("action")
    details = sys_event.get("details", {})
    watcher_name = details.get("name", "")

    if not watcher_name:
        logger.error("ProvisionWatcher system event missing watcher name: %s", sys_event)
        return

    try:
        if action == SYSTEM_EVENT_ACTION_ADD:
            add_watcher(details)
            logger.info("Added provision watcher %s from system event", watcher_name)
        elif action == SYSTEM_EVENT_ACTION_UPDATE:
            update_watcher(watcher_name, details)
            logger.info("Updated provision watcher %s from system event", watcher_name)
        elif action == SYSTEM_EVENT_ACTION_DELETE:
            delete_watcher(watcher_name)
            logger.info("Deleted provision watcher %s from system event", watcher_name)
        else:
            logger.warning("Unknown provision watcher system event action: %s", action)
    except Exception as exc:
        logger.error("Failed to handle provision watcher system event %s for %s: %s", action, watcher_name, exc)


def _handle_device_service_system_event(
    sys_event: Dict[str, Any],
    service_name: str,
    update_service: Callable,
    logger: logging.Logger,
) -> None:
    """Handle DeviceService system event (update only; add/delete are no-op)."""
    action = sys_event.get("action")
    details = sys_event.get("details", {})
    svc_name = details.get("name", "")

    if not svc_name or svc_name != service_name:
        return  # Ignore events for other services

    try:
        if action == SYSTEM_EVENT_ACTION_UPDATE:
            update_service(details)
            logger.info("Updated device service %s from system event", svc_name)
        elif action in (SYSTEM_EVENT_ACTION_ADD, SYSTEM_EVENT_ACTION_DELETE):
            # No-op per Go SDK
            logger.debug("Ignored device service %s (no-op): %s", action, svc_name)
        else:
            logger.warning("Unknown device service system event action: %s", action)
    except Exception as exc:
        logger.error("Failed to handle device service system event %s for %s: %s", action, svc_name, exc)


def subscribe_system_events(
    ctx_cancel: threading.Event,
    client: MessageClient,
    base_topic_prefix: str,
    service_name: str,
base_service_name: Optional[str], # service name without instance suffix
    add_device: Callable,
    update_device: Callable,
    delete_device: Callable,
    add_profile: Callable,
    update_profile: Callable,
    delete_profile: Callable,
    add_watcher: Callable,
    update_watcher: Callable,
    delete_watcher: Callable,
    update_service: Callable,
    logger: Optional[logging.Logger] = None,
) -> threading.Thread:
    """
    Start a background thread that subscribes to Metadata system events.

    Args:
        ctx_cancel: Event to signal shutdown.
        client: Connected MessageClient.
        base_topic_prefix: EdgeX base topic prefix (e.g., "edgex").
        service_name: This device service's full name (with instance if any).
        base_service_name: Service base name (without instance suffix) for provision watcher topic.
        add_device/update_device/delete_device: Callbacks for Device CRUD.
        add_profile/update_profile/delete_profile: Callbacks for DeviceProfile CRUD.
        add_watcher/update_watcher/delete_watcher: Callbacks for ProvisionWatcher CRUD.
        update_service: Callback for DeviceService update.
        logger: Optional logger.

    Returns:
        The started thread.
    """
    log = logger or logging.getLogger(__name__)

    # Build subscription topics
    topics: List[TopicMessageQueue] = []

    # Main system events topic:
    # <basePrefix>/system-events/core-metadata/+/+/<serviceName>/#
    main_topic = f"{base_topic_prefix}/{METADATA_SYSTEM_EVENT_SUBSCRIBE_TOPIC}/{service_name}/#"
    topics.append(TopicMessageQueue(topic=main_topic,
                                 message_queue=__import__("queue").Queue(maxsize=_MAX_SYSTEM_EVENTS_QUEUE)))
    log.info("Subscribing to system events on topic: %s", main_topic)

    # Profile delete special topic:
    # <basePrefix>/system-events/core-metadata/deviceprofile/delete/#
    profile_delete_topic = (
        f"{base_topic_prefix}/{SYSTEM_EVENTS_PUBLISH_TOPIC}/core-metadata/"
        f"{DEVICE_PROFILE_SYSTEM_EVENT_TYPE}/{SYSTEM_EVENT_ACTION_DELETE}/#"
    )
    topics.append(TopicMessageQueue(topic=profile_delete_topic,
                                 message_queue=__import__("queue").Queue(maxsize=_MAX_SYSTEM_EVENTS_QUEUE)))
    log.info("Subscribing to profile delete events on topic: %s", profile_delete_topic)

    # Instance name scenario: provision watcher topic uses base service name.
    # <basePrefix>/system-events/core-metadata/provisionwatcher/+/<baseServiceName>/#
    if base_service_name and base_service_name != service_name:
        pw_topic = (
            f"{base_topic_prefix}/{SYSTEM_EVENTS_PUBLISH_TOPIC}/core-metadata/"
            f"{PROVISION_WATCHER_SYSTEM_EVENT_TYPE}/+/{base_service_name}/#"
        )
        topics.append(TopicMessageQueue(topic=pw_topic,
                                 message_queue=__import__("queue").Queue(maxsize=_MAX_SYSTEM_EVENTS_QUEUE)))
        log.info("Subscribing to provision watcher events on topic: %s", pw_topic)

    error_queue: "queue.Queue[str]" = __import__("queue").Queue()
    client.subscribe(topics, error_queue)

    def run():
        log.debug("System events subscription thread started")

        while not ctx_cancel.is_set():
            try:
                # Check for subscription errors
                try:
                    err = error_queue.get_nowait()
                    log.error("System events subscription error: %s", err)
                except __import__("queue").Empty:
                    pass

                # Process events from any subscribed topic
                for topic_queue in topics:
                    try:
                        envelope = topic_queue.message_queue.get(timeout=0.1)
                    except __import__("queue").Empty:
                        continue

                    sys_event = _decode_system_event(envelope)
                    if sys_event is None:
                        log.error("Failed to decode system event from topic %s", envelope.received_topic)
                        continue

                    event_type = sys_event.get("type")
                    owner = sys_event.get("owner")

                    log.debug("System event received: type=%s, action=%s, owner=%s, topic=%s",
                              event_type, sys_event.get("action"), owner, envelope.received_topic)

                    # Owner validation
                    if owner == CORE_METADATA_SERVICE_KEY:
                        if event_type != DEVICE_PROFILE_SYSTEM_EVENT_TYPE and sys_event.get("action") != SYSTEM_EVENT_ACTION_DELETE:
                            log.error("Only device profile delete supported from owner %s", owner)
                            continue
                    elif owner != service_name and owner != base_service_name:
                        log.error("Unmatched system event owner %s (service=%s, base=%s)",
                                  owner, service_name, base_service_name)
                        continue

                    # Dispatch by type
                    if event_type == DEVICE_SYSTEM_EVENT_TYPE:
                        _handle_device_system_event(
                            sys_event, service_name,
                            add_device, update_device, delete_device, log)
                    elif event_type == DEVICE_PROFILE_SYSTEM_EVENT_TYPE:
                        _handle_device_profile_system_event(
                            sys_event, service_name,
                            update_profile, delete_profile, log)
                    elif event_type == PROVISION_WATCHER_SYSTEM_EVENT_TYPE:
                        _handle_provision_watcher_system_event(
                            sys_event, service_name,
                            add_watcher, update_watcher, delete_watcher, log)
                    elif event_type == DEVICE_SERVICE_SYSTEM_EVENT_TYPE:
                        _handle_device_service_system_event(
                            sys_event, service_name, update_service, log)
                    else:
                        log.warning("Unknown system event type: %s", event_type)

            except Exception as exc:
                log.exception("Unexpected error in system events loop: %s", exc)

        log.debug("System events subscription thread stopped")

    thread = threading.Thread(target=run, daemon=True, name="system-events-sub")
    thread.start()
    return thread


def _get_base_service_name(service_name: str) -> str:
    """
    Extract base service name (without instance suffix).
    Instance suffix format: "-<instance>" where instance is numeric or alphanumeric.
    """
    # EdgeX convention: service name with instance is like "device-simple-1"
    # We strip the last hyphen-separated segment if it looks like an instance id
    parts = service_name.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return service_name
