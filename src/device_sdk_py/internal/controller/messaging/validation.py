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

This module subscribes to that topic, invokes the ProtocolDriver's ``validate_device`` and
publishes the validation result back - validation.go`` loop. The wire
format is a ``MessageEnvelope`` JSON object with an inline JSON ``payload`` (the
``AddDeviceRequest``) and the response envelope carries an empty payload and ``errorCode=0``.

The MQTT transport uses ``paho-mqtt`` (already a project dependency), mirroring the
app-functions-sdk-python ``messaging/mqtt/client.py`` behaviour.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

import paho.mqtt.client as mqtt

from ...provision import _build_device

__all__ = ["DeviceValidationHandler", "subscribe_device_validation"]

_LOGGER = logging.getLogger(__name__)


class DeviceValidationHandler:
    """Subscribes to Core Metadata's device-validation topic and answers each request.

    Args:
        service_name: The name of this Device Service (topic namespace segment).
        driver: The ProtocolDriver whose ``validate_device`` is invoked for each request.
        base_topic_prefix: The EdgeX base topic prefix (default ``edgex``).
        broker_host: The MQTT broker host.
        broker_port: The MQTT broker port.
        logger: Optional logger; defaults to the module logger.
    """

    def __init__(self, service_name: str, driver: Any, base_topic_prefix: str = "edgex",
                 broker_host: str = "127.0.0.1", broker_port: int = 1883,
                 logger: Optional[logging.Logger] = None) -> None:
        self.service_name = service_name
        self.driver = driver
        self.base_topic_prefix = base_topic_prefix.strip("/")
        self.broker_host = broker_host
        self.broker_port = int(broker_port)
        self._logger = logger or _LOGGER
        self._client: Optional[mqtt.Client] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def request_topic(self) -> str:
        return f"{self.base_topic_prefix}/{self.service_name}/validate/device"

    def _response_topic(self, request_id: str) -> str:
        return f"{self.base_topic_prefix}/response/{self.service_name}/{request_id}"

    def start(self) -> None:
        """Connect to the broker and subscribe to the validation topic in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="device-validation-subscriber", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the subscriber thread and disconnect from the broker."""
        self._stop_event.set()
        if self._client is not None:
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"device-validation-{self.service_name}",
            clean_session=True,
            reconnect_on_failure=True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        try:
            self._client.connect(self.broker_host, self.broker_port, keepalive=60)
            self._client.loop_start()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("failed to connect to MQTT broker %s:%s for device "
                                 "validation: %s", self.broker_host, self.broker_port, exc)
            return
        self._logger.info("Subscribed to device validation requests on topic: %s",
                          self.request_topic)
        while not self._stop_event.wait(timeout=0.5):
            pass
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    def _on_connect(self, client: mqtt.Client, userdata: Any,
                    flags: dict, reason_code: Any, properties: Any = None) -> None:
        if reason_code == 0 or int(reason_code) == 0:
            client.subscribe(self.request_topic, qos=0)
            self._logger.debug("subscribed to %s", self.request_topic)
        else:
            self._logger.warning("MQTT connect failed: %s", reason_code)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        self._logger.debug("Device validation request received on topic: %s", message.topic)
        try:
            envelope = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._logger.error("failed to decode validation request envelope: %s", exc)
            return

        request_id = envelope.get("requestID", "")
        correlation_id = envelope.get("correlationID", "")
        payload = envelope.get("payload")
        try:
            device_dto = payload.get("device") if isinstance(payload, dict) else None
            if device_dto is None:
                raise ValueError("payload is not an AddDeviceRequest")
            device = _build_device(device_dto)
            self.driver.validate_device(device)
        except Exception as exc: # noqa: BLE001
            self._logger.error("Device validation failed: %s", exc)
            self._publish_response(request_id, correlation_id, error_code=str(exc))
            return

        self._publish_response(request_id, correlation_id, error_code="")
        self._logger.debug("Device validation response published for request %s", request_id)

    def _publish_response(self, request_id: str, correlation_id: str,
                          error_code: str = "") -> None:
        """Publish a validation result envelope.

        Mirrors ``NewMessageEnvelopeWithError``.
        """
        if self._client is None or not request_id:
            return
        envelope = {
            "apiVersion": "v3",
            "receivedTopic": "",
            "correlationID": correlation_id,
            "requestID": request_id,
            "errorCode": error_code if error_code else 0,
            "payload": "",
            "contentType": "application/json",
        }
        topic = self._response_topic(request_id)
        try:
            self._client.publish(topic, json.dumps(envelope), qos=0)
        except Exception as exc: # noqa: BLE001
            self._logger.error("failed to publish device validation response to %s: %s",
                               topic, exc)


def subscribe_device_validation(service_name: str, driver: Any,
                                base_topic_prefix: str = "edgex",
                                broker_host: str = "127.0.0.1", broker_port: int = 1883,
                                logger: Optional[logging.Logger] = None) -> DeviceValidationHandler:
    """Create and start a `DeviceValidationHandler` (Python counterpart of Go
    ``messaging.SubscribeDeviceValidation``)."""
    handler = DeviceValidationHandler(
        service_name=service_name, driver=driver,
        base_topic_prefix=base_topic_prefix,
        broker_host=broker_host, broker_port=broker_port, logger=logger)
    handler.start()
    return handler
