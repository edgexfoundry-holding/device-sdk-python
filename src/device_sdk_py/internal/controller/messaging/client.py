# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""
EdgeX Device SDK messaging abstraction - adapted from
`app-functions-sdk-python/src/app_functions_sdk_py/messaging/`.

Provides `MessageClient` interface and MQTT implementation for publishing/subscribing
to the EdgeX message bus. The envelope format follows EdgeX v4 conventions:
ContentType (JSON/CBOR), Correlation-Id, Request-Id; Checksum removed.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import ssl
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import cbor2
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

# Content type constants (mirrors app-functions-sdk-python)
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_CBOR = "application/cbor"

# Default message bus values
DEFAULT_MESSAGEBUS_PROTOCOL = "tcp"
DEFAULT_MESSAGEBUS_HOST = "localhost"
DEFAULT_MESSAGEBUS_PORT = 1883
DEFAULT_MESSAGEBUS_TYPE = "mqtt"

# Optional parameter keys
USERNAME = "Username"
PASSWORD = "Password"
CLIENT_ID = "ClientId"
QOS = "Qos"
KEEP_ALIVE = "KeepAlive"
RETAINED = "Retained"
AUTO_RECONNECT = "AutoReconnect"
CLEAN_SESSION = "CleanSession"
CONNECT_TIMEOUT = "ConnectTimeout"
SKIP_CERT_VERIFY = "SkipCertVerify"
CERT_FILE = "CertFile"
KEY_FILE = "KeyFile"
CA_FILE = "CaFile"
KEY_PEM_BLOCK = "KeyPEMBlock"
CERT_PEM_BLOCK = "CertPEMBlock"
CA_PEM_BLOCK = "CaPEMBlock"

# Auth modes
AUTH_MODE_NONE = "none"
AUTH_MODE_USERNAME_PASSWORD = "usernamepassword"
AUTH_MODE_CLIENT_CERT = "clientcert"
AUTH_MODE_CACERT = "cacert"


@dataclass
class HostInfo:
    """Broker connection coordinates."""
    protocol: str = DEFAULT_MESSAGEBUS_PROTOCOL
    host: str = DEFAULT_MESSAGEBUS_HOST
    port: int = DEFAULT_MESSAGEBUS_PORT

    def get_host_url(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"

    def is_host_info_empty(self) -> bool:
        return self.host == "" or self.port == 0


@dataclass
class MessageBusConfig:
    """Complete message bus configuration (EdgeX v4 style)."""
    broker_info: HostInfo
    message_bus_type: str = DEFAULT_MESSAGEBUS_TYPE
    auth_mode: str = AUTH_MODE_NONE
    optional: Dict[str, str] = field(default_factory=dict)
    # v4 additions
    base_topic_prefix: str = "edgex"
publish_topic_prefix: str = "events" # "events" -> edgex/events/device/...
subscribe_topics: List[str] = field(default_factory=list) # for App Services


class TlsConfigurationOptions:
    """TLS configuration parsed from optional section."""
    def __init__(self, message_bus_config: MessageBusConfig) -> None:
        opt = message_bus_config.optional
        self.skip_cert_verify = opt.get(SKIP_CERT_VERIFY, "false").lower() == "true"
        self.cert_file = opt.get(CERT_FILE, "")
        self.key_file = opt.get(KEY_FILE, "")
        self.ca_file = opt.get(CA_FILE, "")
        self.cert_pem_block = opt.get(CERT_PEM_BLOCK, "")
        self.key_pem_block = opt.get(KEY_PEM_BLOCK, "")
        self.ca_pem_block = opt.get(CA_PEM_BLOCK, "")


def _parse_int(val: str, default: int) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _parse_bool(val: str, default: bool = False) -> bool:
    return str(val).lower() in ("true", "1", "yes", "on")


class MQTTClientOptions:
    """Resolved MQTT client options from MessageBusConfig."""
    def __init__(self, message_bus_config: MessageBusConfig) -> None:
        self.auth_mode = message_bus_config.auth_mode
        opt = message_bus_config.optional
        self.username = opt.get(USERNAME, "")
        self.password = opt.get(PASSWORD, "")
        self.client_id = opt.get(CLIENT_ID)
        self.qos = _parse_int(opt.get(QOS, "0"), 0)
        self.keep_alive = _parse_int(opt.get(KEEP_ALIVE, "60"), 60)
        self.retained = _parse_bool(opt.get(RETAINED, "false"))
        self.auto_reconnect = _parse_bool(opt.get(AUTO_RECONNECT, "true"))
        self.clean_session = _parse_bool(opt.get(CLEAN_SESSION, "true"))
        self.connect_timeout = _parse_int(opt.get(CONNECT_TIMEOUT, "5"), 5)
        self.tls_config = TlsConfigurationOptions(message_bus_config)


def _new_mqtt_client(client_options: MQTTClientOptions) -> mqtt.Client:
    """Create and configure a new Paho MQTT client."""
    client = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=client_options.client_id,
        clean_session=client_options.clean_session,
        reconnect_on_failure=client_options.auto_reconnect,
    )
    if client_options.auth_mode == AUTH_MODE_USERNAME_PASSWORD:
        client.username_pw_set(client_options.username, client_options.password)
    elif client_options.auth_mode == AUTH_MODE_CLIENT_CERT:
        client.tls_set(
            ca_certs=client_options.tls_config.ca_file,
            certfile=client_options.tls_config.cert_file,
            keyfile=client_options.tls_config.key_file,
            cert_reqs=ssl.CERT_REQUIRED if not client_options.tls_config.skip_cert_verify
            else ssl.CERT_NONE,
        )
    elif client_options.auth_mode == AUTH_MODE_CACERT:
        client.tls_set(
            ca_certs=client_options.tls_config.ca_file,
            cert_reqs=ssl.CERT_REQUIRED if not client_options.tls_config.skip_cert_verify
            else ssl.CERT_NONE,
        )
    return client


def _on_connect(
    client: mqtt.Client,
    userdata: Any,
    flags: dict,
    rc: int,
properties # pylint: disable=unused-argument
) -> None:
    """Re-register message callbacks after (re)connection."""
    for topic, callback in userdata.items():
        client.message_callback_add(topic, callback)


def _new_message_handler(message_queue: queue.Queue, error_queue: queue.Queue):
    """Create a message handler that decodes envelope and puts into the queue."""
    def on_message(
        client: mqtt.Client,  # pylint: disable=unused-argument
        userdata: Any,        # pylint: disable=unused-argument
        message: mqtt.MQTTMessage,
    ) -> None:
        try:
            env = _decode_message_envelope(message.payload)
        except Exception as ex:  # pylint: disable=broad-except
            error_queue.put(f"Failed to decode message into MessageEnvelope: {ex}")
            return
        env.received_topic = message.topic
        try:
            message_queue.put_nowait(env)
        except queue.Full:
            error_queue.put("Command queue is full; dropping message")
    return on_message


@dataclass
class MessageEnvelope:
    """EdgeX message envelope (v4: ContentType, Correlation-Id, Request-Id)."""
    received_topic: str = ""
    correlation_id: str = ""
    request_id: str = ""
    error_code: int = 0
    payload: Any = None
    content_type: str = CONTENT_TYPE_JSON
    query_params: Optional[Dict[str, str]] = None
    api_version: str = "v3"

    def to_json(self) -> str:
        """Serialize to JSON for publishing (payload base64 if bytes)."""
        data = asdict(self)
        # payload bytes -> base64 string for JSON transport
        if isinstance(data["payload"], bytes):
            data["payload"] = base64.b64encode(data["payload"]).decode("ascii")
        return json.dumps(data)

    @staticmethod
    def from_json(text: str) -> "MessageEnvelope":
        """Deserialize from JSON (base64 payload -> bytes)."""
        data = json.loads(text)
        if isinstance(data.get("payload"), str):
            try:
                data["payload"] = base64.b64decode(data["payload"])
            except Exception:
                pass
        return MessageEnvelope(**data)


def _decode_message_envelope(payload: bytes) -> MessageEnvelope:
    """Decode envelope from wire format (CBOR or JSON)."""
    # EdgeX v4 uses CBOR when ENV_MESSAGE_CBOR_ENCODE=true
    if os.getenv("EDGEX_MESSAGE_CBOR_ENCODE", "false").lower() == "true":
        data = cbor2.loads(payload)
        return MessageEnvelope(**data)
    # JSON path
    text = payload.decode("utf-8")
    return MessageEnvelope.from_json(text)


def marshal_payload(content_type: str, payload: Any) -> bytes:
    """Marshal payload to bytes according to content type."""
    if content_type == CONTENT_TYPE_CBOR:
        return cbor2.dumps(payload)
    if content_type == CONTENT_TYPE_JSON:
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")
    raise ValueError(f"Unsupported content type: {content_type}")


def unmarshal_payload(content_type: str, data: bytes, target_type: type) -> Any:
    """Unmarshal bytes to target type according to content type."""
    if content_type == CONTENT_TYPE_CBOR:
        obj = cbor2.loads(data)
    elif content_type == CONTENT_TYPE_JSON:
        obj = json.loads(data.decode("utf-8"))
    else:
        raise ValueError(f"Unsupported content type: {content_type}")
    if isinstance(obj, target_type):
        return obj
    # best-effort for dict->dataclass
    if hasattr(target_type, "__dataclass_fields__") and isinstance(obj, dict):
        return target_type(**obj)
    return obj


@dataclass
class TopicMessageQueue:
    """Subscription entry: topic + message queue."""
    topic: str
    message_queue: queue.Queue


class MessageClient(ABC):
    """Abstract message client interface."""

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to broker."""

    @abstractmethod
    def publish(self, message: MessageEnvelope, topic: str) -> None:
        """Publish a message envelope to the given topic."""

    @abstractmethod
    def subscribe(
        self,
        topic_queues: List[TopicMessageQueue],
        error_queue: queue.Queue,
    ) -> None:
        """Subscribe to multiple topics, each with its own message queue."""

    @abstractmethod
    def unsubscribe(self, topics: List[str]) -> None:
        """Unsubscribe from topics."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection and cleanup."""


class MqttMessageClient(MessageClient):
    """MQTT implementation using Paho MQTT (thread-based loop)."""

    def __init__(self, message_bus_config: MessageBusConfig) -> None:
        self._broker_info = message_bus_config.broker_info
        self._client_options = MQTTClientOptions(message_bus_config)
        self._existing_subscriptions: Dict[str, Any] = {}
        self._subscription_mutex = threading.Lock()
        self._client = _new_mqtt_client(self._client_options)
        self._client.on_connect = _on_connect
        self._client.user_data_set(self._existing_subscriptions)

    def connect(self) -> None:
        if self._client.is_connected():
            return
        try:
            rc = self._client.connect(
                self._broker_info.host,
                self._broker_info.port,
                self._client_options.keep_alive,
            )
            if rc == mqtt.MQTT_ERR_SUCCESS:
                self._client.loop_start()
            else:
                raise RuntimeError(f"MQTT connect failed with code {rc}")
        except ValueError as ve:
            raise RuntimeError(f"Failed to connect to MQTT broker: {ve}") from ve

    def publish(self, message: MessageEnvelope, topic: str) -> None:
        try:
            payload = message.to_json()
            self._client.publish(
                topic=topic,
                payload=payload,
                qos=self._client_options.qos,
                retain=self._client_options.retained,
            )
        except (ValueError, TypeError) as e:
            raise RuntimeError(f"Failed to publish message: {e}") from e

    def subscribe(
        self,
        topic_queues: List[TopicMessageQueue],
        error_queue: queue.Queue,
    ) -> None:
        with self._subscription_mutex:
            for topic_q in topic_queues:
                handler = _new_message_handler(topic_q.message_queue, error_queue)
                self._client.message_callback_add(topic_q.topic, handler)
                result, _ = self._client.subscribe(
                    topic_q.topic, self._client_options.qos
                )
                if result == 0:
                    self._existing_subscriptions[topic_q.topic] = handler

    def unsubscribe(self, topics: List[str]) -> None:
        with self._subscription_mutex:
            for topic in topics:
                if topic not in self._existing_subscriptions:
                    continue
                result, _ = self._client.unsubscribe(topic)
                if result == 0:
                    self._existing_subscriptions.pop(topic, None)

    def disconnect(self) -> None:
        if self._client.is_connected():
            self._client.disconnect()
            self._client.loop_stop()


def create_message_client(message_bus_config: MessageBusConfig) -> MessageClient:
    """Factory for message client based on config type."""
    if message_bus_config.message_bus_type.lower() == "mqtt":
        return MqttMessageClient(message_bus_config)
    raise ValueError(f"Unsupported message bus type: {message_bus_config.message_bus_type}")


def create_message_envelope(
    payload: Any,
    content_type: str = CONTENT_TYPE_JSON,
    correlation_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> MessageEnvelope:
    """Create a new envelope with generated IDs if not provided."""
    import uuid
    env = MessageEnvelope()
    env.correlation_id = correlation_id or str(uuid.uuid4())
    env.request_id = request_id or str(uuid.uuid4())
    env.content_type = content_type
    env.payload = payload
    # base64 encode payload if EDGEX_MSG_BASE64_PAYLOAD=true (for metrics etc)
    if os.getenv("EDGEX_MSG_BASE64_PAYLOAD", "false").lower() == "true":
        if isinstance(payload, bytes):
            env.payload = base64.b64encode(payload).decode("ascii")
        elif isinstance(payload, str):
            env.payload = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return env
