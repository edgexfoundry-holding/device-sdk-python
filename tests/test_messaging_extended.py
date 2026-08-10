# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Extended unit tests for the message-bus controller layer.

Complements `test_messaging.py` by targeting the branches it leaves uncovered:
the remaining command-subscription handlers (semaphore limit, error responses,
push-event republish, invalid payloads), the MQTT device-validation lifecycle
(start/stop/connect/message-decode failures) and the remaining system-event
dispatch paths (owner validation, per-type dispatch, decode failures).
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

from device_sdk_py.internal.common.utils import (  # noqa: E402
    EdgexError,
    EdgexErrorKind,
)
from device_sdk_py.internal.controller.messaging import callback as cb  # noqa: E402
from device_sdk_py.internal.controller.messaging.client import (  # noqa: E402
    MessageEnvelope,
)
from device_sdk_py.internal.controller.messaging.command import (  # noqa: E402
    _filter_query_params,
    subscribe_commands,
)
from device_sdk_py.internal.controller.messaging.validation import (  # noqa: E402
    DeviceValidationHandler,
)


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _FakeClient:
    """Minimal MessageClient that captures subscriptions and published envelopes."""

    def __init__(self):
        self.subscribed_topics = []
        self.topic_queues = []
        self.err_queue = queue.Queue()
        self.published = []

    def connect(self):
        pass

    def subscribe(self, topics, err_queue=None):
        self.topic_queues.extend(topics)
        self.subscribed_topics.extend(tq.topic for tq in topics)
        self.err_queue = err_queue or queue.Queue()

    def publish(self, message, topic):
        self.published.append((topic, message))

    def unsubscribe(self, topics):
        for t in topics:
            if t in self.subscribed_topics:
                self.subscribed_topics.remove(t)

    def get_queue_for(self, topic_prefix):
        for tq in self.topic_queues:
            if tq.topic.startswith(topic_prefix):
                return tq.message_queue
        return None


class _RaisingClient(_FakeClient):
    """MessageClient whose publish always raises."""

    def publish(self, message, topic):
        raise RuntimeError("broker unavailable")


def _make_config(max_event_size=1024):
    config = mock.Mock()
    config.device = mock.Mock()
    config.device.max_event_size = max_event_size
    return config


def _make_event(value_type="Int64"):
    event = mock.Mock()
    event.profile_name = "p1"
    event.device_name = "sensor-01"
    event.source_name = "src"
    event.readings = [mock.Mock(value_type=value_type)]
    return event


def _make_command_env(topic, request_id="req-1", query_params=None, payload=None):
    return MessageEnvelope(
        received_topic=topic,
        correlation_id="corr-1",
        request_id=request_id,
        payload={} if payload is None else payload,
        query_params=query_params or {},
    )


class _CommandBase(unittest.TestCase):
    """Shared helpers for command subscription tests."""

    def _start(self, client, cancel, max_concurrent=32, device_service=None, logger=None):
        return subscribe_commands(
            cancel, client, "edgex", "device-simple",
            driver=mock.Mock(),
            configuration=_make_config(),
            device_service=device_service or mock.Mock(),
            logger=logger or logging.getLogger("t"),
            max_concurrent=max_concurrent,
        )

    def _stop(self, cancel, thread):
        cancel.set()
        thread.join(timeout=2.0)


class TestMsgFilterQueryParams(_CommandBase):
    """Remaining `_filter_query_params` branches."""

    def test_unknown_ds_param_stripped(self):
        raw, reserved = _filter_query_params({"ds-unknown": "x", "foo": "1"})
        self.assertEqual(raw, "foo=1")
        self.assertFalse(reserved["ds-pushevent"])

    def test_push_event_non_true_keeps_default(self):
        _, reserved = _filter_query_params({"ds-pushevent": "false"})
        self.assertFalse(reserved["ds-pushevent"])


class TestMsgCommandGet(_CommandBase):
    """GET command subscription edge paths."""

    def test_semaphore_limit_rejects_request(self):
        client = _FakeClient()
        cancel = threading.Event()
        logger = mock.Mock()
        thread = self._start(client, cancel, max_concurrent=0, logger=logger)
        q = client.get_queue_for("edgex/device/command/request/device-simple/")
        q.put(_make_command_env("edgex/device/command/request/device-simple/sensor-01/temperature/GET"))
        self.assertTrue(_wait_until(lambda: bool(client.published)))
        topic, env = client.published[0]
        self.assertEqual(topic, "edgex/response/device-simple/req-1")
        self.assertEqual(env.error_code, 1)
        self.assertEqual(env.payload, "device service busy: too many concurrent commands")
        self.assertEqual(env.content_type, "text/plain")
        self.assertTrue(logger.warning.called)
        self._stop(cancel, thread)

    def test_command_read_raises_edgx_error_publishes_error(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_read",
                        side_effect=EdgexError(EdgexErrorKind.SERVER_ERROR, "read boom")):
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            q.put(_make_command_env("edgex/device/command/request/device-simple/sensor-01/temperature/GET"))
            self.assertTrue(_wait_until(lambda: bool(client.published)))
        topic, env = client.published[0]
        self.assertEqual(env.error_code, 1)
        self.assertEqual(env.payload, "read boom")
        self.assertEqual(env.content_type, "text/plain")
        self._stop(cancel, thread)

    def test_event_none_publishes_status_only(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_read",
                        return_value=None):
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            q.put(_make_command_env("edgex/device/command/request/device-simple/sensor-01/temperature/GET"))
            self.assertTrue(_wait_until(lambda: bool(client.published)))
        _, env = client.published[0]
        self.assertEqual(env.payload, {"apiVersion": "v3", "statusCode": 200})
        self._stop(cancel, thread)

    def test_event_non_binary_reading_returns_json_event(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_read",
                        return_value=_make_event("Int64")):
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            q.put(_make_command_env("edgex/device/command/request/device-simple/sensor-01/temperature/GET"))
            self.assertTrue(_wait_until(lambda: bool(client.published)))
        _, env = client.published[0]
        self.assertIn("event", env.payload)
        self.assertEqual(env.content_type, "application/json")
        self._stop(cancel, thread)

    def test_event_binary_reading_uses_cbor(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_read",
                        return_value=_make_event("Binary")):
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            q.put(_make_command_env("edgex/device/command/request/device-simple/sensor-01/temperature/GET"))
            self.assertTrue(_wait_until(lambda: bool(client.published)))
        _, env = client.published[0]
        self.assertEqual(env.content_type, "application/cbor")
        self._stop(cancel, thread)

    def test_publish_response_raises_logs(self):
        client = _RaisingClient()
        cancel = threading.Event()
        logger = mock.Mock()
        thread = self._start(client, cancel, logger=logger)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_read",
                        return_value=_make_event("Int64")):
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            q.put(_make_command_env("edgex/device/command/request/device-simple/sensor-01/temperature/GET"))
            self.assertTrue(_wait_until(lambda: logger.error.called))
        self.assertTrue(
            any("Failed to publish command response" in str(call)
                for call in logger.error.call_args_list))
        self._stop(cancel, thread)

    def test_push_event_republishes_event(self):
        client = _FakeClient()
        cancel = threading.Event()
        device_service = mock.Mock()
        device_service.configuration = mock.Mock()
        device_service.configuration.device = mock.Mock()
        device_service.configuration.device.max_event_size = 2048
        thread = self._start(client, cancel, device_service=device_service)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_read",
                        return_value=_make_event("Int64")), \
                mock.patch("device_sdk_py.internal.controller.messaging.publish.publish_event") as pub:
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            env = _make_command_env(
                "edgex/device/command/request/device-simple/sensor-01/temperature/GET",
                query_params={"ds-pushevent": "true"})
            q.put(env)
            self.assertTrue(_wait_until(lambda: pub.called))
        self.assertEqual(pub.call_args.kwargs["max_event_size"], 2048)
        self.assertEqual(pub.call_args.kwargs["base_topic_prefix"], "edgex")
        self.assertEqual(len(client.published), 1)
        self._stop(cancel, thread)

    def test_push_event_publish_failure_logged(self):
        client = _FakeClient()
        cancel = threading.Event()
        logger = mock.Mock()
        thread = self._start(client, cancel, logger=logger)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_read",
                        return_value=_make_event("Int64")), \
                mock.patch("device_sdk_py.internal.controller.messaging.publish.publish_event",
                           side_effect=RuntimeError("nope")):
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            env = _make_command_env(
                "edgex/device/command/request/device-simple/sensor-01/temperature/GET",
                query_params={"ds-pushevent": "true"})
            q.put(env)
            self.assertTrue(_wait_until(lambda: logger.error.called))
        self.assertTrue(
            any("Failed to publish event via ds-pushevent" in str(call)
                for call in logger.error.call_args_list))
        self._stop(cancel, thread)


class TestMsgCommandSet(_CommandBase):
    """SET command subscription edge paths."""

    def test_invalid_payload_publishes_error(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        q = client.get_queue_for("edgex/device/command/request/device-simple/")
        env = _make_command_env(
            "edgex/device/command/request/device-simple/sensor-01/power/SET",
            request_id="req-2", payload=["not", "a", "dict"])
        q.put(env)
        self.assertTrue(_wait_until(lambda: bool(client.published)))
        _, resp = client.published[0]
        self.assertEqual(resp.error_code, 1)
        self.assertEqual(resp.payload, "invalid request payload")
        self.assertEqual(resp.content_type, "text/plain")
        self._stop(cancel, thread)

    def test_set_string_payload_json_decoded(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_write",
                        return_value=None) as cw:
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            env = _make_command_env(
                "edgex/device/command/request/device-simple/sensor-01/power/SET",
                request_id="req-s1", payload=json.dumps({"power": "on"}))
            q.put(env)
            self.assertTrue(_wait_until(lambda: bool(client.published)))
        self.assertEqual(cw.call_args.kwargs["requests"], {"power": "on"})
        self._stop(cancel, thread)

    def test_set_bytes_payload_json_decoded(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_write",
                        return_value=None) as cw:
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            env = _make_command_env(
                "edgex/device/command/request/device-simple/sensor-01/power/SET",
                request_id="req-s2", payload=b'{"power": "on"}')
            q.put(env)
            self.assertTrue(_wait_until(lambda: bool(client.published)))
        self.assertEqual(cw.call_args.kwargs["requests"], {"power": "on"})
        self._stop(cancel, thread)

    def test_set_cbor_payload_decoded(self):
        import cbor2
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_write",
                        return_value=None) as cw:
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            env = MessageEnvelope(
                received_topic="edgex/device/command/request/device-simple/sensor-01/power/SET",
                correlation_id="corr-1", request_id="req-s3",
                payload=cbor2.dumps({"power": "on"}),
                content_type="application/cbor", query_params={})
            q.put(env)
            self.assertTrue(_wait_until(lambda: bool(client.published)))
        self.assertEqual(cw.call_args.kwargs["requests"], {"power": "on"})
        self._stop(cancel, thread)

    def test_set_undecodable_string_payload_publishes_error(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        q = client.get_queue_for("edgex/device/command/request/device-simple/")
        env = _make_command_env(
            "edgex/device/command/request/device-simple/sensor-01/power/SET",
            request_id="req-s4", payload="not-json{")
        q.put(env)
        self.assertTrue(_wait_until(lambda: bool(client.published)))
        _, resp = client.published[0]
        self.assertEqual(resp.error_code, 1)
        self.assertEqual(resp.payload, "invalid request payload")
        self.assertEqual(resp.content_type, "text/plain")
        self._stop(cancel, thread)

    def test_command_write_raises_edgx_error_publishes_error(self):
        client = _FakeClient()
        cancel = threading.Event()
        thread = self._start(client, cancel)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_write",
                        side_effect=EdgexError(EdgexErrorKind.SERVER_ERROR, "write boom")):
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            env = _make_command_env(
                "edgex/device/command/request/device-simple/sensor-01/power/SET",
                request_id="req-3", payload={"power": "on"})
            q.put(env)
            self.assertTrue(_wait_until(lambda: bool(client.published)))
        _, resp = client.published[0]
        self.assertEqual(resp.error_code, 1)
        self.assertEqual(resp.payload, "write boom")
        self.assertEqual(resp.content_type, "text/plain")
        self._stop(cancel, thread)

    def test_publish_response_raises_logs(self):
        client = _RaisingClient()
        cancel = threading.Event()
        logger = mock.Mock()
        thread = self._start(client, cancel, logger=logger)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_write",
                        return_value=None):
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            env = _make_command_env(
                "edgex/device/command/request/device-simple/sensor-01/power/SET",
                request_id="req-4", payload={"power": "on"})
            q.put(env)
            self.assertTrue(_wait_until(lambda: logger.error.called))
        self.assertTrue(
            any("Failed to publish set command response" in str(call)
                for call in logger.error.call_args_list))
        self._stop(cancel, thread)

    def test_event_published_after_set(self):
        client = _FakeClient()
        cancel = threading.Event()
        device_service = mock.Mock()
        device_service.configuration = mock.Mock()
        device_service.configuration.device = mock.Mock()
        device_service.configuration.device.max_event_size = 1024
        thread = self._start(client, cancel, device_service=device_service)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_write",
                        return_value=_make_event("Int64")), \
                mock.patch("device_sdk_py.internal.controller.messaging.publish.publish_event") as pub:
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            env = _make_command_env(
                "edgex/device/command/request/device-simple/sensor-01/power/SET",
                request_id="req-5", payload={"power": "on"})
            q.put(env)
            self.assertTrue(_wait_until(lambda: pub.called))
        self.assertEqual(pub.call_args.kwargs["service_name"], "device-simple")
        self.assertEqual(len(client.published), 1)
        self._stop(cancel, thread)

    def test_event_publish_failure_logged(self):
        client = _FakeClient()
        cancel = threading.Event()
        logger = mock.Mock()
        thread = self._start(client, cancel, logger=logger)
        with mock.patch("device_sdk_py.internal.controller.messaging.command.command_write",
                        return_value=_make_event("Int64")), \
                mock.patch("device_sdk_py.internal.controller.messaging.publish.publish_event",
                           side_effect=RuntimeError("nope")):
            q = client.get_queue_for("edgex/device/command/request/device-simple/")
            env = _make_command_env(
                "edgex/device/command/request/device-simple/sensor-01/power/SET",
                request_id="req-6", payload={"power": "on"})
            q.put(env)
            self.assertTrue(_wait_until(lambda: logger.error.called))
        self.assertTrue(
            any("Failed to publish event after set command" in str(call)
                for call in logger.error.call_args_list))
        self._stop(cancel, thread)


class TestMsgCommandLoop(_CommandBase):
    """Command subscription run-loop error paths."""

    def test_error_response_publish_failure_logged(self):
        client = _RaisingClient()
        cancel = threading.Event()
        logger = mock.Mock()
        thread = self._start(client, cancel, max_concurrent=0, logger=logger)
        q = client.get_queue_for("edgex/device/command/request/device-simple/")
        q.put(_make_command_env("edgex/device/command/request/device-simple/sensor-01/temperature/GET"))
        self.assertTrue(_wait_until(lambda: logger.error.called))
        self.assertTrue(
            any("Failed to publish error response" in str(call)
                for call in logger.error.call_args_list))
        self._stop(cancel, thread)

    def test_subscription_error_reported(self):
        client = _FakeClient()
        cancel = threading.Event()
        logger = mock.Mock()
        thread = self._start(client, cancel, logger=logger)
        client.err_queue.put("subscribe failed")
        self.assertTrue(_wait_until(
            lambda: any("Command subscription error" in str(call)
                        for call in logger.error.call_args_list)))
        self._stop(cancel, thread)

    def test_unexpected_error_in_loop_logged(self):
        client = _FakeClient()
        cancel = threading.Event()
        logger = mock.Mock()
        thread = self._start(client, cancel, logger=logger)
        q = client.get_queue_for("edgex/device/command/request/device-simple/")
        q.put(object())
        self.assertTrue(_wait_until(
            lambda: any("Unexpected error in command subscription loop" in str(call)
                        for call in logger.exception.call_args_list)))
        self._stop(cancel, thread)


class TestPublishEventMetrics(unittest.TestCase):
    """SendEvent mirrors Go by incrementing the EventsSent/ReadingsSent metrics."""

    def _event(self, reading_count=2):
        from device_sdk_py.internal.transformer.transform import Event, Reading
        readings = [
            Reading(resource_name=f"r{i}", value_type="Int64", value="1",
                    device_name="d1", profile_name="p1")
            for i in range(reading_count)
        ]
        return Event(event_id="e1", device_name="d1", profile_name="p1",
                     source_name="s1", origin=1, readings=readings)

    def test_increments_events_sent_and_readings_sent(self):
        from device_sdk_py.internal.clients.metrics import MetricsManager
        from device_sdk_py.internal.controller.messaging.publish import publish_event
        client = _FakeClient()
        mm = MetricsManager()
        publish_event(
            client=client, event=self._event(3), correlation_id="c1",
            base_topic_prefix="edgex", service_name="svc",
            profile_name="p1", device_name="d1", source_name="s1",
            metrics_manager=mm,
        )
        self.assertEqual(len(client.published), 1)
        self.assertEqual(mm.new_counter("EventsSent").value(), 1)
        self.assertEqual(mm.new_counter("ReadingsSent").value(), 3)
        self.assertEqual(mm.get_all_metrics()["EventsSent"]["value"], 1)
        self.assertEqual(mm.get_all_metrics()["ReadingsSent"]["value"], 3)

    def test_no_metrics_manager_still_publishes(self):
        from device_sdk_py.internal.controller.messaging.publish import publish_event
        client = _FakeClient()
        publish_event(
            client=client, event=self._event(1), correlation_id="c1",
            base_topic_prefix="edgex", service_name="svc",
            profile_name="p1", device_name="d1", source_name="s1",
        )
        self.assertEqual(len(client.published), 1)


class TestMsgValidationHandler(unittest.TestCase):
    """Device validation handler lifecycle and message edge paths."""

    def setUp(self):
        self.driver = mock.Mock()
        self.handler = DeviceValidationHandler(
            service_name="device-simple",
            driver=self.driver,
            base_topic_prefix="edgex",
            logger=mock.Mock(),
        )

    def test_start_when_thread_alive_noop(self):
        existing = mock.Mock()
        existing.is_alive.return_value = True
        self.handler._thread = existing
        self.handler.start()
        existing.join.assert_not_called()
        self.assertIs(self.handler._thread, existing)

    def test_start_spawns_thread(self):
        with mock.patch.object(self.handler, "_run") as run:
            self.handler.start()
            self.assertIsInstance(self.handler._thread, threading.Thread)
            run.assert_called_once_with()
            self.handler._thread.join(timeout=2.0)
        self.assertFalse(self.handler._thread.is_alive())

    def test_stop_unsubscribes_and_joins(self):
        fake_client = mock.Mock()
        fake_thread = mock.Mock()
        self.handler._client = fake_client
        self.handler._thread = fake_thread
        self.handler.stop()
        fake_client.unsubscribe.assert_called_once_with([self.handler.request_topic])
        fake_thread.join.assert_called_once_with(timeout=2.0)

    def test_stop_client_raises_still_joins(self):
        fake_client = mock.Mock()
        fake_client.unsubscribe.side_effect = RuntimeError("gone")
        fake_thread = mock.Mock()
        self.handler._client = fake_client
        self.handler._thread = fake_thread
        self.handler.stop()
        fake_thread.join.assert_called_once_with(timeout=2.0)

    def test_stop_without_client_or_thread(self):
        self.handler._client = None
        self.handler._thread = None
        self.handler.stop()

    def test_run_no_client_returns(self):
        self.handler._client = None
        self.handler._run()

    def test_run_subscribe_failure_logs_warning(self):
        fake_client = mock.Mock()
        fake_client.subscribe.side_effect = RuntimeError("subscribe failed")
        self.handler._client = fake_client
        self.handler._run()
        self.assertTrue(
            any("failed to subscribe" in str(call)
                for call in self.handler._logger.warning.call_args_list))

    def test_run_success_loop(self):
        fake_client = _FakeClient()
        self.handler._client = fake_client
        thread = threading.Thread(target=self.handler._run)
        thread.start()
        time.sleep(0.2)
        self.handler._stop_event.set()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertIn(self.handler.request_topic, fake_client.subscribed_topics)

    def test_run_processes_envelope(self):
        fake_client = _FakeClient()
        self.handler._client = fake_client
        thread = threading.Thread(target=self.handler._run)
        thread.start()
        try:
            q = fake_client.get_queue_for(self.handler.request_topic)
            q.put(MessageEnvelope(
                request_id="req-1", correlation_id="corr-1",
                payload={"device": {"name": "sensor-01"}}))
            self.assertTrue(_wait_until(lambda: bool(fake_client.published)))
            topic, env = fake_client.published[0]
            self.assertEqual(topic, "edgex/response/device-simple/req-1")
            self.assertEqual(env.error_code, 0)
        finally:
            self.handler._stop_event.set()
            thread.join(timeout=2.0)

    def test_publish_response_publish_raises_logged(self):
        client = mock.Mock()
        client.publish.side_effect = RuntimeError("broker gone")
        self.handler._client = client
        self.handler._publish_response("req-1", "corr-1")
        self.assertTrue(
            any("failed to publish device validation response" in str(call)
                for call in self.handler._logger.error.call_args_list))


class TestMsgCallbackDecode(unittest.TestCase):
    """Remaining `_decode_system_event` branches."""

    def test_str_payload_not_json_returns_none(self):
        env = MessageEnvelope(payload="not json at all")
        self.assertIsNone(cb._decode_system_event(env))

    def test_byte_payload_not_utf8_returns_none(self):
        env = MessageEnvelope(payload=b"\xff\xfe\x00\x01")
        self.assertIsNone(cb._decode_system_event(env))


class TestMsgCallbackHandlers(unittest.TestCase):
    """Remaining system-event handler edge paths."""

    def setUp(self):
        self.updated = []
        self.deleted = []
        self.added = []
        self.updated_service = []
        self.log = logging.getLogger("t")

    def test_profile_missing_name(self):
        logger = mock.Mock()
        cb._handle_device_profile_system_event(
            {"action": "update", "details": {}}, "device-simple",
            lambda d: self.updated.append(d), lambda n: self.deleted.append(n), logger)
        self.assertEqual(self.updated, [])
        self.assertTrue(
            any("missing profile name" in str(call)
                for call in logger.error.call_args_list))

    def test_profile_handler_raises_is_caught(self):
        def boom(details):
            raise RuntimeError("boom")
        logger = mock.Mock()
        evt = {"action": "update", "details": {"name": "p1"}}
        cb._handle_device_profile_system_event(evt, "device-simple", boom,
                                               lambda n: self.deleted.append(n), logger)
        self.assertEqual(self.deleted, [])
        self.assertTrue(
            any("Failed to handle device profile system event" in str(call)
                for call in logger.error.call_args_list))

    def test_watcher_handler_raises_is_caught(self):
        def boom(details):
            raise RuntimeError("boom")
        logger = mock.Mock()
        evt = {"action": "add", "details": {"name": "pw-1"}}
        cb._handle_provision_watcher_system_event(evt, "device-simple", boom,
                                                  lambda n, d: None,
                                                  lambda n: self.deleted.append(n), logger)
        self.assertEqual(self.deleted, [])
        self.assertTrue(
            any("Failed to handle provision watcher system event" in str(call)
                for call in logger.error.call_args_list))

    def test_service_handler_raises_is_caught(self):
        def boom(details):
            raise RuntimeError("boom")
        logger = mock.Mock()
        evt = {"action": "update", "details": {"name": "device-simple"}}
        cb._handle_device_service_system_event(evt, "device-simple", boom, logger)
        self.assertTrue(
            any("Failed to handle device service system event" in str(call)
                for call in logger.error.call_args_list))

    def test_profile_unknown_action_warns(self):
        logger = mock.Mock()
        evt = {"action": "bogus", "details": {"name": "p1"}}
        cb._handle_device_profile_system_event(evt, "device-simple",
                                               lambda d: None, lambda n: None, logger)
        self.assertTrue(
            any("Unknown device profile system event action" in str(call)
                for call in logger.warning.call_args_list))


class _SystemEventHarness:
    """Helper that wires callbacks and captures dispatch results."""

    def __init__(self, logger=None):
        self.client = _FakeClient()
        self.cancel = threading.Event()
        self.added_device = []
        self.deleted_device = []
        self.updated_profile = []
        self.deleted_profile = []
        self.added_watcher = []
        self.updated_service = []
        self.logger = logger or logging.getLogger("t")

    def subscribe(self, service_name="device-simple", base_service_name=None):
        self.thread = cb.subscribe_system_events(
            self.cancel, self.client, "edgex", service_name, base_service_name,
            add_device=lambda d: self.added_device.append(d),
            update_device=lambda n, d: None,
            delete_device=lambda n: self.deleted_device.append(n),
            add_profile=lambda d: None,
            update_profile=lambda d: self.updated_profile.append(d),
            delete_profile=lambda n: self.deleted_profile.append(n),
            add_watcher=lambda d: self.added_watcher.append(d),
            update_watcher=lambda n, d: None,
            delete_watcher=lambda n: None,
            update_service=lambda d: self.updated_service.append(d),
            logger=self.logger,
        )
        return self.thread

    def stop(self):
        self.cancel.set()
        self.thread.join(timeout=2.0)


class TestMsgSystemEventSubscribe(unittest.TestCase):
    """Remaining system-event subscription loop branches."""

    def test_provision_watcher_topic_for_instance(self):
        harness = _SystemEventHarness()
        thread = harness.subscribe(service_name="device-simple-1",
                                   base_service_name="device-simple")
        self.assertIn("edgex/system-events/core-metadata/provisionwatcher/+/device-simple/#",
                      harness.client.subscribed_topics)
        harness.stop()

    def test_error_queue_reported(self):
        logger = mock.Mock()
        harness = _SystemEventHarness(logger=logger)
        thread = harness.subscribe()
        harness.client.err_queue.put("boom")
        self.assertTrue(_wait_until(
            lambda: any("System events subscription error" in str(call)
                        for call in logger.error.call_args_list)))
        harness.stop()

    def test_undecodable_event_logged(self):
        logger = mock.Mock()
        harness = _SystemEventHarness(logger=logger)
        thread = harness.subscribe()
        q = harness.client.get_queue_for("edgex/system-events/core-metadata/+/+/device-simple/")
        q.put(MessageEnvelope(received_topic="edgex/system-events/core-metadata/device/delete/device-simple",
                              payload="not json"))
        self.assertTrue(_wait_until(
            lambda: any("Failed to decode system event" in str(call)
                        for call in logger.error.call_args_list)))
        harness.stop()

    def test_core_metadata_owner_rejects_non_profile_delete(self):
        logger = mock.Mock()
        harness = _SystemEventHarness(logger=logger)
        thread = harness.subscribe()
        q = harness.client.get_queue_for("edgex/system-events/core-metadata/+/+/device-simple/")
        q.put(MessageEnvelope(received_topic="edgex/system-events/core-metadata/device/add/device-simple",
                              payload={"type": "device", "action": "add",
                                       "owner": "core-metadata", "details": {}}))
        self.assertTrue(_wait_until(
            lambda: any("Only device profile delete supported" in str(call)
                        for call in logger.error.call_args_list)))
        self.assertEqual(harness.added_device, [])
        harness.stop()

    def test_unmatched_owner_rejected(self):
        logger = mock.Mock()
        harness = _SystemEventHarness(logger=logger)
        thread = harness.subscribe()
        q = harness.client.get_queue_for("edgex/system-events/core-metadata/+/+/device-simple/")
        q.put(MessageEnvelope(received_topic="edgex/system-events/core-metadata/device/delete/device-simple",
                              payload={"type": "device", "action": "delete",
                                       "owner": "stranger",
                                       "details": {"name": "sensor-01"}}))
        self.assertTrue(_wait_until(
            lambda: any("Unmatched system event owner" in str(call)
                        for call in logger.error.call_args_list)))
        self.assertEqual(harness.deleted_device, [])
        harness.stop()

    def test_dispatch_profile_update(self):
        harness = _SystemEventHarness()
        thread = harness.subscribe()
        q = harness.client.get_queue_for("edgex/system-events/core-metadata/+/+/device-simple/")
        q.put(MessageEnvelope(received_topic="edgex/system-events/core-metadata/deviceprofile/update/device-simple",
                              payload={"type": "deviceprofile", "action": "update",
                                       "owner": "device-simple",
                                       "details": {"name": "p1"}}))
        self.assertTrue(_wait_until(lambda: harness.updated_profile == [{"name": "p1"}]))
        harness.stop()

    def test_dispatch_profile_delete_from_core_metadata(self):
        harness = _SystemEventHarness()
        thread = harness.subscribe()
        q = harness.client.get_queue_for("edgex/system-events/core-metadata/deviceprofile/delete/")
        q.put(MessageEnvelope(received_topic="edgex/system-events/core-metadata/deviceprofile/delete/core-metadata",
                              payload={"type": "deviceprofile", "action": "delete",
                                       "owner": "core-metadata",
                                       "details": {"name": "p1"}}))
        self.assertTrue(_wait_until(lambda: harness.deleted_profile == ["p1"]))
        harness.stop()

    def test_dispatch_watcher_add(self):
        harness = _SystemEventHarness()
        thread = harness.subscribe()
        q = harness.client.get_queue_for("edgex/system-events/core-metadata/+/+/device-simple/")
        q.put(MessageEnvelope(received_topic="edgex/system-events/core-metadata/provisionwatcher/add/device-simple",
                              payload={"type": "provisionwatcher", "action": "add",
                                       "owner": "device-simple",
                                       "details": {"name": "pw-1"}}))
        self.assertTrue(_wait_until(lambda: harness.added_watcher == [{"name": "pw-1"}]))
        harness.stop()

    def test_dispatch_service_update(self):
        harness = _SystemEventHarness()
        thread = harness.subscribe()
        q = harness.client.get_queue_for("edgex/system-events/core-metadata/+/+/device-simple/")
        q.put(MessageEnvelope(received_topic="edgex/system-events/core-metadata/deviceservice/update/device-simple",
                              payload={"type": "deviceservice", "action": "update",
                                       "owner": "device-simple",
                                       "details": {"name": "device-simple"}}))
        self.assertTrue(_wait_until(
            lambda: harness.updated_service == [{"name": "device-simple"}]))
        harness.stop()

    def test_dispatch_unknown_type_warns(self):
        logger = mock.Mock()
        harness = _SystemEventHarness(logger=logger)
        thread = harness.subscribe()
        q = harness.client.get_queue_for("edgex/system-events/core-metadata/+/+/device-simple/")
        q.put(MessageEnvelope(received_topic="edgex/system-events/core-metadata/bogus/delete/device-simple",
                              payload={"type": "bogus", "action": "add",
                                       "owner": "device-simple", "details": {}}))
        self.assertTrue(_wait_until(
            lambda: any("Unknown system event type" in str(call)
                        for call in logger.warning.call_args_list)))
        harness.stop()


if __name__ == "__main__":
    unittest.main()
