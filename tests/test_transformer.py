# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the data transformation pipeline.

Covers `internal/transformer/`:
- checknan / transformvaluechecker (NaN + range checking)
- transformresult (mask/shift/base/scale/offset read transforms, assertion, mapping)
- transformparam (write parameter validation + reverse transforms)
- transform (CommandValue -> Event with all reading kinds + error paths)
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.cache import (  # noqa: E402
    Device,
    DeviceProfile,
    DeviceResource,
    Devices,
    Profiles,
    ResourceProperties,
)
from device_sdk_py.internal.cache.devices import create_device_cache  # noqa: E402
from device_sdk_py.internal.cache.profiles import create_profile_cache  # noqa: E402
from device_sdk_py.models import (  # noqa: E402
    VALUETYPE_BINARY,
    VALUETYPE_BOOL,
    VALUETYPE_FLOAT32,
    VALUETYPE_FLOAT64,
    VALUETYPE_INT8,
    VALUETYPE_INT16,
    VALUETYPE_INT32,
    VALUETYPE_INT64,
    VALUETYPE_OBJECT,
    VALUETYPE_OBJECT_ARRAY,
    VALUETYPE_STRING,
    VALUETYPE_UINT8,
    VALUETYPE_UINT16,
    VALUETYPE_UINT32,
    VALUETYPE_UINT64,
    CommandValue,
    create_command_value,
)
from device_sdk_py.internal.transformer.checknan import check_nan, is_nan  # noqa: E402
from device_sdk_py.internal.transformer.transformvaluechecker import (  # noqa: E402
    check_transformed_value_in_range,
)
from device_sdk_py.internal.transformer.transformresult import (  # noqa: E402
    OVERFLOW,
    NAN,
    NaNTransformerError,
    OverflowTransformerError,
    TransformerError,
    check_assertion,
    command_value_for_transform,
    is_numeric_value_type,
    map_command_value,
    transform_base,
    transform_mask,
    transform_offset,
    transform_read_result,
    transform_scale,
    transform_shift,
)
from device_sdk_py.internal.transformer.transformparam import (  # noqa: E402
    WriteParameterError,
    transform_write_parameter,
    validate_write_maximum,
    validate_write_minimum,
)
from device_sdk_py.internal.transformer import transform as tf  # noqa: E402
from device_sdk_py.internal.transformer.transform import (  # noqa: E402
    command_value_to_reading,
    command_values_to_event,
    get_unique_origin,
)


def _cv(name, value_type, value):
    return CommandValue(device_resource_name=name, value_type=value_type, value=value)


def _props(**kw):
    defaults = dict(value_type=VALUETYPE_INT32)
    defaults.update(kw)
    return ResourceProperties(**defaults)


class TestCheckNan(unittest.TestCase):
    def test_nan_float64(self):
        self.assertTrue(is_nan(_cv("x", VALUETYPE_FLOAT64, float("nan"))))
        self.assertTrue(check_nan(float("nan"), VALUETYPE_FLOAT64))

    def test_non_nan(self):
        self.assertFalse(is_nan(_cv("x", VALUETYPE_FLOAT64, 1.5)))
        self.assertFalse(is_nan(_cv("x", VALUETYPE_INT32, 5)))
        self.assertFalse(is_nan(_cv("x", VALUETYPE_FLOAT64, None)))
        self.assertFalse(check_nan(5, VALUETYPE_INT32))
        self.assertFalse(check_nan(None, VALUETYPE_FLOAT64))


class TestRangeChecker(unittest.TestCase):
    def test_uint8_boundaries(self):
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_UINT8, 255))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_UINT8, 256))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_UINT8, -1))

    def test_int8_boundaries(self):
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_INT8, -128))
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_INT8, 127))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_INT8, 128))

    def test_uint16_boundaries(self):
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_UINT16, 65535))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_UINT16, 65536))

    def test_int16_boundaries(self):
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_INT16, -32768))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_INT16, 32768))

    def test_int64_boundaries(self):
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_INT64,
                                                         float(-(2**63))))
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_INT64,
                                                         float(2**63 - 1)))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_INT64,
                                                          -(2**63) - 1))

    def test_int32(self):
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_INT32, 2**31 - 1))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_INT32, 2**31))

    def test_uint64(self):
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_UINT64, 2**64 - 1))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_UINT64, -1))

    def test_fractional_integer_rejected(self):
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_INT32, 1.5))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_UINT16, 1.5))

    def test_float32(self):
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_FLOAT32, 1.5))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_FLOAT32,
                                                          float("nan")))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_FLOAT32,
                                                          3.5e38))

    def test_float64(self):
        self.assertTrue(check_transformed_value_in_range(0, VALUETYPE_FLOAT64, 1e300))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_FLOAT64,
                                                          float("inf")))
        self.assertFalse(check_transformed_value_in_range(0, VALUETYPE_FLOAT64,
                                                          float("nan")))

    def test_unknown_type(self):
        self.assertFalse(check_transformed_value_in_range(0, "Weird", 1.0))


class TestTransformResult(unittest.TestCase):
    def test_is_numeric_value_type(self):
        self.assertTrue(is_numeric_value_type(_cv("x", VALUETYPE_INT64, 1)))
        self.assertFalse(is_numeric_value_type(_cv("x", VALUETYPE_STRING, "a")))

    def test_command_value_for_transform(self):
        self.assertEqual(command_value_for_transform(_cv("x", VALUETYPE_INT32, 7)), 7)
        self.assertIsNone(command_value_for_transform(_cv("x", VALUETYPE_INT32, None)))
        with self.assertRaises(TransformerError):
            command_value_for_transform(_cv("x", VALUETYPE_STRING, "a"))

    def test_transform_mask(self):
        self.assertEqual(transform_mask(0b1111, 0b1100, VALUETYPE_UINT8), 0b1100)
        self.assertEqual(transform_mask(0xFF, 0x0F, VALUETYPE_UINT8), 0x0F)

    def test_transform_mask_signed(self):
        self.assertEqual(transform_mask(-1, 0xFF, VALUETYPE_INT8), -1)

    def test_transform_shift_right(self):
        self.assertEqual(transform_shift(0b1100, 2, VALUETYPE_UINT8), 0b11)

    def test_transform_shift_left(self):
        self.assertEqual(transform_shift(0b11, -2, VALUETYPE_UINT8), 0b1100)

    def test_transform_shift_truncates(self):
        self.assertEqual(transform_shift(0xFF, 0, VALUETYPE_INT8), -1)

    def test_transform_base_read(self):
        result = transform_base(2, 3.0, True, VALUETYPE_FLOAT64)
        self.assertEqual(result, 9.0)

    def test_transform_base_read_int_type(self):
        result = transform_base(2, 2.0, True, VALUETYPE_INT8)
        self.assertEqual(result, 4)

    def test_transform_base_read_float32(self):
        result = transform_base(2, 2.0, True, VALUETYPE_FLOAT32)
        self.assertEqual(result, 4.0)

    def test_transform_base_write(self):
        result = transform_base(9, 3.0, False, VALUETYPE_FLOAT64)
        self.assertAlmostEqual(result, 2.0)

    def test_transform_scale_read_float32(self):
        result = transform_scale(1.5, 2.0, True, VALUETYPE_FLOAT32)
        self.assertEqual(result, 3.0)

    def test_transform_scale_float32_value(self):
        result = transform_scale(1.5, 2.0, True, VALUETYPE_FLOAT32)
        self.assertAlmostEqual(result, 3.0, places=6)

    def test_transform_offset_read_float32(self):
        result = transform_offset(1.5, 1.0, True, VALUETYPE_FLOAT32)
        self.assertEqual(result, 2.5)

    def test_transform_scale_read(self):
        self.assertEqual(transform_scale(2, 3.0, True, VALUETYPE_FLOAT64), 6.0)
        self.assertEqual(transform_scale(2, 3, True, VALUETYPE_INT32), 6)

    def test_transform_scale_write(self):
        self.assertEqual(transform_scale(6, 3.0, False, VALUETYPE_FLOAT64), 2.0)
        self.assertEqual(transform_scale(6, 3, False, VALUETYPE_INT32), 2)

    def test_transform_scale_trunc_div(self):
        self.assertEqual(transform_scale(-6, 3, False, VALUETYPE_INT32), -2)

    def test_transform_scale_overflow(self):
        with self.assertRaises(OverflowTransformerError):
            transform_scale(100, 10.0, True, VALUETYPE_INT8)

    def test_transform_offset_read(self):
        self.assertEqual(transform_offset(2, 3.0, True, VALUETYPE_FLOAT64), 5.0)
        self.assertEqual(transform_offset(2, 3, True, VALUETYPE_INT32), 5)

    def test_transform_offset_write(self):
        self.assertEqual(transform_offset(5, 3.0, False, VALUETYPE_FLOAT64), 2.0)

    def test_transform_offset_overflow(self):
        with self.assertRaises(OverflowTransformerError):
            transform_offset(127, 1, True, VALUETYPE_INT8)

    def test_transform_read_result_noop_for_none(self):
        cv = _cv("x", VALUETYPE_INT32, None)
        transform_read_result(cv, _props())
        self.assertIsNone(cv.value)

    def test_transform_read_result_noop_for_string(self):
        cv = _cv("x", VALUETYPE_STRING, "abc")
        transform_read_result(cv, _props(value_type=VALUETYPE_STRING))
        self.assertEqual(cv.value, "abc")

    def test_transform_read_result_nan_raises(self):
        cv = _cv("x", VALUETYPE_FLOAT64, float("nan"))
        with self.assertRaises(NaNTransformerError):
            transform_read_result(cv, _props(value_type=VALUETYPE_FLOAT64))

    def test_transform_read_result_full_pipeline(self):
        cv = _cv("x", VALUETYPE_INT32, 10)
        transform_read_result(cv, _props(mask=0xFF, shift=1, scale=2.0, offset=3.0))
        self.assertEqual(cv.value, ((10 >> 1) * 2 + 3) & 0xFF)

    def test_transform_read_result_base_pipeline(self):
        cv = _cv("x", VALUETYPE_FLOAT64, 2.0)
        transform_read_result(cv, _props(value_type=VALUETYPE_FLOAT64, base=2.0))
        self.assertEqual(cv.value, 4.0)

    def test_trunc_div_by_zero_raises(self):
        from device_sdk_py.internal.transformer.transformresult import _trunc_div
        with self.assertRaises(OverflowTransformerError):
            _trunc_div(5, 0)

    def test_transform_read_result_overflows(self):
        cv = _cv("x", VALUETYPE_INT8, 100)
        with self.assertRaises(OverflowTransformerError):
            transform_read_result(cv, _props(value_type=VALUETYPE_INT8, scale=10.0))

    def test_check_assertion_pass(self):
        cv = _cv("x", VALUETYPE_STRING, "ok")
        check_assertion(cv, "ok")

    def test_check_assertion_fail(self):
        cv = _cv("x", VALUETYPE_STRING, "ok")
        with self.assertRaises(TransformerError):
            check_assertion(cv, "not-ok")

    def test_map_command_value_hit(self):
        cv = _cv("x", VALUETYPE_STRING, "a")
        mapped = map_command_value(cv, {"a": "1", "b": "2"})
        self.assertEqual(mapped.value_type, VALUETYPE_STRING)
        self.assertEqual(mapped.value, "1")

    def test_map_command_value_miss(self):
        cv = _cv("x", VALUETYPE_STRING, "z")
        self.assertIsNone(map_command_value(cv, {"a": "1"}))


class TestTransformParam(unittest.TestCase):
    def test_validate_maximum_int(self):
        cv = _cv("x", VALUETYPE_INT32, 5)
        validate_write_maximum(cv.value, cv.value_type, 10.0)  # no raise
        with self.assertRaises(WriteParameterError):
            validate_write_maximum(cv.value, cv.value_type, 4.0)

    def test_validate_minimum_int(self):
        cv = _cv("x", VALUETYPE_INT32, 5)
        validate_write_minimum(cv.value, cv.value_type, 5.0)
        with self.assertRaises(WriteParameterError):
            validate_write_minimum(cv.value, cv.value_type, 6.0)

    def test_validate_float32(self):
        with self.assertRaises(WriteParameterError):
            validate_write_maximum(1.5, VALUETYPE_FLOAT32, 1.0)
        with self.assertRaises(WriteParameterError):
            validate_write_minimum(0.5, VALUETYPE_FLOAT32, 1.0)

    def test_validate_float64(self):
        with self.assertRaises(WriteParameterError):
            validate_write_maximum(1.5, VALUETYPE_FLOAT64, 1.0)
        with self.assertRaises(WriteParameterError):
            validate_write_minimum(0.5, VALUETYPE_FLOAT64, 1.0)

    def test_validate_string_noop(self):
        validate_write_maximum("abc", VALUETYPE_STRING, 1.0)

    def test_transform_write_parameter_reverse(self):
        cv = _cv("x", VALUETYPE_INT32, 13)
        transform_write_parameter(cv, _props(offset=3.0, scale=2.0))
        self.assertEqual(cv.value, 5)

    def test_transform_write_parameter_validation_error(self):
        cv = _cv("x", VALUETYPE_INT32, 200)
        with self.assertRaises(WriteParameterError):
            transform_write_parameter(cv, _props(maximum=100.0))

    def test_transform_write_parameter_minimum_error(self):
        cv = _cv("x", VALUETYPE_INT32, 1)
        with self.assertRaises(WriteParameterError):
            transform_write_parameter(cv, _props(minimum=10.0))

    def test_transform_write_parameter_base(self):
        cv = _cv("x", VALUETYPE_FLOAT64, 4.0)
        transform_write_parameter(cv, _props(value_type=VALUETYPE_FLOAT64, base=2.0))
        self.assertAlmostEqual(cv.value, 2.0)

    def test_transform_write_parameter_shift(self):
        cv = _cv("x", VALUETYPE_UINT8, 0b11)
        transform_write_parameter(cv, _props(value_type=VALUETYPE_UINT8, shift=2))
        self.assertEqual(cv.value, 0b1100)

    def test_transform_write_parameter_none_noop(self):
        cv = _cv("x", VALUETYPE_INT32, None)
        transform_write_parameter(cv, _props())
        self.assertIsNone(cv.value)

    def test_transform_write_parameter_string_noop(self):
        cv = _cv("x", VALUETYPE_STRING, "abc")
        transform_write_parameter(cv, _props(value_type=VALUETYPE_STRING))
        self.assertEqual(cv.value, "abc")

    def test_transform_write_parameter_mask(self):
        cv = _cv("x", VALUETYPE_UINT8, 0x0F)
        transform_write_parameter(cv, _props(value_type=VALUETYPE_UINT8, mask=0x07))
        self.assertEqual(cv.value, 0x07)


class TestTransform(unittest.TestCase):
    """transform.py: get_unique_origin / readings / command_values_to_event."""

    def setUp(self):
        create_device_cache([])
        create_profile_cache([])
        self.device = Device(name="dev1", profile_name="p1", tags={"site": "a"})
        Devices().add(self.device)
        self.profile = DeviceProfile(name="p1")
        Profiles().add(self.profile)

    def tearDown(self):
        Devices().remove_by_name("dev1")
        Profiles().remove_by_name("p1")

    def _add_resource(self, name="res1", value_type=VALUETYPE_STRING, **prop_kw):
        resource = DeviceResource(
            name=name, properties=ResourceProperties(value_type=value_type, **prop_kw))
        self.profile.device_resources.append(resource)
        Profiles().remove_by_name("p1")
        Profiles().add(self.profile)
        return resource

    def test_get_unique_origin_increasing(self):
        first = get_unique_origin()
        second = get_unique_origin()
        self.assertLess(first, second)

    def test_command_value_to_reading_simple(self):
        cv = _cv("res1", VALUETYPE_STRING, "25")
        reading = command_value_to_reading(cv, "dev1", "p1", "", 123)
        self.assertEqual(reading.value, "25")
        self.assertEqual(reading.origin, 123)

    def test_command_value_to_reading_float32(self):
        cv = _cv("res1", VALUETYPE_FLOAT32, 0.1234567)
        reading = command_value_to_reading(cv, "dev1", "p1", "", 0)
        self.assertEqual(float(reading.value), cv.value)

    def test_format_float32_round_trips(self):
        from device_sdk_py.internal.transformer.transform import (
            _format_float32,
            _to_float32,
        )
        result = _format_float32(0.1234567)
        self.assertIsInstance(result, str)
        self.assertEqual(_to_float32(float(result)), _to_float32(0.1234567))

    def test_command_value_to_reading_uses_cv_origin(self):
        cv = _cv("res1", VALUETYPE_STRING, "25")
        cv.origin = 999
        reading = command_value_to_reading(cv, "dev1", "p1", "", 123)
        self.assertEqual(reading.origin, 999)

    def test_command_value_to_reading_binary(self):
        cv = _cv("res1", VALUETYPE_BINARY, b"\x01\x02")
        reading = command_value_to_reading(cv, "dev1", "p1", "image/png", 0)
        self.assertEqual(reading.binary_value, b"\x01\x02")
        self.assertEqual(reading.media_type, "image/png")

    def test_command_value_to_reading_object(self):
        cv = _cv("res1", VALUETYPE_OBJECT, {"a": 1})
        reading = command_value_to_reading(cv, "dev1", "p1", "", 0)
        self.assertEqual(reading.object_value, {"a": 1})

    def test_command_value_to_reading_object_array(self):
        cv = _cv("res1", VALUETYPE_OBJECT_ARRAY, [{"a": 1}])
        reading = command_value_to_reading(cv, "dev1", "p1", "", 0)
        self.assertEqual(reading.object_value, [{"a": 1}])

    def test_command_value_to_reading_null(self):
        cv = _cv("res1", VALUETYPE_STRING, None)
        reading = command_value_to_reading(cv, "dev1", "p1", "", 0)
        self.assertIsNone(reading.value)

    def test_command_values_to_event_none(self):
        self.assertIsNone(command_values_to_event(None, "dev1", "src"))

    def test_command_values_to_event_missing_device(self):
        with self.assertRaises(TransformerError):
            command_values_to_event([], "ghost", "src")

    def test_command_values_to_event_basic(self):
        self._add_resource("res1")
        cv = _cv("res1", VALUETYPE_STRING, "25")
        event = command_values_to_event([cv], "dev1", "src")
        self.assertEqual(event.device_name, "dev1")
        self.assertEqual(event.source_name, "src")
        self.assertEqual(event.tags, {"site": "a"})
        self.assertEqual(event.readings[0].units, "")

    def test_command_values_to_event_skips_none_cv(self):
        self._add_resource("res1")
        event = command_values_to_event([None], "dev1", "src")
        self.assertIsNone(event)

    def test_command_values_to_event_missing_resource(self):
        cv = _cv("ghost-res", VALUETYPE_STRING, "x")
        with self.assertRaises(TransformerError):
            command_values_to_event([cv], "dev1", "src")

    def test_command_values_to_event_overflow_replaced(self):
        self._add_resource("res1", value_type=VALUETYPE_INT8, scale=100.0)
        cv = _cv("res1", VALUETYPE_INT8, 5)
        event = command_values_to_event([cv], "dev1", "src")
        self.assertEqual(event.readings[0].value, OVERFLOW)

    def test_command_values_to_event_nan_replaced(self):
        self._add_resource("res1", value_type=VALUETYPE_FLOAT64)
        cv = _cv("res1", VALUETYPE_FLOAT64, float("nan"))
        event = command_values_to_event([cv], "dev1", "src")
        self.assertEqual(event.readings[0].value, NAN)

    def test_command_values_to_event_assertion_fail_disables_device(self):
        self._add_resource("res1", assertion="expected")
        cv = _cv("res1", VALUETYPE_STRING, "actual")
        with self.assertRaises(TransformerError):
            command_values_to_event([cv], "dev1", "src")
        self.assertEqual(Devices().for_name("dev1")[0].operating_state, "DISABLED")

    def test_command_values_to_event_mapping(self):
        self._add_resource("res1")
        self.profile.device_commands = [mock.Mock(
            resource_operations=[mock.Mock(device_resource="res1")],
            mappings={"25": "hot"})]
        Profiles().remove_by_name("p1")
        Profiles().add(self.profile)
        with mock.patch.object(Profiles(), "resource_operation",
                               return_value=mock.Mock(mappings={"25": "hot"})):
            cv = _cv("res1", VALUETYPE_STRING, "25")
            event = command_values_to_event([cv], "dev1", "src")
            self.assertEqual(event.readings[0].value, "hot")

    def test_command_values_to_event_cv_tags_merged(self):
        self._add_resource("res1")
        cv = _cv("res1", VALUETYPE_STRING, "25")
        cv.tags = {"k": "v"}
        event = command_values_to_event([cv], "dev1", "src")
        self.assertEqual(event.tags["k"], "v")

    def test_command_values_to_event_reading_units_false(self):
        self._add_resource("res1", units="C")
        cv = _cv("res1", VALUETYPE_STRING, "25")
        event = command_values_to_event([cv], "dev1", "src", reading_units=False)
        self.assertEqual(event.readings[0].units, "")

    def test_command_values_to_event_transform_error_aborts(self):
        self._add_resource("res1")
        cv = _cv("res1", VALUETYPE_STRING, "25")
        with mock.patch.object(tf, "transform_read_result",
                               side_effect=TransformerError("boom")):
            with self.assertRaises(TransformerError):
                command_values_to_event([cv], "dev1", "src")


if __name__ == "__main__":
    unittest.main()
