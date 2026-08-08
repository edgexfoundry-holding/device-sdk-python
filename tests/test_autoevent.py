# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the auto-event scheduler (executor + manager).

Covers `internal/autoevent/executor.py` and `internal/autoevent/manager.py`:
- duration parsing (Go time.ParseDuration grammar)
- on_change / threshold / binary checksum comparison
- the executor loop (start/stop/run on daemon threads)
- manager scheduling, restart, stop-all, and read/send plumbing
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.autoevent import executor as ex  # noqa: E402
from device_sdk_py.internal.autoevent.executor import (  # noqa: E402
    AutoEventError,
    AutoEventExecutor,
    create_executor,
    parse_duration,
)
from device_sdk_py.internal.autoevent import manager as mgr  # noqa: E402
from device_sdk_py.internal.autoevent.manager import AutoEventManager  # noqa: E402
from device_sdk_py.internal.cache import AutoEvent, Device  # noqa: E402
from device_sdk_py.internal.cache.providers import (  # noqa: E402
    ADMIN_STATE_LOCKED,
    ADMIN_STATE_UNLOCKED,
)
from device_sdk_py.internal.common.consts import OPERATING_STATE_DOWN  # noqa: E402
from device_sdk_py.models import (  # noqa: E402
    VALUETYPE_BINARY,
    VALUETYPE_FLOAT32,
    VALUETYPE_INT32,
    VALUETYPE_STRING,
    AsyncValues,
    CommandValue,
)


def _cv(name, value_type, value):
    return CommandValue(device_resource_name=name, value_type=value_type, value=value)


def _resource(name="Temp"):
    r = mock.Mock()
    r.name = name
    r.attributes = {"unit": "C"}
    r.properties = mock.Mock(value_type=VALUETYPE_INT32)
    return r


class TestParseDuration(unittest.TestCase):
    """Go duration grammar."""

    def test_zero(self):
        self.assertEqual(parse_duration("0"), 0.0)
        self.assertEqual(parse_duration("-0"), 0.0)

    def test_single_units(self):
        self.assertAlmostEqual(parse_duration("300ms"), 0.3)
        self.assertEqual(parse_duration("1.5h"), 5400.0)
        self.assertAlmostEqual(parse_duration("10us"), 1e-5)
        self.assertAlmostEqual(parse_duration("10µs"), 1e-5)
        self.assertAlmostEqual(parse_duration("100ns"), 1e-7)
        self.assertEqual(parse_duration("30s"), 30.0)
        self.assertEqual(parse_duration("2m"), 120.0)

    def test_compound(self):
        self.assertEqual(parse_duration("2h45m"), 9900.0)
        self.assertAlmostEqual(parse_duration("1m30s"), 90.0)

    def test_signs(self):
        self.assertEqual(parse_duration("+30s"), 30.0)
        self.assertEqual(parse_duration("-30s"), -30.0)

    def test_invalid(self):
        for bad in ("", "abc", "30", "30X", "3.2.1s", "s", "1.5"):
            with self.assertRaises(AutoEventError, msg=bad):
                parse_duration(bad)


class TestChecksum(unittest.TestCase):
    def test_matches_zlib(self):
        import zlib
        data = b"hello world"
        self.assertEqual(ex._checksum(data), zlib.crc32(data))


class TestToFloat(unittest.TestCase):
    def test_numeric(self):
        self.assertEqual(ex._to_float(42), 42.0)
        self.assertEqual(ex._to_float("3.5"), 3.5)

    def test_unparseable(self):
        self.assertEqual(ex._to_float("abc"), 0.0)
        self.assertEqual(ex._to_float(None), 0.0)


class TestAutoEventExecutor(unittest.TestCase):
    """Executor construction + sync execution paths."""

    def _executor(self, interval="10s", **kwargs):
        event = AutoEvent(source_name="Temperature", interval=interval, **kwargs)
        return AutoEventExecutor(
            device_name="device1",
            auto_event=event,
            read_handler=lambda d, s: None,
            send_handler=None)

    def test_interval_property(self):
        self.assertEqual(self._executor(interval="5s").interval, 5.0)

    def test_invalid_interval_raises_at_construction(self):
        with self.assertRaises(AutoEventError):
            self._executor(interval="garbage")

    def test_create_executor(self):
        event = AutoEvent(source_name="S", interval="1s")
        inst = create_executor("d", event, read_handler=lambda d, s: None)
        self.assertIsInstance(inst, AutoEventExecutor)
        self.assertEqual(inst.device_name, "d")
        self.assertEqual(inst.source_name, "S")

    def test_execute_once_sends_values(self):
        sent = []
        executor = AutoEventExecutor(
            device_name="device1",
            auto_event=AutoEvent(source_name="S", interval="1s"),
            read_handler=lambda d, s: [_cv("Temp", VALUETYPE_INT32, 25)],
            send_handler=sent.append)
        executor._execute_once()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].device_name, "device1")
        self.assertEqual(sent[0].command_values[0].value, 25)
        self.assertTrue(sent[0].origin > 0)

    def test_execute_once_no_values_skips_send(self):
        sent = []
        executor = AutoEventExecutor(
            device_name="device1",
            auto_event=AutoEvent(source_name="S", interval="1s"),
            read_handler=lambda d, s: None,
            send_handler=sent.append)
        executor._execute_once()
        self.assertEqual(sent, [])

    def test_execute_once_read_error_skips_send(self):
        sent = []

        def bad(d, s):
            raise RuntimeError("read failed")

        executor = AutoEventExecutor(
            device_name="device1",
            auto_event=AutoEvent(source_name="S", interval="1s"),
            read_handler=bad,
            send_handler=sent.append)
        with mock.patch.object(ex, "_logger") as log:
            executor._execute_once()
            self.assertEqual(sent, [])

    def test_on_change_skips_unchanged(self):
        sent = []
        executor = AutoEventExecutor(
            device_name="d",
            auto_event=AutoEvent(source_name="S", interval="1s", on_change=True),
            read_handler=lambda d, s: [_cv("Temp", VALUETYPE_INT32, 25)],
            send_handler=sent.append)
        executor._execute_once()
        executor._execute_once()
        self.assertEqual(len(sent), 1)

    def test_on_change_sends_on_change(self):
        sent = []
        values = [25]

        def read(d, s):
            return [_cv("Temp", VALUETYPE_INT32, values[0])]

        executor = AutoEventExecutor(
            device_name="d",
            auto_event=AutoEvent(source_name="S", interval="1s", on_change=True),
            read_handler=read,
            send_handler=sent.append)
        executor._execute_once()
        values[0] = 30
        executor._execute_once()
        self.assertEqual(len(sent), 2)

    def test_on_change_within_threshold_skipped(self):
        sent = []
        values = [25.0]

        def read(d, s):
            return [_cv("Temp", VALUETYPE_FLOAT32, values[0])]

        executor = AutoEventExecutor(
            device_name="d",
            auto_event=AutoEvent(source_name="S", interval="1s",
                                 on_change=True, on_change_threshold=5.0),
            read_handler=read,
            send_handler=sent.append)
        executor._execute_once()
        values[0] = 27.0
        executor._execute_once()
        self.assertEqual(len(sent), 1)

    def test_changed_readings_only(self):
        sent = []
        values = [25]

        def read(d, s):
            return [_cv("Temp", VALUETYPE_INT32, values[0]),
                    _cv("Humidity", VALUETYPE_INT32, 50)]

        executor = AutoEventExecutor(
            device_name="d",
            auto_event=AutoEvent(source_name="S", interval="1s", on_change=True),
            read_handler=read,
            send_handler=sent.append,
            send_changed_readings_only=True)
        executor._execute_once()
        values[0] = 30
        executor._execute_once()
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[1].command_values, [sent[1].command_values[0]])

    def test_compare_binary_checksum(self):
        executor = self._executor()
        executor._compare_readings([_cv("Img", VALUETYPE_BINARY, b"aaa")])
        unchanged = executor._compare_readings([_cv("Img", VALUETYPE_BINARY, b"aaa")])
        self.assertTrue(unchanged)
        changed = executor._compare_readings([_cv("Img", VALUETYPE_BINARY, b"bbb")])
        self.assertFalse(changed)

    def test_compare_extra_reading_renews(self):
        executor = self._executor()
        executor._compare_readings([_cv("A", VALUETYPE_INT32, 1)])
        unchanged = executor._compare_readings(
            [_cv("A", VALUETYPE_INT32, 1), _cv("B", VALUETYPE_INT32, 2)])
        self.assertFalse(unchanged)

    def test_start_stop_loop(self):
        reads = []
        executor = AutoEventExecutor(
            device_name="d",
            auto_event=AutoEvent(source_name="S", interval="0.01s"),
            read_handler=lambda d, s: (reads.append(1), [_cv("V", VALUETYPE_INT32, 1)])[1],
            send_handler=lambda av: None)
        executor.start()
        self.assertIsNotNone(executor._thread)
        deadline = time.monotonic() + 2.0
        while len(reads) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        executor.stop()
        executor._thread.join(timeout=2.0)
        self.assertGreaterEqual(len(reads), 2)

    def test_start_idempotent(self):
        executor = self._executor()
        executor.start()
        thread = executor._thread
        executor.start()
        self.assertIs(executor._thread, thread)
        executor.stop()


class TestAutoEventManager(unittest.TestCase):
    """Manager scheduling logic."""

    def setUp(self):
        self.service = mock.Mock()
        self.service._logger = mock.Mock()
        self.service.configuration = None
        self.manager = AutoEventManager(self.service)
        self.devices_patcher = mock.patch.object(mgr, "Devices")
        self.profiles_patcher = mock.patch.object(mgr, "Profiles")
        self.mock_devices = self.devices_patcher.start()
        self.mock_profiles = self.profiles_patcher.start()
        self.addCleanup(self.devices_patcher.stop)
        self.addCleanup(self.profiles_patcher.stop)

    def _device(self, name="device1", auto_events=(), admin=ADMIN_STATE_UNLOCKED,
                operating_state="UP"):
        return Device(name=name, profile_name="profile1", admin_state=admin,
                      operating_state=operating_state, auto_events=list(auto_events))

    def _events(self, *intervals):
        return [AutoEvent(source_name="Temp", interval=i) for i in intervals]

    def test_start_auto_events_schedules(self):
        devices = [self._device(auto_events=self._events("1s", "2s"))]
        self.mock_devices.return_value.all.return_value = devices
        with mock.patch.object(mgr, "create_executor") as create:
            executors = [mock.Mock() for _ in range(2)]
            create.side_effect = executors
            self.manager.start_auto_events()
            self.assertEqual(create.call_count, 2)
            for e in executors:
                e.start.assert_called_once()
        self.assertEqual(len(self.manager._executors["device1"]), 2)

    def test_start_skips_locked_and_down(self):
        devices = [
            self._device("locked", self._events("1s"), admin=ADMIN_STATE_LOCKED),
            self._device("down", self._events("1s"), operating_state=OPERATING_STATE_DOWN),
            self._device("noevents", []),
        ]
        self.mock_devices.return_value.all.return_value = devices
        with mock.patch.object(mgr, "create_executor") as create:
            self.manager.start_auto_events()
            create.assert_not_called()

    def test_start_skips_empty_source_name(self):
        devices = [self._device(auto_events=[AutoEvent(source_name="", interval="1s")])]
        self.mock_devices.return_value.all.return_value = devices
        with mock.patch.object(mgr, "create_executor") as create:
            self.manager.start_auto_events()
            create.assert_not_called()

    def test_restart_for_device(self):
        old = mock.Mock()
        self.manager._executors["device1"] = [old]
        self.mock_devices.return_value.for_name.return_value = (
            self._device(auto_events=self._events("1s")), True)
        with mock.patch.object(mgr, "create_executor") as create:
            create.return_value = mock.Mock()
            self.manager.restart_for_device("device1")
            old.stop.assert_called_once()
            create.assert_called_once()
        self.assertIn("device1", self.manager._executors)

    def test_restart_missing_device_warns(self):
        self.mock_devices.return_value.for_name.return_value = (None, False)
        self.manager.restart_for_device("ghost")
        self.service._logger.warning.assert_called()

    def test_stop_all(self):
        e1, e2 = mock.Mock(), mock.Mock()
        self.manager._executors["d1"] = [e1]
        self.manager._executors["d2"] = [e2]
        self.manager.stop_all()
        e1.stop.assert_called_once()
        e2.stop.assert_called_once()
        self.assertEqual(self.manager._executors, {})

    def test_read_uses_driver_and_send(self):
        self.service.driver = mock.Mock()
        device = self._device()
        self.mock_devices.return_value.for_name.return_value = (device, True)
        self.mock_profiles.return_value.device_command.return_value = (None, False)
        self.mock_profiles.return_value.device_resource.return_value = (
            _resource(), True)
        with mock.patch.object(mgr, "_handle_read_commands") as handle:
            handle.return_value = [_cv("Temp", VALUETYPE_INT32, 25)]
            result = self.manager._read("device1", "Temp")
            self.assertEqual(result[0].value, 25)

    def test_read_no_driver(self):
        self.service.driver = None
        self.assertIsNone(self.manager._read("device1", "Temp"))

    def test_read_device_not_found(self):
        self.service.driver = mock.Mock()
        self.mock_devices.return_value.for_name.return_value = (None, False)
        self.assertIsNone(self.manager._read("device1", "Temp"))

    def test_send_puts_on_channel(self):
        channel = mock.Mock()
        self.service.async_values_channel.return_value = channel
        av = AsyncValues(device_name="d", source_name="S", command_values=[])
        self.manager._send(av)
        channel.put.assert_called_once_with(av)


class TestBuildRequests(unittest.TestCase):
    """source_name resolution to CommandRequests."""

    def setUp(self):
        self.service = mock.Mock()
        self.service._logger = mock.Mock()
        self.manager = AutoEventManager(self.service)
        self.profiles_patcher = mock.patch.object(mgr, "Profiles")
        self.mock_profiles = self.profiles_patcher.start()
        self.addCleanup(self.profiles_patcher.stop)

    def _resource(self, name="Temp"):
        return _resource(name)

    def test_resolves_device_command(self):
        op = mock.Mock(device_resource="Temp")
        command = mock.Mock(resource_operations=[op])
        self.mock_profiles.return_value.device_command.return_value = (command, True)
        self.mock_profiles.return_value.device_resource.return_value = (
            self._resource(), True)
        device = Device(name="d", profile_name="p", auto_events=[])
        requests = self.manager._build_requests(device, "GetTemp")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].resource_name, "Temp")
        self.assertEqual(requests[0].value_type, VALUETYPE_INT32)
        self.assertEqual(requests[0].attributes, {"unit": "C"})

    def test_resolves_device_resource(self):
        self.mock_profiles.return_value.device_command.return_value = (None, False)
        self.mock_profiles.return_value.device_resource.return_value = (
            self._resource(), True)
        device = Device(name="d", profile_name="p", auto_events=[])
        requests = self.manager._build_requests(device, "Temp")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].resource_name, "Temp")

    def test_unknown_source_warns(self):
        self.mock_profiles.return_value.device_command.return_value = (None, False)
        self.mock_profiles.return_value.device_resource.return_value = (None, False)
        device = Device(name="d", profile_name="p", auto_events=[])
        self.assertIsNone(self.manager._build_requests(device, "Nope"))
        self.service._logger.warning.assert_called()


if __name__ == "__main__":
    unittest.main()
