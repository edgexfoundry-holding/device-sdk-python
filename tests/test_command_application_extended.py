# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Extended unit tests for `internal/application/command.py`.

Complements `test_command_application.py` by targeting the branches it leaves
uncovered: the full write-value coercion matrix
(`create_command_value_from_device_resource`), the regex / DeviceCommand read
paths, the DeviceResource / DeviceCommand write paths, the shared helper error
handling and the remaining `device_request_failed` / `device_request_succeeded`
/ `_validate_service_and_device_state` branches.
"""

from __future__ import annotations

import base64
import os
import struct
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.application import command  # noqa: E402
from device_sdk_py.internal.cache import (  # noqa: E402
    ADMIN_STATE_LOCKED,
    Device,
    DeviceCommand,
    DeviceProfile,
    DeviceResource,
    Devices,
    Profiles,
    ResourceOperation,
    ResourceProperties,
)
from device_sdk_py.internal.cache.devices import create_device_cache  # noqa: E402
from device_sdk_py.internal.cache.profiles import create_profile_cache  # noqa: E402
from device_sdk_py.internal.common.consts import (  # noqa: E402
    OPERATING_STATE_DOWN,
    OPERATING_STATE_UP,
    READ_WRITE_R,
    READ_WRITE_W,
)
from device_sdk_py.internal.common.utils import (  # noqa: E402
    EdgexError,
    KIND_CONTRACT_INVALID,
    KIND_ENTITY_DOES_NOT_EXIST,
    KIND_NOT_ALLOWED,
    KIND_SERVER_ERROR,
    KIND_SERVICE_LOCKED,
)
from device_sdk_py.models import (  # noqa: E402
    VALUETYPE_BOOL,
    VALUETYPE_BOOL_ARRAY,
    VALUETYPE_FLOAT32,
    VALUETYPE_FLOAT32_ARRAY,
    VALUETYPE_FLOAT64,
    VALUETYPE_FLOAT64_ARRAY,
    VALUETYPE_INT8,
    VALUETYPE_INT8_ARRAY,
    VALUETYPE_INT16,
    VALUETYPE_INT16_ARRAY,
    VALUETYPE_INT32,
    VALUETYPE_INT32_ARRAY,
    VALUETYPE_INT64,
    VALUETYPE_INT64_ARRAY,
    VALUETYPE_OBJECT,
    VALUETYPE_OBJECT_ARRAY,
    VALUETYPE_STRING,
    VALUETYPE_STRING_ARRAY,
    VALUETYPE_UINT8,
    VALUETYPE_UINT8_ARRAY,
    VALUETYPE_UINT16,
    VALUETYPE_UINT16_ARRAY,
    VALUETYPE_UINT32,
    VALUETYPE_UINT32_ARRAY,
    VALUETYPE_UINT64,
    VALUETYPE_UINT64_ARRAY,
    CommandValue,
)


class _MockConfig:
    def __init__(self, **options):
        self.device = type("_MockDevice", (), options)()


def _resource(name="res1", value_type=VALUETYPE_STRING, read_write="RW",
              default_value=""):
    return DeviceResource(
        name=name,
        properties=ResourceProperties(value_type=value_type,
                                      read_write=read_write,
                                      default_value=default_value),
        attributes={"foo": "bar"})


def _profile(name="p1", resources=()):
    profile = DeviceProfile(name=name)
    profile.device_resources = list(resources)
    return profile


class CommandAppBase(unittest.TestCase):
    def setUp(self):
        create_device_cache([])
        create_profile_cache([])

    def tearDown(self):
        command._allowed_request_failures.clear()


class TestCoercion(CommandAppBase):
    """`create_command_value_from_device_resource` value-type matrix."""

    def _cv(self, value_type, value):
        return command.create_command_value_from_device_resource(
            _resource(value_type=value_type), value)

    def _assert_value(self, value_type, value, expected):
        cv = self._cv(value_type, value)
        self.assertEqual(cv.value_type, value_type)
        self.assertEqual(cv.value, expected)

    def test_none_value(self):
        cv = command.create_command_value_from_device_resource(
            _resource(), None)
        self.assertIsNone(cv.value)

    def test_string(self):
        self._assert_value(VALUETYPE_STRING, 42, "42")

    def test_string_array(self):
        self._assert_value(VALUETYPE_STRING_ARRAY, '["a","b"]', ["a", "b"])

    def test_bool_and_array(self):
        self._assert_value(VALUETYPE_BOOL, "true", True)
        self._assert_value(VALUETYPE_BOOL, "1", True)
        self._assert_value(VALUETYPE_BOOL, "0", False)
        self._assert_value(VALUETYPE_BOOL_ARRAY, "[true,false]", [True, False])

    def test_uint_types(self):
        self._assert_value(VALUETYPE_UINT8, "255", 255)
        self._assert_value(VALUETYPE_UINT16, "65535", 65535)
        self._assert_value(VALUETYPE_UINT32, "4294967295", 4294967295)
        self._assert_value(VALUETYPE_UINT64, "18446744073709551615",
                           18446744073709551615)

    def test_uint_arrays(self):
        self._assert_value(VALUETYPE_UINT8_ARRAY, "[1,2,3]", [1, 2, 3])
        self._assert_value(VALUETYPE_UINT16_ARRAY, "1,2,3", [1, 2, 3])
        self._assert_value(VALUETYPE_UINT32_ARRAY, "[10,20]", [10, 20])
        self._assert_value(VALUETYPE_UINT64_ARRAY, "[5,6]", [5, 6])

    def test_int_types(self):
        self._assert_value(VALUETYPE_INT8, "-128", -128)
        self._assert_value(VALUETYPE_INT16, "-32768", -32768)
        self._assert_value(VALUETYPE_INT32, "2147483647", 2147483647)
        self._assert_value(VALUETYPE_INT64, "-9223372036854775808",
                           -9223372036854775808)

    def test_int_arrays(self):
        self._assert_value(VALUETYPE_INT8_ARRAY, "[-1,2]", [-1, 2])
        self._assert_value(VALUETYPE_INT16_ARRAY, "[1,-2]", [1, -2])
        self._assert_value(VALUETYPE_INT32_ARRAY, "[1,2]", [1, 2])
        self._assert_value(VALUETYPE_INT64_ARRAY, "[1,2]", [1, 2])

    def test_float_types(self):
        self._assert_value(VALUETYPE_FLOAT32, "1.5", 1.5)
        self._assert_value(VALUETYPE_FLOAT64, "2.5", 2.5)
        self._assert_value(VALUETYPE_FLOAT32_ARRAY, "[1.5,2.5]", [1.5, 2.5])
        self._assert_value(VALUETYPE_FLOAT64_ARRAY, "[1.5,2.5]", [1.5, 2.5])

    def test_object_types(self):
        self._assert_value(VALUETYPE_OBJECT, '{"k": 1}', {"k": 1})
        self._assert_value(VALUETYPE_OBJECT, {"k": 1}, {"k": 1})
        self._assert_value(VALUETYPE_OBJECT_ARRAY, '[{"k": 1}]', [{"k": 1}])
        self._assert_value(VALUETYPE_OBJECT_ARRAY, [{"k": 1}], [{"k": 1}])

    def test_object_none(self):
        self._assert_value(VALUETYPE_OBJECT, None, None)
        self._assert_value(VALUETYPE_OBJECT_ARRAY, None, None)

    def test_float_base64_fallback(self):
        f32 = base64.b64encode(struct.pack(">f", 2.0)).decode()
        self._assert_value(VALUETYPE_FLOAT32, f32, 2.0)
        f64 = base64.b64encode(struct.pack(">d", 2.0)).decode()
        self._assert_value(VALUETYPE_FLOAT64, f64, 2.0)

    # -- error branches ----------------------------------------------------

    def test_empty_string_non_string(self):
        for value_type in (VALUETYPE_UINT8, VALUETYPE_INT32, VALUETYPE_BOOL):
            with self.assertRaises(EdgexError) as ctx:
                self._cv(value_type, "  ")
            self.assertEqual(ctx.exception.kind, KIND_CONTRACT_INVALID)

    def test_unrecognized_value_type(self):
        with self.assertRaises(EdgexError) as ctx:
            self._cv("Bogus", "1")
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_bool_invalid(self):
        with self.assertRaises(EdgexError) as ctx:
            self._cv(VALUETYPE_BOOL, "yes")
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_bad_json_array(self):
        with self.assertRaises(EdgexError) as ctx:
            self._cv(VALUETYPE_STRING_ARRAY, "[not-json")
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_uint_errors(self):
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_UINT8, "256")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_UINT8, "-1")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_UINT8, "abc")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_UINT16_ARRAY, "[1,99999]")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_UINT32_ARRAY, "[-1]")

    def test_int_errors(self):
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_INT8, "128")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_INT8, "-129")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_INT16_ARRAY, "[1,true]")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_INT32_ARRAY, "4000000000")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_INT8_ARRAY, "[1,300]")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_INT64, "abc")

    def test_float_inf_and_nan(self):
        with self.assertRaises(EdgexError) as ctx:
            self._cv(VALUETYPE_FLOAT32, "inf")
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)
        with self.assertRaises(EdgexError) as ctx:
            self._cv(VALUETYPE_FLOAT64, "Infinity")
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)
        nan32 = base64.b64encode(struct.pack(">f", float("nan"))).decode()
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_FLOAT32, nan32)
        nan64 = base64.b64encode(struct.pack(">d", float("nan"))).decode()
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_FLOAT64, nan64)

    def test_float_short_bytes(self):
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_FLOAT32, "AA==")

    def test_object_errors(self):
        with self.assertRaises(EdgexError) as ctx:
            self._cv(VALUETYPE_OBJECT, "[1,2]")
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_OBJECT, "{bad")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_OBJECT, 5)
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_OBJECT_ARRAY, "5")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_OBJECT_ARRAY, "{bad")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_OBJECT_ARRAY, 5)
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_OBJECT_ARRAY, "[1,2]")
        with self.assertRaises(EdgexError):
            self._cv(VALUETYPE_OBJECT_ARRAY, '[{"k":1}, 2]')

    def test_normalize_object_none_direct(self):
        self.assertIsNone(command._normalize_to_object(None, "null"))
        self.assertIsNone(command._normalize_to_object_array(None, "null"))


class TestReadPaths(CommandAppBase):
    def setUp(self):
        super().setUp()
        Profiles().add(_profile("p1", [
            _resource("r1", read_write="RW"),
            _resource("w1", read_write="W"),
        ]))
        Profiles().add(_profile("p2", [
            _resource("rw", read_write="RW"),
        ]))
        self.device = Device(name="dev", profile_name="p1",
                             operating_state=OPERATING_STATE_UP)
        Devices().add(self.device)

    def _driver(self, values=None):
        driver = mock.Mock()
        driver.handle_read_commands.return_value = values or [
            CommandValue(device_resource_name="r1", value_type="String", value="25")]
        return driver

    def test_empty_names(self):
        cfg = _MockConfig()
        with self.assertRaises(EdgexError) as ctx:
            command.command_read("", "id", "cmd", driver=mock.Mock(),
                                 configuration=cfg)
        self.assertEqual(ctx.exception.kind, KIND_CONTRACT_INVALID)
        with self.assertRaises(EdgexError) as ctx:
            command.command_read("dev", "id", "", driver=mock.Mock(),
                                 configuration=cfg)
        self.assertEqual(ctx.exception.kind, KIND_CONTRACT_INVALID)

    def test_regex_read_success(self):
        driver = self._driver()
        event = command.command_read(
            "dev", "req", "r.*", driver=driver, configuration=_MockConfig(),
            regex_cmd=True, device_service=None, logger=mock.Mock())
        self.assertIsNotNone(event)
        driver.handle_read_commands.assert_called_once()
        reqs = driver.handle_read_commands.call_args[0][2]
        self.assertEqual([r.resource_name for r in reqs], ["r1"])

    def test_regex_read_bad_pattern(self):
        with self.assertRaises(EdgexError) as ctx:
            command.command_read(
                "dev", "req", "[", driver=mock.Mock(),
                configuration=_MockConfig(), regex_cmd=True)
        self.assertEqual(ctx.exception.kind, KIND_CONTRACT_INVALID)

    def test_regex_read_no_match(self):
        with self.assertRaises(EdgexError) as ctx:
            command.command_read(
                "dev", "req", "zzz", driver=mock.Mock(),
                configuration=_MockConfig(), regex_cmd=True)
        self.assertEqual(ctx.exception.kind, KIND_ENTITY_DOES_NOT_EXIST)

    def test_regex_read_all_write_only(self):
        driver = mock.Mock()
        with self.assertRaises(EdgexError) as ctx:
            command.command_read(
                "dev", "req", "w.*", driver=driver,
                configuration=_MockConfig(), regex_cmd=True)
        self.assertEqual(ctx.exception.kind, KIND_NOT_ALLOWED)
        driver.handle_read_commands.assert_not_called()

    def test_read_single_missing_resource(self):
        with self.assertRaises(EdgexError) as ctx:
            command.command_read(
                "dev", "req", "ghost", driver=mock.Mock(),
                configuration=_MockConfig(), regex_cmd=False)
        self.assertEqual(ctx.exception.kind, KIND_ENTITY_DOES_NOT_EXIST)

    def test_read_device_command(self):
        self._add_command("cmd1", [ResourceOperation(device_resource="r1")])
        driver = self._driver()
        event = command.command_read(
            "dev", "req", "cmd1", driver=driver,
            configuration=_MockConfig(), regex_cmd=False)
        self.assertIsNotNone(event)
        driver.handle_read_commands.assert_called_once()

    def test_read_command_write_only(self):
        self._add_command("wcmd", [ResourceOperation(device_resource="r1")],
                          read_write=READ_WRITE_W)
        with self.assertRaises(EdgexError) as ctx:
            command.command_read(
                "dev", "req", "wcmd", driver=mock.Mock(),
                configuration=_MockConfig(), regex_cmd=False)
        self.assertEqual(ctx.exception.kind, KIND_NOT_ALLOWED)

    def test_read_command_exceeds_max_cmd_ops(self):
        self._add_command(
            "big", [ResourceOperation(device_resource="r1"),
                    ResourceOperation(device_resource="w1")])
        cfg = _MockConfig(max_cmd_ops=1)
        with self.assertRaises(EdgexError) as ctx:
            command.command_read(
                "dev", "req", "big", driver=mock.Mock(),
                configuration=cfg, regex_cmd=False)
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_read_command_missing_resource(self):
        self._add_command("bad", [ResourceOperation(device_resource="ghost")])
        with self.assertRaises(EdgexError) as ctx:
            command.command_read(
                "dev", "req", "bad", driver=mock.Mock(),
                configuration=_MockConfig(), regex_cmd=False)
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_read_command_not_found_direct(self):
        with self.assertRaises(EdgexError) as ctx:
            command._read_device_command(self.device, "ghost", "", mock.Mock(),
                                         _MockConfig())
        self.assertEqual(ctx.exception.kind, KIND_ENTITY_DOES_NOT_EXIST)

    def test_driver_exception_wrapped(self):
        driver = mock.Mock()
        driver.handle_read_commands.side_effect = RuntimeError("boom")
        with self.assertRaises(EdgexError) as ctx:
            command.command_read(
                "dev", "req", "r1", driver=driver,
                configuration=_MockConfig(), regex_cmd=False)
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_transform_error_wrapped(self):
        driver = self._driver([CommandValue(device_resource_name="r1",
                                            value_type="Int32", value=10)])
        cfg = _MockConfig(data_transform=True, reading_units=True)
        with mock.patch.object(command,
                               "command_values_to_event",
                               side_effect=command.TransformerError("boom")):
            with self.assertRaises(EdgexError) as ctx:
                command.command_read(
                    "dev", "req", "r1", driver=driver,
                    configuration=cfg, regex_cmd=False)
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def _add_command(self, name, operations, read_write="RW"):
        profile = Profiles().for_name("p1")[0]
        profile.device_commands = [
            DeviceCommand(name=name, read_write=read_write,
                          resource_operations=operations)]
        Profiles().update(profile)


class TestWritePaths(CommandAppBase):
    def setUp(self):
        super().setUp()
        Profiles().add(_profile("p1", [
            _resource("r1", read_write="RW"),
            _resource("ro", read_write=READ_WRITE_R, value_type="String"),
            _resource("def", read_write="RW", value_type="String",
                      default_value="dflt"),
        ]))
        Profiles().add(_profile("p2", [
            _resource("rw", read_write="RW"),
        ]))
        self.device = Device(name="dev", profile_name="p1",
                             operating_state=OPERATING_STATE_UP)
        Devices().add(self.device)

    def _driver(self):
        driver = mock.Mock()
        driver.handle_write_commands.return_value = None
        return driver

    def test_empty_names(self):
        cfg = _MockConfig()
        with self.assertRaises(EdgexError) as ctx:
            command.command_write("", "id", "cmd", driver=mock.Mock(),
                                  configuration=cfg, requests={})
        self.assertEqual(ctx.exception.kind, KIND_CONTRACT_INVALID)
        with self.assertRaises(EdgexError) as ctx:
            command.command_write("dev", "id", "", driver=mock.Mock(),
                                  configuration=cfg, requests={})
        self.assertEqual(ctx.exception.kind, KIND_CONTRACT_INVALID)

    def test_write_resource_success(self):
        driver = self._driver()
        event = command.command_write(
            "dev", "req", "r1", driver=driver,
            configuration=_MockConfig(), requests={"r1": "5"})
        self.assertIsNotNone(event)
        driver.handle_write_commands.assert_called_once()

    def test_write_resource_uses_default(self):
        driver = self._driver()
        command.command_write(
            "dev", "req", "def", driver=driver,
            configuration=_MockConfig(), requests={})
        params = driver.handle_write_commands.call_args[0][3]
        self.assertEqual(params[0].value, "dflt")

    def test_write_resource_missing(self):
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "dev", "req", "ghost", driver=mock.Mock(),
                configuration=_MockConfig(), requests={})
        self.assertEqual(ctx.exception.kind, KIND_ENTITY_DOES_NOT_EXIST)

    def test_write_read_only_resource(self):
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "dev", "req", "ro", driver=mock.Mock(),
                configuration=_MockConfig(), requests={"ro": "x"})
        self.assertEqual(ctx.exception.kind, KIND_NOT_ALLOWED)

    def test_write_resource_no_value_no_default(self):
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "dev", "req", "r1", driver=mock.Mock(),
                configuration=_MockConfig(), requests={})
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_write_resource_bad_value(self):
        Profiles().add(_profile("pbad", [
            _resource("n1", value_type=VALUETYPE_INT32, read_write="RW")]))
        bdev = Device(name="bdev", profile_name="pbad",
                      operating_state=OPERATING_STATE_UP)
        Devices().add(bdev)
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "bdev", "req", "n1", driver=mock.Mock(),
                configuration=_MockConfig(), requests={"n1": "not-an-int"})
        self.assertEqual(ctx.exception.kind, KIND_CONTRACT_INVALID)

    def test_write_resource_write_only_no_event(self):
        Profiles().add(_profile("p3", [
            _resource("w1", read_write=READ_WRITE_W, value_type="String")]))
        wdev = Device(name="wdev", profile_name="p3",
                      operating_state=OPERATING_STATE_UP)
        Devices().add(wdev)
        driver = self._driver()
        event = command.command_write(
            "wdev", "req", "w1", driver=driver,
            configuration=_MockConfig(), requests={"w1": "x"})
        self.assertIsNone(event)
        driver.handle_write_commands.assert_called_once()

    def test_write_command_success(self):
        self._add_command("cmd1", [ResourceOperation(device_resource="r1")])
        driver = self._driver()
        event = command.command_write(
            "dev", "req", "cmd1", driver=driver,
            configuration=_MockConfig(), requests={"r1": "5"})
        self.assertIsNotNone(event)
        driver.handle_write_commands.assert_called_once()

    def test_write_command_not_found(self):
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "dev", "req", "ghost", driver=mock.Mock(),
                configuration=_MockConfig(), requests={})
        self.assertEqual(ctx.exception.kind, KIND_ENTITY_DOES_NOT_EXIST)

    def test_write_command_not_found_direct(self):
        with self.assertRaises(EdgexError) as ctx:
            command._write_device_command(self.device, "ghost", "", {},
                                          mock.Mock(), _MockConfig())
        self.assertEqual(ctx.exception.kind, KIND_ENTITY_DOES_NOT_EXIST)

    def test_write_read_only_command(self):
        self._add_command("rocmd", [ResourceOperation(device_resource="r1")],
                          read_write=READ_WRITE_R)
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "dev", "req", "rocmd", driver=mock.Mock(),
                configuration=_MockConfig(), requests={"r1": "5"})
        self.assertEqual(ctx.exception.kind, KIND_NOT_ALLOWED)

    def test_write_command_exceeds_max_cmd_ops(self):
        self._add_command(
            "big", [ResourceOperation(device_resource="r1"),
                    ResourceOperation(device_resource="def")])
        cfg = _MockConfig(max_cmd_ops=1)
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "dev", "req", "big", driver=mock.Mock(),
                configuration=cfg, requests={"r1": "5"})
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_write_command_missing_resource(self):
        self._add_command("bad", [ResourceOperation(device_resource="ghost")])
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "dev", "req", "bad", driver=mock.Mock(),
                configuration=_MockConfig(), requests={})
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_write_command_operation_defaults(self):
        self._add_command("opdef", [ResourceOperation(device_resource="def",
                                                     default_value="opd")])
        driver = self._driver()
        command.command_write(
            "dev", "req", "opdef", driver=driver,
            configuration=_MockConfig(), requests={})
        params = driver.handle_write_commands.call_args[0][3]
        self.assertEqual(params[0].value, "opd")

    def test_write_command_resource_default(self):
        self._add_command("resdef", [ResourceOperation(device_resource="def")])
        driver = self._driver()
        command.command_write(
            "dev", "req", "resdef", driver=driver,
            configuration=_MockConfig(), requests={})
        params = driver.handle_write_commands.call_args[0][3]
        self.assertEqual(params[0].value, "dflt")

    def test_write_command_no_default(self):
        self._add_command("nodef", [ResourceOperation(device_resource="r1")])
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "dev", "req", "nodef", driver=mock.Mock(),
                configuration=_MockConfig(), requests={})
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def test_write_command_mapping(self):
        op = ResourceOperation(device_resource="r1", mappings={"0": "off"})
        self._add_command("mapped", [op])
        driver = self._driver()
        command.command_write(
            "dev", "req", "mapped", driver=driver,
            configuration=_MockConfig(), requests={"r1": "off"})
        params = driver.handle_write_commands.call_args[0][3]
        self.assertEqual(params[0].value, "0")

    def test_write_command_bad_value(self):
        Profiles().add(_profile("p4", [
            _resource("n1", value_type=VALUETYPE_INT32, read_write="RW")]))
        ndev = Device(name="ndev", profile_name="p4",
                      operating_state=OPERATING_STATE_UP)
        Devices().add(ndev)
        self._add_command("bad", [ResourceOperation(device_resource="n1")],
                          profile="p4")
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "ndev", "req", "bad", driver=mock.Mock(),
                configuration=_MockConfig(), requests={"n1": "xyz"})
        self.assertEqual(ctx.exception.kind, KIND_CONTRACT_INVALID)

    def test_write_command_write_only_no_event(self):
        self._add_command("wcmd", [ResourceOperation(device_resource="r1")],
                          read_write=READ_WRITE_W)
        driver = self._driver()
        event = command.command_write(
            "dev", "req", "wcmd", driver=driver,
            configuration=_MockConfig(), requests={"r1": "5"})
        self.assertIsNone(event)

    def test_driver_exception_wrapped(self):
        driver = mock.Mock()
        driver.handle_write_commands.side_effect = RuntimeError("boom")
        with self.assertRaises(EdgexError) as ctx:
            command.command_write(
                "dev", "req", "r1", driver=driver,
                configuration=_MockConfig(), requests={"r1": "5"})
        self.assertEqual(ctx.exception.kind, KIND_SERVER_ERROR)

    def _add_command(self, name, operations, read_write="RW", profile="p1"):
        current = Profiles().for_name(profile)[0]
        current.device_commands = [
            DeviceCommand(name=name, read_write=read_write,
                          resource_operations=operations)]
        Profiles().update(current)


class TestStateAndFailureTracking(CommandAppBase):
    def setUp(self):
        super().setUp()
        Profiles().add(_profile("p1", [_resource("r1")]))
        self.device = Device(name="dev", profile_name="p1",
                             operating_state=OPERATING_STATE_UP)
        Devices().add(self.device)

    def test_device_admin_locked(self):
        device = Devices().for_name("dev")[0]
        device.admin_state = ADMIN_STATE_LOCKED
        Devices().update(device)
        with self.assertRaises(EdgexError) as ctx:
            command._validate_service_and_device_state(
                "dev", _MockConfig())
        self.assertEqual(ctx.exception.kind, KIND_SERVICE_LOCKED)

    def test_operating_down_no_retry_config(self):
        device = Devices().for_name("dev")[0]
        device.operating_state = OPERATING_STATE_DOWN
        Devices().update(device)
        cfg = _MockConfig(allowed_fails=0)
        with self.assertRaises(EdgexError) as ctx:
            command._validate_service_and_device_state("dev", cfg)
        self.assertEqual(ctx.exception.kind, KIND_SERVICE_LOCKED)

    def test_empty_profile_name(self):
        device = Devices().for_name("dev")[0]
        device.profile_name = ""
        Devices().update(device)
        with self.assertRaises(EdgexError) as ctx:
            command._validate_service_and_device_state(
                "dev", _MockConfig(allowed_fails=3, device_down_timeout=30))
        self.assertEqual(ctx.exception.kind, KIND_SERVICE_LOCKED)

    def test_device_request_failed_missing_device_noop(self):
        command._allowed_request_failures["ghost"] = 1
        command.device_request_failed(
            "ghost", _MockConfig(allowed_fails=1), mock.Mock())
        self.assertEqual(command.failure_count("ghost"), 0)

    def test_device_request_failed_via_device_service(self):
        service = mock.Mock()
        service.update_device_operating_state = mock.Mock()
        command._allowed_request_failures["dev"] = 1
        cfg = _MockConfig(allowed_fails=1, device_down_timeout=30)
        command.device_request_failed("dev", cfg, mock.Mock(), service)
        self.assertEqual(command.failure_count("dev"), 0)
        service.update_device_operating_state.assert_called_once_with(
            "dev", OPERATING_STATE_DOWN)

    def test_device_request_succeeded_via_device_service(self):
        service = mock.Mock()
        service.update_device_operating_state = mock.Mock()
        device = Devices().for_name("dev")[0]
        device.operating_state = OPERATING_STATE_DOWN
        Devices().update(device)
        command.set_failure_count("dev", 0)
        command.device_request_succeeded(
            Devices().for_name("dev")[0], _MockConfig(allowed_fails=3),
            mock.Mock(), service)
        self.assertEqual(command.failure_count("dev"), 3)
        service.update_device_operating_state.assert_called_once_with(
            "dev", OPERATING_STATE_UP)

    def test_attributes_query_stored(self):
        req = mock.Mock()
        req.attributes = {}
        command._set_attributes_query(req, "x=1")
        self.assertEqual(req.attributes, {"urlRawQuery": "x=1"})
        req.attributes = {"a": 1}
        command._set_attributes_query(req, "x=1")
        self.assertEqual(req.attributes, {"a": 1, "urlRawQuery": "x=1"})


if __name__ == "__main__":
    unittest.main()
