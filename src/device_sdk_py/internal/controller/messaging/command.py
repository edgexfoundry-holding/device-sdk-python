# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
Command request subscription - ported from
`device-sdk-go/internal/controller/messaging/command.go` (`SubscribeCommands`).

Subscribes to `<basePrefix>/command/request/<serviceName>/#`, parses device/command/method
from the topic, enforces a semaphore (default 32 concurrent), invokes
`application.command_read` / `application.command_write`, publishes response to
`<basePrefix>/response/<serviceName>/<requestId>`, and optionally re-publishes the
Event via `send_event` when `ds-pushevent=true`.
"""

from __future__ import annotations

import logging
import threading
import urllib.parse
from http import HTTPStatus
from typing import Any, Callable, Dict, List, Optional

from device_sdk_py.internal.controller.messaging.client import (
    MessageClient,
    MessageEnvelope,
    TopicMessageQueue,
    CONTENT_TYPE_JSON,
)
from device_sdk_py.internal.controller.messaging.publish import (
    build_event_publish_topic,
)
from device_sdk_py.internal.application import command_read, command_write
from device_sdk_py.internal.common.consts import (
    COMMAND_REQUEST_SUBSCRIBE_TOPIC,
    COMMAND,
    DEVICE_SERVICE_EVENT_PREFIX,
    EVENTS_PUBLISH_TOPIC,
    NAME,
    PUSH_EVENT,
    REGEX_COMMAND,
    RESPONSE_TOPIC,
    RETURN_EVENT,
    VALUE_FALSE,
    VALUE_TRUE,
)
from device_sdk_py.internal.common.utils import EdgexError

# Default max concurrent command requests (mirrors Go constant)
DEFAULT_MAX_CONCURRENT_COMMANDS = 32


def _parse_command_topic(
    topic: str,
    base_topic_prefix: str,
    service_name: str,
) -> Optional[Dict[str, str]]:
    """
    Parse the command request topic to extract device name, command name, and method.

    Expected topic format: `<basePrefix>/command/request/<serviceName>/<deviceName>/<commandName>/<method>`
    """
    prefix = f"{base_topic_prefix}/{COMMAND_REQUEST_SUBSCRIBE_TOPIC}/{service_name}/"
    if not topic.startswith(prefix):
        return None
    remainder = topic[len(prefix):]
    parts = remainder.split("/")
    if len(parts) != 3:
        return None
    device_name = urllib.parse.unquote(parts[0])
    command_name = urllib.parse.unquote(parts[1])
    method = parts[2].upper()
    if method not in ("GET", "SET"):
        return None
    return {
        "device_name": device_name,
        "command_name": command_name,
        "method": method,
    }


def _build_response_topic(
    base_topic_prefix: str,
    service_name: str,
    request_id: str,
) -> str:
    """Build the command response topic: `<basePrefix>/response/<serviceName>/<requestId>`."""
    return f"{base_topic_prefix}/{RESPONSE_TOPIC}/{service_name}/{request_id}"


def _filter_query_params(query_params: Dict[str, str]) -> tuple[str, Dict[str, bool]]:
    """
    Filter SDK reserved query parameters from the command request.
    Mirrors Go `filterQueryParams` in command.go.
    """
    raw_parts: List[str] = []
    reserved: Dict[str, bool] = {
        PUSH_EVENT: False,
        RETURN_EVENT: True,
        REGEX_COMMAND: True,
    }
    for k, v in query_params.items():
        if k == PUSH_EVENT and v == VALUE_TRUE:
            reserved[PUSH_EVENT] = True
            continue
        if k == RETURN_EVENT and v == VALUE_FALSE:
            reserved[RETURN_EVENT] = False
            continue
        if k == REGEX_COMMAND and v == VALUE_FALSE:
            reserved[REGEX_COMMAND] = False
            continue
        if k.startswith("ds-"):
            continue
        raw_parts.append(f"{k}={v}")
    return "&".join(raw_parts), reserved


def subscribe_commands(
    ctx_cancel: threading.Event,
    client: MessageClient,
    base_topic_prefix: str,
    service_name: str,
    driver: Any,
    configuration: Any,
    device_service: Any,
    logger: Optional[logging.Logger] = None,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT_COMMANDS,
) -> threading.Thread:
    """
    Start a background thread that subscribes to command requests and processes them.

    Args:
        ctx_cancel: Event to signal shutdown.
        client: Connected MessageClient.
        base_topic_prefix: EdgeX base topic prefix (e.g., "edgex").
        service_name: This device service's name.
        driver: ProtocolDriver instance.
        configuration: Service configuration.
        device_service: DeviceService instance (for admin state check).
        logger: Optional logger.
        max_concurrent: Maximum concurrent command requests (semaphore).

    Returns:
        The started thread.
    """
    log = logger or logging.getLogger(__name__)

    request_topic = f"{base_topic_prefix}/{COMMAND_REQUEST_SUBSCRIBE_TOPIC}/{service_name}/#"
    log.info("Subscribing to command requests on topic: %s", request_topic)

    response_topic_prefix = f"{base_topic_prefix}/{RESPONSE_TOPIC}/{service_name}"
    log.info("Responses to command requests will be published on topic: %s/<requestId>", response_topic_prefix)

    msg_queue: "queue.Queue[MessageEnvelope]" = __import__("queue").Queue()
    err_queue: "queue.Queue[str]" = __import__("queue").Queue()

    topic_queue = TopicMessageQueue(topic=request_topic, message_queue=msg_queue)

    sem = threading.Semaphore(max_concurrent)

    def _publish_error_response(
        request_id: str,
        correlation_id: str,
        error_msg: str,
        response_topic: str,
        encoding: str = CONTENT_TYPE_JSON,
    ) -> None:
        """Publish an error response envelope."""
        env = MessageEnvelope(
            correlation_id=correlation_id,
            request_id=request_id,
            content_type=encoding,
            payload={"apiVersion": "v3", "errorCode": 500, "message": error_msg},
        )
        try:
            client.publish(env, response_topic)
        except Exception as exc:
            log.error("Failed to publish error response: %s", exc)

    def _process_request(envelope: MessageEnvelope) -> None:
        """Process a single command request envelope."""
        parsed = _parse_command_topic(envelope.received_topic, base_topic_prefix, service_name)
        if parsed is None:
            log.error("Failed to parse command request topic: %s", envelope.received_topic)
            return

        device_name = parsed["device_name"]
        command_name = parsed["command_name"]
        method = parsed["method"]
        is_get = method == "GET"

        response_topic = _build_response_topic(base_topic_prefix, service_name, envelope.request_id)

        # Non-blocking semaphore acquire
        if not sem.acquire(blocking=False):
            log.warning("Command in-flight limit (%d) reached; rejecting %s request for %s/%s",
                        max_concurrent, method, device_name, command_name)
            _publish_error_response(
                envelope.request_id, envelope.correlation_id,
                "device service busy: too many concurrent commands",
                response_topic,
            )
            return

        def worker():
            try:
                raw_query, reserved = _filter_query_params(envelope.query_params or {})

                correlation_id = envelope.correlation_id

                if is_get:
                    # GET command
                    regex_cmd = reserved[REGEX_COMMAND]
                    try:
                        event = command_read(
                            device_name, correlation_id, command_name,
                            driver=driver,
                            configuration=configuration,
                            attributes=raw_query,
                            regex_cmd=regex_cmd,
                            device_service=device_service,
                            logger=log,
                        )
                    except EdgexError as exc:
                        log.error("Failed to process get device command %s for device %s: %s",
                                  command_name, device_name, exc)
                        _publish_error_response(envelope.request_id, correlation_id,
                                                str(exc), response_topic)
                        return

                    # Build response
                    if event is not None and reserved[RETURN_EVENT]:
                        from device_sdk_py.internal.controller.http._utils import event_to_dict
                        resp = {
                            "apiVersion": "v3",
                            "statusCode": HTTPStatus.OK,
                            "event": event_to_dict(event),
                        }
                        encoding = CONTENT_TYPE_JSON
                        # Check for binary reading -> CBOR
                        for r in event.readings:
                            if r.value_type == "Binary":
                                import cbor2
                                encoding = "application/cbor"
                                break
                    else:
                        resp = {"apiVersion": "v3", "statusCode": HTTPStatus.OK}
                        encoding = CONTENT_TYPE_JSON

                    env = MessageEnvelope(
                        correlation_id=correlation_id,
                        request_id=envelope.request_id,
                        content_type=encoding,
                        payload=resp,
                    )
                    try:
                        client.publish(env, response_topic)
                    except Exception as exc:
                        log.error("Failed to publish command response: %s", exc)
                        return

                    # If ds-pushevent=true, also publish event to events topic
                    if event is not None and reserved[PUSH_EVENT]:
                        from device_sdk_py.internal.controller.messaging.publish import publish_event
                        try:
                            publish_event(
                                client=client,
                                event=event,
                                correlation_id=correlation_id,
                                base_topic_prefix=base_topic_prefix,
                                service_name=service_name,
                                profile_name=event.profile_name,
                                device_name=event.device_name,
                                source_name=event.source_name,
                                max_event_size=0,
                                logger=log,
                            )
                        except Exception as exc:
                            log.error("Failed to publish event via ds-pushevent: %s", exc)

                else:
                    # SET command
                    request_payload = envelope.payload
                    if not isinstance(request_payload, dict):
                        log.error("Invalid set command payload: not a dict")
                        _publish_error_response(envelope.request_id, correlation_id,
                                                "invalid request payload", response_topic)
                        return

                    try:
                        event = command_write(
                            device_name, correlation_id, command_name,
                            driver=driver,
                            configuration=configuration,
                            requests=request_payload,
                            attributes=raw_query,
                            device_service=device_service,
                            logger=log,
                        )
                    except EdgexError as exc:
                        log.error("Failed to process set device command %s for device %s: %s",
                                  command_name, device_name, exc)
                        _publish_error_response(envelope.request_id, correlation_id,
                                                str(exc), response_topic)
                        return

                    # BaseResponse for SET
                    resp = {"apiVersion": "v3", "statusCode": HTTPStatus.OK}
                    env = MessageEnvelope(
                        correlation_id=correlation_id,
                        request_id=envelope.request_id,
                        content_type=CONTENT_TYPE_JSON,
                        payload=resp,
                    )
                    try:
                        client.publish(env, response_topic)
                    except Exception as exc:
                        log.error("Failed to publish set command response: %s", exc)
                        return

                    # If event produced, publish it
                    if event is not None:
                        from device_sdk_py.internal.controller.messaging.publish import publish_event
                        try:
                            publish_event(
                                client=client,
                                event=event,
                                correlation_id=correlation_id,
                                base_topic_prefix=base_topic_prefix,
                                service_name=service_name,
                                profile_name=event.profile_name,
                                device_name=event.device_name,
                                source_name=event.source_name,
                                max_event_size=0,
                                logger=log,
                            )
                        except Exception as exc:
                            log.error("Failed to publish event after set command: %s", exc)

            finally:
                sem.release()

        threading.Thread(target=worker, daemon=True).start()

    def run():
        log.debug("Command subscription thread started")
        client.subscribe([topic_queue], err_queue)

        while not ctx_cancel.is_set():
            try:
                # Check for subscription errors
                try:
                    err = err_queue.get_nowait()
                    log.error("Command subscription error: %s", err)
                except __import__("queue").Empty:
                    pass

                # Process incoming command request
                try:
                    envelope = msg_queue.get(timeout=0.5)
                except __import__("queue").Empty:
                    continue

                _process_request(envelope)

            except Exception as exc:
                log.exception("Unexpected error in command subscription loop: %s", exc)

        log.debug("Command subscription thread stopped")

    thread = threading.Thread(target=run, daemon=True, name="command-sub")
    thread.start()
    return thread