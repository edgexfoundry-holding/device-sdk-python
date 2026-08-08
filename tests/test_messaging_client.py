# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the messaging client layer (M10 cleanup).

Covers `internal/controller/messaging/client.py`:
- HostInfo / MessageBusConfig / option parsing
- TLS configuration, MQTT client construction
- MessageEnvelope serialization (JSON/base64/CBOR)
- MqttMessageClient publish/subscribe/connect/disconnect
- create_message_client factory + create_message_envelope
"""

from __future__ import annotations

import json
import os
import queue
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import device_sdk_py.internal.controller.messaging.client as cl  # noqa: E402
from device_sdk_py.internal.controller.messaging.client import (  # noqa: E402
    AUTH_MODE_CACERT,
    AUTH_MODE_CLIENT_CERT,
    AUTH_MODE_NONE,
    AUTH_MODE_USERNAME_PASSWORD,
    CONTENT_TYPE_CBOR,
    CONTENT_TYPE_JSON,
    HostInfo,
    MessageBusConfig,
    MessageEnvelope,
    MQTTClientOptions,
    MqttMessageClient,
    TlsConfigurationOptions,
    TopicMessageQueue,
    create_message_client,
    create_message_envelope,
    marshal_payload,
    unmarshal_payload,
)


def _config(**optional):
    return MessageBusConfig(
        broker_info=HostInfo(host="broker", port=1883),
        optional=optional,
    )


class TestHostInfo(unittest.TestCase):
    """Test HostInfo."""

    def test_get_host_url(self):
        info = HostInfo(protocol="tcp", host="broker", port=1883)
        self.assertEqual(info.get_host_url(), "tcp://broker:1883")

    def test_is_empty(self):
        self.assertTrue(HostInfo(host="", port=1883).is_host_info_empty())
        self.assertTrue(HostInfo(host="x", port=0).is_host_info_empty())
        self.assertFalse(HostInfo(host="x", port=1883).is_host_info_empty())


class TestParseHelpers(unittest.TestCase):
    """Test _parse_int and _parse_bool."""

    def test_parse_int_valid(self):
        self.assertEqual(cl._parse_int("42", 0), 42)

    def test_parse_int_invalid(self):
        self.assertEqual(cl._parse_int("abc", 7), 7)
        self.assertEqual(cl._parse_int(None, 7), 7)

    def test_parse_bool_true_variants(self):
        for val in ("true", "1", "yes", "on"):
            self.assertTrue(cl._parse_bool(val))
        self.assertTrue(cl._parse_bool(True))

    def test_parse_bool_false(self):
        self.assertFalse(cl._parse_bool("false"))
        self.assertFalse(cl._parse_bool("0"))


class TestTlsConfigurationOptions(unittest.TestCase):
    """Test TLS option parsing from the optional map."""

    def test_defaults(self):
        tls = TlsConfigurationOptions(_config())
        self.assertFalse(tls.skip_cert_verify)
        self.assertEqual(tls.cert_file, "")
        self.assertEqual(tls.ca_file, "")

    def test_parsed_values(self):
        tls = TlsConfigurationOptions(_config(
            **{cl.SKIP_CERT_VERIFY: "true",
               cl.CERT_FILE: "/c/cert.pem",
               cl.KEY_FILE: "/c/key.pem",
               cl.CA_FILE: "/c/ca.pem",
               cl.CERT_PEM_BLOCK: "cert",
               cl.KEY_PEM_BLOCK: "key",
               cl.CA_PEM_BLOCK: "ca"})
        )
        self.assertTrue(tls.skip_cert_verify)
        self.assertEqual(tls.cert_file, "/c/cert.pem")
        self.assertEqual(tls.key_file, "/c/key.pem")
        self.assertEqual(tls.ca_file, "/c/ca.pem")
        self.assertEqual(tls.cert_pem_block, "cert")
        self.assertEqual(tls.key_pem_block, "key")
        self.assertEqual(tls.ca_pem_block, "ca")


class TestMQTTClientOptions(unittest.TestCase):
    """Test MQTTClientOptions resolution."""

    def test_defaults(self):
        opts = MQTTClientOptions(_config())
        self.assertEqual(opts.qos, 0)
        self.assertEqual(opts.keep_alive, 60)
        self.assertFalse(opts.retained)
        self.assertTrue(opts.auto_reconnect)
        self.assertTrue(opts.clean_session)
        self.assertEqual(opts.connect_timeout, 5)

    def test_custom_values(self):
        opts = MQTTClientOptions(_config(
            **{cl.QOS: "2", cl.KEEP_ALIVE: "30", cl.RETAINED: "true",
               cl.AUTO_RECONNECT: "false", cl.CLEAN_SESSION: "false",
               cl.CONNECT_TIMEOUT: "9", cl.USERNAME: "u", cl.PASSWORD: "p",
               cl.CLIENT_ID: "my-client"})
        )
        self.assertEqual(opts.qos, 2)
        self.assertEqual(opts.keep_alive, 30)
        self.assertTrue(opts.retained)
        self.assertFalse(opts.auto_reconnect)
        self.assertFalse(opts.clean_session)
        self.assertEqual(opts.connect_timeout, 9)
        self.assertEqual(opts.username, "u")
        self.assertEqual(opts.password, "p")
        self.assertEqual(opts.client_id, "my-client")


class TestNewMqttClient(unittest.TestCase):
    """Test _new_mqtt_client auth modes."""

    def test_none_auth(self):
        opts = MQTTClientOptions(_config())
        with mock.patch("device_sdk_py.internal.controller.messaging.client.mqtt.Client") as m:
            cl._new_mqtt_client(opts)
            m.assert_called_once()
            m.return_value.username_pw_set.assert_not_called()
            m.return_value.tls_set.assert_not_called()

    def test_username_password(self):
        opts = MQTTClientOptions(_config(**{cl.USERNAME: "u", cl.PASSWORD: "p"}))
        opts.auth_mode = AUTH_MODE_USERNAME_PASSWORD
        with mock.patch("device_sdk_py.internal.controller.messaging.client.mqtt.Client") as m:
            cl._new_mqtt_client(opts)
            m.return_value.username_pw_set.assert_called_once_with("u", "p")

    def test_client_cert_auth(self):
        opts = MQTTClientOptions(_config(
            **{cl.CERT_FILE: "/c.pem", cl.KEY_FILE: "/k.pem", cl.CA_FILE: "/ca.pem"}))
        opts.auth_mode = AUTH_MODE_CLIENT_CERT
        with mock.patch("device_sdk_py.internal.controller.messaging.client.mqtt.Client") as m:
            cl._new_mqtt_client(opts)
            m.return_value.tls_set.assert_called_once()
            args, kwargs = m.return_value.tls_set.call_args
            self.assertEqual(kwargs["certfile"], "/c.pem")
            self.assertEqual(kwargs["keyfile"], "/k.pem")
            self.assertEqual(kwargs["ca_certs"], "/ca.pem")

    def test_cacert_auth(self):
        opts = MQTTClientOptions(_config(**{cl.CA_FILE: "/ca.pem"}))
        opts.auth_mode = AUTH_MODE_CACERT
        with mock.patch("device_sdk_py.internal.controller.messaging.client.mqtt.Client") as m:
            cl._new_mqtt_client(opts)
            m.return_value.tls_set.assert_called_once()
            self.assertEqual(m.return_value.tls_set.call_args.kwargs["ca_certs"], "/ca.pem")


class TestMessageEnvelope(unittest.TestCase):
    """Test MessageEnvelope JSON/base64 serialization."""

    def test_roundtrip(self):
        env = MessageEnvelope(
            received_topic="edgex/events/device/simple",
            correlation_id="c1",
            request_id="r1",
            payload={"x": 1},
        )
        text = env.to_json()
        decoded = MessageEnvelope.from_json(text)
        self.assertEqual(decoded.correlation_id, "c1")
        self.assertEqual(decoded.request_id, "r1")
        self.assertEqual(decoded.payload, {"x": 1})

    def test_bytes_payload_base64(self):
        env = MessageEnvelope(payload=b"\x00\x01\x02")
        text = env.to_json()
        self.assertIn("AAEC", text)
        decoded = MessageEnvelope.from_json(text)
        self.assertEqual(decoded.payload, b"\x00\x01\x02")

    def test_from_json_non_base64_string_payload(self):
        decoded = MessageEnvelope.from_json(json.dumps({"payload": "plain"}))
        self.assertEqual(decoded.payload, "plain")

    def test_envelope_has_query_params_default(self):
        env = MessageEnvelope()
        self.assertIsNone(env.query_params)
        self.assertEqual(env.api_version, "v3")


class TestDecodeMessageEnvelope(unittest.TestCase):
    """Test _decode_message_envelope JSON/CBOR paths."""

    def tearDown(self):
        os.environ.pop("EDGEX_MESSAGE_CBOR_ENCODE", None)

    def test_json_path(self):
        os.environ["EDGEX_MESSAGE_CBOR_ENCODE"] = "false"
        env = MessageEnvelope(correlation_id="c", payload={"a": 1})
        decoded = cl._decode_message_envelope(env.to_json().encode("utf-8"))
        self.assertEqual(decoded.correlation_id, "c")
        self.assertEqual(decoded.payload, {"a": 1})

    def test_cbor_path(self):
        os.environ["EDGEX_MESSAGE_CBOR_ENCODE"] = "true"
        payload = {"CorrelationId": "c", "RequestId": "r", "payload": {"a": 1},
                   "ApiVersion": "v4"}
        import cbor2
        raw = cbor2.dumps(payload)
        decoded = cl._decode_message_envelope(raw)
        self.assertEqual(decoded.correlation_id, "c")
        self.assertEqual(decoded.request_id, "r")
        self.assertEqual(decoded.api_version, "v4")
        self.assertEqual(decoded.payload, {"a": 1})


class TestMarshalPayload(unittest.TestCase):
    """Test marshal/unmarshal payload helpers."""

    def test_marshal_json(self):
        raw = marshal_payload(CONTENT_TYPE_JSON, {"a": 1})
        self.assertEqual(json.loads(raw), {"a": 1})

    def test_marshal_cbor(self):
        import cbor2
        raw = marshal_payload(CONTENT_TYPE_CBOR, {"a": 1})
        self.assertEqual(cbor2.loads(raw), {"a": 1})

    def test_marshal_unsupported(self):
        with self.assertRaises(ValueError):
            marshal_payload("text/xml", {"a": 1})

    def test_unmarshal_json_dict_to_dataclass(self):
        raw = b'{"a": 1, "b": "x"}'

        @cl.dataclass
        class Sample:
            a: int
            b: str

        obj = unmarshal_payload(CONTENT_TYPE_JSON, raw, Sample)
        self.assertEqual(obj.a, 1)
        self.assertEqual(obj.b, "x")

    def test_unmarshal_cbor(self):
        import cbor2
        raw = cbor2.dumps({"a": 1})
        self.assertEqual(unmarshal_payload(CONTENT_TYPE_CBOR, raw, dict), {"a": 1})

    def test_unmarshal_unsupported(self):
        with self.assertRaises(ValueError):
            unmarshal_payload("text/xml", b"", dict)


class TestNewMessageHandler(unittest.TestCase):
    """Test the subscribe message handler."""

    def test_handles_valid_envelope(self):
        msg_queue = queue.Queue()
        err_queue = queue.Queue()
        handler = cl._new_message_handler(msg_queue, err_queue)
        env = MessageEnvelope(correlation_id="c", payload={"x": 1})
        msg = mock.Mock()
        msg.payload = env.to_json().encode("utf-8")
        msg.topic = "edgex/events/device/simple"
        handler(None, None, msg)
        received = msg_queue.get_nowait()
        self.assertEqual(received.received_topic, "edgex/events/device/simple")
        self.assertEqual(received.payload, {"x": 1})

    def test_bad_payload_goes_to_error_queue(self):
        msg_queue = queue.Queue()
        err_queue = queue.Queue()
        handler = cl._new_message_handler(msg_queue, err_queue)
        msg = mock.Mock()
        msg.payload = b"\xff\xfe not envelope"
        msg.topic = "t"
        handler(None, None, msg)
        self.assertTrue(msg_queue.empty())
        self.assertFalse(err_queue.empty())

    def test_full_queue_drops_message(self):
        msg_queue = queue.Queue(maxsize=1)
        err_queue = queue.Queue()
        handler = cl._new_message_handler(msg_queue, err_queue)
        env = MessageEnvelope(payload={})
        msg = mock.Mock()
        msg.payload = env.to_json().encode("utf-8")
        msg.topic = "t"
        handler(None, None, msg)
        handler(None, None, msg)  # second: queue full
        self.assertFalse(err_queue.empty())


class TestOnConnect(unittest.TestCase):
    """Test the _on_connect re-registration callback."""

    def test_re_registers_callbacks(self):
        client = mock.Mock()
        cb = lambda: None
        userdata = {"topic/a": cb, "topic/b": cb}
        cl._on_connect(client, userdata, {}, 0, None)
        client.message_callback_add.assert_any_call("topic/a", cb)
        client.message_callback_add.assert_any_call("topic/b", cb)


class TestMqttMessageClient(unittest.TestCase):
    """Test MqttMessageClient against a mocked paho client."""

    def setUp(self):
        self.patcher = mock.patch("device_sdk_py.internal.controller.messaging.client._new_mqtt_client")
        self.mock_factory = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.paho = mock.Mock()
        self.paho.is_connected.return_value = False
        self.mock_factory.return_value = self.paho

    def _client(self, **optional):
        return MqttMessageClient(_config(**optional))

    def test_connect_success(self):
        self.paho.connect.return_value = 0
        client = self._client()
        client.connect()
        self.paho.connect.assert_called_once()
        self.paho.loop_start.assert_called_once()

    def test_connect_returns_early_if_connected(self):
        self.paho.is_connected.return_value = True
        client = self._client()
        client.connect()
        self.paho.connect.assert_not_called()

    def test_connect_failure_raises(self):
        self.paho.connect.return_value = 1
        client = self._client()
        with self.assertRaises(RuntimeError):
            client.connect()
        self.paho.loop_start.assert_not_called()

    def test_connect_value_error_wrapped(self):
        self.paho.connect.side_effect = ValueError("bad host")
        client = self._client()
        with self.assertRaises(RuntimeError):
            client.connect()

    def test_publish_calls_paho(self):
        client = self._client()
        env = MessageEnvelope(correlation_id="c", payload={"x": 1})
        client.publish(env, "edgex/events/device/simple")
        self.paho.publish.assert_called_once()
        kwargs = self.paho.publish.call_args.kwargs
        self.assertEqual(kwargs["topic"], "edgex/events/device/simple")
        self.assertEqual(kwargs["qos"], 0)

    def test_publish_error_wrapped(self):
        self.paho.publish.side_effect = ValueError("bad payload")
        client = self._client()
        with self.assertRaises(RuntimeError):
            client.publish(MessageEnvelope(payload={}), "t")

    def test_subscribe_records_success(self):
        self.paho.subscribe.return_value = (0, 1)
        client = self._client()
        msg_queue = queue.Queue()
        client.subscribe([TopicMessageQueue("edgex/events/#", msg_queue)], queue.Queue())
        self.paho.message_callback_add.assert_called_once()
        self.assertIn("edgex/events/#", client._existing_subscriptions)

    def test_unsubscribe_removes(self):
        self.paho.subscribe.return_value = (0, 1)
        self.paho.unsubscribe.return_value = (0, None)
        client = self._client()
        msg_queue = queue.Queue()
        client.subscribe([TopicMessageQueue("edgex/events/#", msg_queue)], queue.Queue())
        client.unsubscribe(["edgex/events/#"])
        self.assertNotIn("edgex/events/#", client._existing_subscriptions)
        self.paho.unsubscribe.assert_called_once_with("edgex/events/#")

    def test_unsubscribe_unknown_topic_noop(self):
        client = self._client()
        client.unsubscribe(["never-subscribed"])
        self.paho.unsubscribe.assert_not_called()

    def test_disconnect(self):
        self.paho.is_connected.return_value = True
        client = self._client()
        client.disconnect()
        self.paho.disconnect.assert_called_once()
        self.paho.loop_stop.assert_called_once()


class TestCreateMessageClient(unittest.TestCase):
    """Test the factory."""

    def test_mqtt_type(self):
        with mock.patch("device_sdk_py.internal.controller.messaging.client._new_mqtt_client"):
            client = create_message_client(_config())
            self.assertIsInstance(client, MqttMessageClient)

    def test_unsupported_type_raises(self):
        cfg = _config()
        cfg.message_bus_type = "kafka"
        with self.assertRaises(ValueError):
            create_message_client(cfg)


class TestCreateMessageEnvelope(unittest.TestCase):
    """Test create_message_envelope."""

    def test_generates_ids(self):
        env = create_message_envelope({"x": 1})
        self.assertTrue(env.correlation_id)
        self.assertTrue(env.request_id)
        self.assertEqual(env.payload, {"x": 1})

    def test_provided_ids(self):
        env = create_message_envelope({"x": 1}, correlation_id="c", request_id="r")
        self.assertEqual(env.correlation_id, "c")
        self.assertEqual(env.request_id, "r")

    def test_base64_env(self):
        os.environ["EDGEX_MSG_BASE64_PAYLOAD"] = "true"
        try:
            env = create_message_envelope(b"bytes")
            self.assertEqual(env.payload, "Ynl0ZXM=")
        finally:
            os.environ.pop("EDGEX_MSG_BASE64_PAYLOAD", None)


if __name__ == "__main__":
    unittest.main()
