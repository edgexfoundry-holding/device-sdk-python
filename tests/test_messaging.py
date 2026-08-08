# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the message-bus controller layer (M10 cleanup).

Covers the previously low/untested modules:
- `messaging/command.py`: topic parsing, query filtering, command subscription
- `messaging/validation.py`: device validation request handler
- `messaging/callback.py`: system-event decode/dispatch
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.controller.messaging.client import (  # noqa: E402
    MessageEnvelope,
    TopicMessageQueue,
)
from device_sdk_py.internal.controller.messaging.command import (  # noqa: E402
    _build_response_topic,
    _filter_query_params,
    _parse_command_topic,
    subscribe_commands,
)
from device_sdk_py.internal.controller.messaging.validation import (  # noqa: E402
    DeviceValidationHandler,
    subscribe_device_validation,
)
from device_sdk_py.internal.controller.messaging import callback as cb  # noqa: E402


class _FakeClient:
    """Minimal MessageClient that captures subscriptions and published envelopes."""

    def __init__(self):
        self.subscribed_topics: list = []
        self.topic_queues: list = []
        self.err_queue = queue.Queue()
        self.published: list = []

    def connect(self):
        pass

    def subscribe(self, topics, err_queue=None):
        self.topic_queues.extend(topics)
        self.subscribed_topics.extend(tq.topic for tq in topics)
        self.err_queue = err_queue or queue.Queue()

    def publish(self, message, topic):
        self.published.append((topic, message))

    def get_queue_for(self, topic_prefix):
        for tq in self.topic_queues:
            if tq.topic.startswith(topic_prefix):
                return tq.message_queue
        return None


# ---------------------------------------------------------------------------
# messaging/command.py
# ---------------------------------------------------------------------------

class TestParseCommandTopic(unittest.TestCase):
    """Test _parse_command_topic."""

    def test_valid_get_topic(self):
        parsed = _parse_command_topic(
            "edgex/command/request/device-simple/sensor-01/temperature/GET",
            "edgex", "device-simple",
        )
        self.assertEqual(parsed["device_name"], "sensor-01")
        self.assertEqual(parsed["command_name"], "temperature")
        self.assertEqual(parsed["method"], "GET")

    def test_valid_set_topic(self):
        parsed = _parse_command_topic(
            "edgex/command/request/device-simple/sensor-01/power/SET",
            "edgex", "device-simple",
        )
        self.assertEqual(parsed["method"], "SET")

    def test_url_encoded_segments(self):
        parsed = _parse_command_topic(
            "edgex/command/request/device-simple/my%20device/on%2Foff/GET",
            "edgex", "device-simple",
        )
        self.assertEqual(parsed["device_name"], "my device")
        self.assertEqual(parsed["command_name"], "on/off")

    def test_wrong_prefix_returns_none(self):
        self.assertIsNone(_parse_command_topic(
            "other/command/request/device-simple/x/y/GET", "edgex", "device-simple"))

    def test_wrong_service_returns_none(self):
        self.assertIsNone(_parse_command_topic(
            "edgex/command/request/other-service/x/y/GET", "edgex", "device-simple"))

    def test_too_few_segments_returns_none(self):
        self.assertIsNone(_parse_command_topic(
            "edgex/command/request/device-simple/x/y", "edgex", "device-simple"))

    def test_unsupported_method_returns_none(self):
        self.assertIsNone(_parse_command_topic(
            "edgex/command/request/device-simple/x/y/POST", "edgex", "device-simple"))


class TestBuildResponseTopic(unittest.TestCase):
    """Test _build_response_topic."""

    def test_format(self):
        self.assertEqual(
            _build_response_topic("edgex", "device-simple", "req-1"),
            "edgex/response/device-simple/req-1",
        )


class TestFilterQueryParams(unittest.TestCase):
    """Test _filter_query_params reserved-parameter handling."""

    def test_defaults(self):
        raw, reserved = _filter_query_params({})
        self.assertEqual(raw, "")
        self.assertFalse(reserved["ds-pushevent"])
        self.assertTrue(reserved["ds-returnevent"])
        self.assertTrue(reserved["ds-regexcommand"])

    def test_push_event_true(self):
        _, reserved = _filter_query_params({"ds-pushevent": "true"})
        self.assertTrue(reserved["ds-pushevent"])
        self.assertEqual(_, "")

    def test_return_event_false(self):
        _, reserved = _filter_query_params({"ds-returnevent": "false"})
        self.assertFalse(reserved["ds-returnevent"])

    def test_regex_command_false(self):
        _, reserved = _filter_query_params({"ds-regexcommand": "false"})
        self.assertFalse(reserved["ds-regexcommand"])

    def test_ds_params_stripped(self):
        raw, _ = _filter_query_params({"ds-pushevent": "true", "foo": "bar"})
        self.assertEqual(raw, "foo=bar")

    def test_multiple_raw_params(self):
        raw, _ = _filter_query_params({"a": "1", "b": "2"})
        self.assertEqual(raw, "a=1&b=2")


class TestSubscribeCommands(unittest.TestCase):
    """Integration-style test of the command subscription thread."""

    def _make_env(self, topic="edgex/command/request/device-simple/sensor-01/temperature/GET",
                  request_id="req-1", query_params=None):
        return MessageEnvelope(
            received_topic=topic,
            correlation_id="corr-1",
            request_id=request_id,
            payload={},
            query_params=query_params or {},
        )

    def _make_context(self):
        client = _FakeClient()
        cancel = threading.Event()
        config = mock.Mock()
        config.device = mock.Mock()
        config.device.max_event_size = 1024
        return client, cancel, config

    def _drain(self, client, seconds=1.0):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if client.published:
                return
            time.sleep(0.01)

    def test_subscribe_gets_topic(self):
        client, cancel, config = self._make_context()
        thread = subscribe_commands(
            cancel, client, "edgex", "device-simple", driver=mock.Mock(),
            configuration=config, device_service=mock.Mock(), logger=logging.getLogger("t"),
        )
        self.assertTrue(thread.is_alive())
        self.assertIn("edgex/command/request/device-simple/#", client.subscribed_topics)
        cancel.set()
        thread.join(timeout=2.0)

    def test_get_command_publishes_response(self):
        client, cancel, config = self._make_context()
        driver = mock.Mock()

        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_read") as cmd_read:
            event = mock.Mock()
            event.profile_name = "p1"
            event.device_name = "sensor-01"
            event.source_name = "src"
            event.readings = []
            cmd_read.return_value = event

            subscribe_commands(
                cancel, client, "edgex", "device-simple", driver=driver,
                configuration=config, device_service=mock.Mock(),
                logger=logging.getLogger("t"),
            )
            q = client.get_queue_for("edgex/command/request/device-simple/")
            self.assertIsNotNone(q)
            q.put(self._make_env())
            self._drain(client)
            self.assertTrue(client.published, "no response published")
            topic, env = client.published[0]
            self.assertEqual(topic, "edgex/response/device-simple/req-1")
            self.assertEqual(env.payload["statusCode"], 200)

        cancel.set()
        client.topic_queues[0].message_queue = queue.Queue()
        thread = None

    def test_set_command_publishes_response(self):
        client, cancel, config = self._make_context()
        env = self._make_env(
            topic="edgex/command/request/device-simple/sensor-01/power/SET",
            request_id="req-2",
        )
        env.payload = {"power": "on"}

        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_write") as cmd_write:
            cmd_write.return_value = None
            subscribe_commands(
                cancel, client, "edgex", "device-simple", driver=mock.Mock(),
                configuration=config, device_service=mock.Mock(),
                logger=logging.getLogger("t"),
            )
            q = client.get_queue_for("edgex/command/request/device-simple/")
            q.put(env)
            self._drain(client)
            self.assertTrue(client.published)
            topic, resp_env = client.published[0]
            self.assertEqual(topic, "edgex/response/device-simple/req-2")
            self.assertEqual(resp_env.payload["statusCode"], 200)

        cancel.set()

    def test_unparseable_topic_publishes_nothing(self):
        client, cancel, config = self._make_context()
        subscribe_commands(
            cancel, client, "edgex", "device-simple", driver=mock.Mock(),
            configuration=config, device_service=mock.Mock(), logger=logging.getLogger("t"),
        )
        q = client.get_queue_for("edgex/command/request/device-simple/")
        q.put(self._make_env(topic="edgex/wrong/topic"))
        time.sleep(0.1)
        self.assertEqual(client.published, [])
        cancel.set()


# ---------------------------------------------------------------------------
# messaging/validation.py
# ---------------------------------------------------------------------------

class TestDeviceValidationHandler(unittest.TestCase):
    """Test the MQTT device validation handler."""

    def setUp(self):
        self.driver = mock.Mock()
        self.handler = DeviceValidationHandler(
            service_name="device-simple",
            driver=self.driver,
            base_topic_prefix="edgex",
            logger=logging.getLogger("t"),
        )

    def test_request_topic(self):
        self.assertEqual(self.handler.request_topic, "edgex/device-simple/validate/device")

    def test_response_topic(self):
        self.assertEqual(
            self.handler._response_topic("req-9"),
            "edgex/response/device-simple/req-9",
        )

    def test_publish_response_success(self):
        client = mock.Mock()
        self.handler._client = client
        self.handler._publish_response("req-1", "corr-1", error_code="")
        topic, payload, kwargs = client.publish.call_args[0][0], client.publish.call_args[0][1], client.publish.call_args[1]
        self.assertEqual(topic, "edgex/response/device-simple/req-1")
        data = json.loads(payload)
        self.assertEqual(data["errorCode"], 0)
        self.assertEqual(data["requestID"], "req-1")
        self.assertEqual(kwargs["qos"], 0)

    def test_publish_response_with_error(self):
        client = mock.Mock()
        self.handler._client = client
        self.handler._publish_response("req-1", "corr-1", error_code="boom")
        data = json.loads(client.publish.call_args[0][1])
        self.assertEqual(data["errorCode"], "boom")

    def test_publish_response_noop_without_request_id(self):
        client = mock.Mock()
        self.handler._client = client
        self.handler._publish_response("", "corr-1")
        client.publish.assert_not_called()

    def test_on_message_valid_device(self):
        envelope = {
            "requestID": "req-1",
            "correlationID": "corr-1",
            "payload": {"device": {"name": "sensor-01", "adminState": "UNLOCKED"}},
        }
        msg = mock.Mock()
        msg.payload = json.dumps(envelope).encode("utf-8")
        msg.topic = self.handler.request_topic
        client = mock.Mock()
        self.handler._client = client

        with mock.patch("device_sdk_py.internal.controller.messaging.validation._build_device") as build:
            device = mock.Mock()
            build.return_value = device
            self.handler._on_message(client, None, msg)

        self.driver.validate_device.assert_called_once_with(device)
        client.publish.assert_called_once()
        data = json.loads(client.publish.call_args[0][1])
        self.assertEqual(data["errorCode"], 0)

    def test_on_message_validation_failure_publishes_error(self):
        envelope = {
            "requestID": "req-2",
            "correlationID": "corr-2",
            "payload": {"device": {"name": "bad"}},
        }
        msg = mock.Mock()
        msg.payload = json.dumps(envelope).encode("utf-8")
        client = mock.Mock()
        self.handler._client = client

        self.driver.validate_device.side_effect = RuntimeError("bad protocols")
        with mock.patch("device_sdk_py.internal.controller.messaging.validation._build_device") as build:
            build.return_value = mock.Mock()
            self.handler._on_message(client, None, msg)

        data = json.loads(client.publish.call_args[0][1])
        self.assertEqual(data["errorCode"], "bad protocols")

    def test_on_message_missing_payload(self):
        msg = mock.Mock()
        msg.payload = json.dumps({"requestID": "r", "payload": None}).encode("utf-8")
        client = mock.Mock()
        self.handler._client = client
        self.handler._on_message(client, None, msg)
        client.publish.assert_called_once()
        data = json.loads(client.publish.call_args[0][1])
        self.assertNotEqual(data["errorCode"], 0)

    def test_subscribe_device_validation_returns_handler(self):
        with mock.patch.object(DeviceValidationHandler, "start") as start:
            handler = subscribe_device_validation("device-simple", self.driver)
            start.assert_called_once()
            self.assertIsInstance(handler, DeviceValidationHandler)


# ---------------------------------------------------------------------------
# messaging/callback.py
# ---------------------------------------------------------------------------

class TestDecodeSystemEvent(unittest.TestCase):
    """Test _decode_system_event."""

    def test_payload_bytes_json(self):
        env = MessageEnvelope(payload=json.dumps({"type": "device"}).encode("utf-8"))
        self.assertEqual(cb._decode_system_event(env), {"type": "device"})

    def test_payload_str_json(self):
        env = MessageEnvelope(payload='{"type": "device"}')
        self.assertEqual(cb._decode_system_event(env), {"type": "device"})

    def test_payload_dict(self):
        env = MessageEnvelope(payload={"type": "device"})
        self.assertEqual(cb._decode_system_event(env), {"type": "device"})

    def test_invalid_payload(self):
        env = MessageEnvelope(payload=b"\xff\xfe not json")
        self.assertIsNone(cb._decode_system_event(env))

    def test_non_dict_payload(self):
        env = MessageEnvelope(payload=[1, 2, 3])
        self.assertIsNone(cb._decode_system_event(env))


class TestDeviceSystemEvent(unittest.TestCase):
    """Test _handle_device_system_event."""

    def setUp(self):
        self.added = []
        self.updated = []
        self.deleted = []
        self.log = logging.getLogger("t")

    def _event(self, action):
        return {
            "type": "device",
            "action": action,
            "owner": "device-simple",
            "details": {"name": "sensor-01"},
        }

    def test_add(self):
        cb._handle_device_system_event(self._event("add"), "device-simple",
                                       self.added.append, self.updated.append,
                                       self.deleted.append, self.log)
        self.assertEqual(self.added, [{"name": "sensor-01"}])

    def test_update(self):
        updated = []
        cb._handle_device_system_event(self._event("update"), "device-simple",
                                       self.added.append,
                                       lambda n, d: updated.append((n, d)),
                                       self.deleted.append, self.log)
        self.assertEqual(updated, [("sensor-01", {"name": "sensor-01"})])

    def test_delete(self):
        cb._handle_device_system_event(self._event("delete"), "device-simple",
                                       self.added.append, self.updated.append,
                                       self.deleted.append, self.log)
        self.assertEqual(self.deleted, ["sensor-01"])

    def test_missing_name(self):
        evt = {"type": "device", "action": "add", "details": {}}
        cb._handle_device_system_event(evt, "device-simple",
                                       self.added.append, self.updated.append,
                                       self.deleted.append, self.log)
        self.assertEqual(self.added, [])

    def test_unknown_action(self):
        cb._handle_device_system_event(self._event("bogus"), "device-simple",
                                       self.added.append, self.updated.append,
                                       self.deleted.append, self.log)
        self.assertEqual(self.added, [])
        self.assertEqual(self.updated, [])
        self.assertEqual(self.deleted, [])

    def test_handler_raises_is_caught(self):
        def boom(details):
            raise RuntimeError("boom")
        cb._handle_device_system_event(self._event("add"), "device-simple",
                                       boom, self.updated.append,
                                       self.deleted.append, self.log)
        self.assertEqual(self.updated, [])
        self.assertEqual(self.deleted, [])


class TestDeviceProfileSystemEvent(unittest.TestCase):
    """Test _handle_device_profile_system_event."""

    def setUp(self):
        self.updated = []
        self.deleted = []
        self.log = logging.getLogger("t")

    def _event(self, action, owner="device-simple"):
        return {
            "type": "deviceprofile",
            "action": action,
            "owner": owner,
            "details": {"name": "p1"},
        }

    def test_update(self):
        cb._handle_device_profile_system_event(self._event("update"), "device-simple",
                                               self.updated.append, self.deleted.append, self.log)
        self.assertEqual(self.updated, [{"name": "p1"}])

    def test_delete_with_core_metadata_owner(self):
        cb._handle_device_profile_system_event(
            self._event("delete", owner="core-metadata"), "device-simple",
            self.updated.append, self.deleted.append, self.log)
        self.assertEqual(self.deleted, ["p1"])

    def test_delete_with_other_owner_ignored(self):
        cb._handle_device_profile_system_event(self._event("delete", owner="other"), "device-simple",
                                               self.updated.append, self.deleted.append, self.log)
        self.assertEqual(self.deleted, [])

    def test_add_is_noop(self):
        cb._handle_device_profile_system_event(self._event("add"), "device-simple",
                                               self.updated.append, self.deleted.append, self.log)
        self.assertEqual(self.updated, [])
        self.assertEqual(self.deleted, [])


class TestProvisionWatcherSystemEvent(unittest.TestCase):
    """Test _handle_provision_watcher_system_event."""

    def setUp(self):
        self.added = []
        self.updated = []
        self.deleted = []
        self.log = logging.getLogger("t")

    def _event(self, action):
        return {
            "type": "provisionwatcher",
            "action": action,
            "owner": "device-simple",
            "details": {"name": "pw-1"},
        }

    def test_add(self):
        cb._handle_provision_watcher_system_event(self._event("add"), "device-simple",
                                                  self.added.append, self.updated.append,
                                                  self.deleted.append, self.log)
        self.assertEqual(self.added, [{"name": "pw-1"}])

    def test_update(self):
        updated = []
        cb._handle_provision_watcher_system_event(self._event("update"), "device-simple",
                                                  self.added.append,
                                                  lambda n, d: updated.append((n, d)),
                                                  self.deleted.append, self.log)
        self.assertEqual(updated, [("pw-1", {"name": "pw-1"})])

    def test_delete(self):
        cb._handle_provision_watcher_system_event(self._event("delete"), "device-simple",
                                                  self.added.append, self.updated.append,
                                                  self.deleted.append, self.log)
        self.assertEqual(self.deleted, ["pw-1"])

    def test_missing_name(self):
        evt = {"type": "provisionwatcher", "action": "add", "details": {}}
        cb._handle_provision_watcher_system_event(evt, "device-simple",
                                                  self.added.append, self.updated.append,
                                                  self.deleted.append, self.log)
        self.assertEqual(self.added, [])


class TestDeviceServiceSystemEvent(unittest.TestCase):
    """Test _handle_device_service_system_event."""

    def setUp(self):
        self.updated = []
        self.log = logging.getLogger("t")

    def test_update_for_self(self):
        evt = {"action": "update", "details": {"name": "device-simple"}}
        cb._handle_device_service_system_event(evt, "device-simple", self.updated.append, self.log)
        self.assertEqual(self.updated, [{"name": "device-simple"}])

    def test_update_for_other_service_ignored(self):
        evt = {"action": "update", "details": {"name": "other-service"}}
        cb._handle_device_service_system_event(evt, "device-simple", self.updated.append, self.log)
        self.assertEqual(self.updated, [])

    def test_add_delete_noop(self):
        for action in ("add", "delete"):
            evt = {"action": action, "details": {"name": "device-simple"}}
            cb._handle_device_service_system_event(evt, "device-simple", self.updated.append, self.log)
        self.assertEqual(self.updated, [])


class TestGetBaseServiceName(unittest.TestCase):
    """Test _get_base_service_name."""

    def test_no_instance(self):
        self.assertEqual(cb._get_base_service_name("device-simple"), "device-simple")

    def test_numeric_instance(self):
        self.assertEqual(cb._get_base_service_name("device-simple-1"), "device-simple")

    def test_alphanumeric_instance_kept(self):
        self.assertEqual(cb._get_base_service_name("device-simple-west"), "device-simple-west")


class TestSubscribeSystemEvents(unittest.TestCase):
    """Integration-style test of the system-event subscription thread."""

    def test_subscribes_expected_topics(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = cb.subscribe_system_events(
            cancel, client, "edgex", "device-simple", None,
            add_device=lambda d: None, update_device=lambda n, d: None,
            delete_device=lambda n: None,
            add_profile=lambda d: None, update_profile=lambda d: None,
            delete_profile=lambda n: None,
            add_watcher=lambda d: None, update_watcher=lambda n, d: None,
            delete_watcher=lambda n: None,
            update_service=lambda d: None,
            logger=logging.getLogger("t"),
        )
        self.assertTrue(thread.is_alive())
        self.assertIn("edgex/system-events/device-simple/#", client.subscribed_topics)
        self.assertIn("edgex/system-events/device-profile/delete/#", client.subscribed_topics)
        cancel.set()
        thread.join(timeout=2.0)

    def test_dispatches_device_event_to_callback(self):
        client = _FakeClient()
        cancel = threading.Event()
        deleted = []
        cb.subscribe_system_events(
            cancel, client, "edgex", "device-simple", None,
            add_device=lambda d: None, update_device=lambda n, d: None,
            delete_device=lambda n: deleted.append(n),
            add_profile=lambda d: None, update_profile=lambda d: None,
            delete_profile=lambda n: None,
            add_watcher=lambda d: None, update_watcher=lambda n, d: None,
            delete_watcher=lambda n: None,
            update_service=lambda d: None,
            logger=logging.getLogger("t"),
        )
        q = client.get_queue_for("edgex/system-events/device-simple/")
        self.assertIsNotNone(q)
        evt = {
            "type": "device", "action": "delete", "owner": "device-simple",
            "details": {"name": "sensor-01"},
        }
        q.put(MessageEnvelope(received_topic="edgex/system-events/device-simple/delete",
                              payload=evt))
        deadline = time.time() + 1.0
        while time.time() < deadline and not deleted:
            time.sleep(0.01)
        self.assertEqual(deleted, ["sensor-01"])
        cancel.set()


if __name__ == "__main__":
    unittest.main()
