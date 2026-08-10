# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
Device validation request handler - ported from ``device-sdk-go/internal/controller/
messaging/validation.go`` (``SubscribeDeviceValidation``).

When Core Metadata adds or updates a Device it publishes an ``AddDeviceRequest`` envelope on
the topic ``<baseTopicPrefix>/<serviceName>/validate/device`` and waits for a response on
``<baseTopicPrefix>/response/<serviceName>/<requestId>``. Without a subscriber the
request times out and Core Metadata returns HTTP 503 "request timeout" for the device
create/update call.

This module subscribes to that topic using the shared ``MessageClient`` (so auth / TLS
configuration is honoured), invokes the ProtocolDriver's ``validate_device`` and publishes
the validation result back - mirroring the Go ``validation.go`` loop. The wire format is a
``MessageEnvelope`` JSON object with an inline JSON ``payload`` (the ``AddDeviceRequest``);
the success response envelope carries an empty payload and ``errorCode=0``, the error
response carries ``errorCode=1`` with the error message as a ``text/plain`` payload.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Optional

from ...provision import _build_device
from ...controller.messaging.client import (
    MessageClient,
    MessageEnvelope,
    TopicMessageQueue,
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_TEXT,
)

__all__ = ["DeviceValidationHandler", "subscribe_device_validation"]

_LOGGER = logging.getLogger(__name__)


class DeviceValidationHandler:
    """Subscribes to Core Metadata's device-validation topic and answers each request.

    Args:
        service_name: The name of this Device Service (topic namespace segment).
        driver: The ProtocolDriver whose ``validate_device`` is invoked for each request.
        base_topic_prefix: The EdgeX base topic prefix (default ``edgex``).
        client: The connected MessageClient used to subscribe / publish.
        logger: Optional logger; defaults to the module logger.
    """

    def __init__(self, service_name: str, driver: Any, base_topic_prefix: str = "edgex",
                 client: Optional[MessageClient] = None,
                 logger: Optional[logging.Logger] = None) -> None:
        self.service_name = service_name
        self.driver = driver
        self.base_topic_prefix = base_topic_prefix.strip("/")
        self._client: Optional[MessageClient] = client
        self._logger = logger or _LOGGER
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def request_topic(self) -> str:
        return f"{self.base_topic_prefix}/{self.service_name}/validate/device"

    def _response_topic(self, request_id: str) -> str:
        return f"{self.base_topic_prefix}/response/{self.service_name}/{request_id}"

    def start(self) -> None:
        """Subscribe to the validation topic and process requests in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="device-validation-subscriber", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the subscriber thread and unsubscribe from the validation topic."""
        self._stop_event.set()
        if self._client is not None:
            try:
                self._client.unsubscribe([self.request_topic])
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        if self._client is None:
            return
        msg_queue: "queue.Queue[MessageEnvelope]" = queue.Queue()
        err_queue: "queue.Queue[str]" = queue.Queue()
        try:
            self._client.subscribe(
                [TopicMessageQueue(self.request_topic, msg_queue)], err_queue)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("failed to subscribe to device validation topic %s: %s",
                                 self.request_topic, exc)
            return
        self._logger.info("Subscribed to device validation requests on topic: %s",
                          self.request_topic)
        while not self._stop_event.is_set():
            try:
                err = err_queue.get_nowait()
                self._logger.error("Device validation subscription error: %s", err)
            except queue.Empty:
                pass
            try:
                envelope = msg_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process_envelope(envelope)
            except Exception as exc:  # noqa: BLE001
                self._logger.exception(
                    "Unexpected error handling device validation request: %s", exc)

    def _process_envelope(self, envelope: MessageEnvelope) -> None:
        self._logger.debug("Device validation request received on topic: %s",
                           envelope.received_topic)
        request_id = envelope.request_id
        correlation_id = envelope.correlation_id
        payload = envelope.payload
        try:
            device_dto = payload.get("device") if isinstance(payload, dict) else None
            if device_dto is None:
                raise ValueError("payload is not an AddDeviceRequest")
            device = _build_device(device_dto)
            self.driver.validate_device(device)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Device validation failed: %s", exc)
            self._publish_response(request_id, correlation_id, error_msg=str(exc))
            return

        self._publish_response(request_id, correlation_id)
        self._logger.debug("Device validation response published for request %s", request_id)

    def _publish_response(self, request_id: str, correlation_id: str,
                          error_msg: str = "") -> None:
        """Publish a validation result envelope.

        Mirrors ``NewMessageEnvelopeForResponse`` (success, empty payload) and
        ``NewMessageEnvelopeWithError`` (errorCode=1, text/plain error payload).
        """
        if self._client is None or not request_id:
            return
        if error_msg:
            envelope = MessageEnvelope(
                correlation_id=correlation_id,
                request_id=request_id,
                error_code=1,
                payload=error_msg,
                content_type=CONTENT_TYPE_TEXT,
            )
        else:
            envelope = MessageEnvelope(
                correlation_id=correlation_id,
                request_id=request_id,
                error_code=0,
                payload=None,
                content_type=CONTENT_TYPE_JSON,
            )
        topic = self._response_topic(request_id)
        try:
            self._client.publish(envelope, topic)
        except Exception as exc:  # noqa: BLE001
            self._logger.error("failed to publish device validation response to %s: %s",
                               topic, exc)


def subscribe_device_validation(service_name: str, driver: Any,
                                base_topic_prefix: str = "edgex",
                                client: Optional[MessageClient] = None,
                                logger: Optional[logging.Logger] = None) -> DeviceValidationHandler:
    """Create and start a `DeviceValidationHandler` (Python counterpart of Go
    ``messaging.SubscribeDeviceValidation``)."""
    handler = DeviceValidationHandler(
        service_name=service_name, driver=driver,
        base_topic_prefix=base_topic_prefix,
        client=client, logger=logger)
    handler.start()
    return handler
