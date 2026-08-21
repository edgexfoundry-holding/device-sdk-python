# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for `models/command_value.py`.

Covers the VALUETYPE_* constants, value validation, the typed scalar / array getters,
`value_to_string`, and the `create_command_value` helpers.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.models import (  # noqa: E402
    VALUETYPE_BINARY,
    VALUETYPE_BOOL,
    VALUETYPE_BOOL_ARRAY,
    VALUETYPE_FLOAT32,
    VALUETYPE_FLOAT32_ARRAY,
    VALUETYPE_FLOAT64,
    VALUETYPE_INT16,
    VALUETYPE_INT32,
    VALUETYPE_INT8,
    VALUETYPE_OBJECT,
    VALUETYPE_OBJECT_ARRAY,
    VALUETYPE_STRING,
    VALUETYPE_STRING_ARRAY,
    VALUETYPE_UINT64,
    CommandValue,
)
from device_sdk_py.models.command_value import (  # noqa: E402
    MAX_BINARY_BYTES,
    VALUETYPES,
    ValueTypeError,
    create_command_value,
    create_command_value_with_origin,
    validate,
)


def _cv(value_type, value):
    return CommandValue(device_resource_name="r1", value_type=value_type, value=value)


class TestValueTypes(unittest.TestCase):
    def test_all_types_in_set(self):
        self.assertIn(VALUETYPE_STRING, VALUETYPES)
        self.assertIn(VALUETYPE_OBJECT_ARRAY, VALUETYPES)
        self.assertGreaterEqual(len(VALUETYPES), 25)

    def test_binary_limit(self):
        self.assertEqual(MAX_BINARY_BYTES, 16777216)


class TestValidate(unittest.TestCase):
    def test_none_passes(self):
        validate(VALUETYPE_INT32, None)

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueTypeError):
            validate("Weird", 1)

    def test_type_mismatch_raises(self):
        with self.assertRaises(ValueTypeError):
            validate(VALUETYPE_INT32, "abc")
        with self.assertRaises(ValueTypeError):
            validate(VALUETYPE_STRING, 42)
        with self.assertRaises(ValueTypeError):
            validate(VALUETYPE_FLOAT64, 42)  # int not accepted for float

    def test_bool_not_an_int(self):
        with self.assertRaises(ValueTypeError):
            validate(VALUETYPE_INT32, True)

    def test_binary_size_limit(self):
        with self.assertRaises(ValueTypeError):
            validate(VALUETYPE_BINARY, b"\x00" * (MAX_BINARY_BYTES + 1))

    def test_object_accepts_anything_non_none(self):
        validate(VALUETYPE_OBJECT, {"a": 1})
        validate(VALUETYPE_OBJECT, 5)
        validate(VALUETYPE_OBJECT, None)  # None passes the generic validation


class TestCommandValueConstruction(unittest.TestCase):
    def test_constructor_validates(self):
        with self.assertRaises(ValueTypeError):
            CommandValue(device_resource_name="r", value_type=VALUETYPE_INT32, value="x")

    def test_create_command_value(self):
        cv = create_command_value("r1", VALUETYPE_STRING, "hello")
        self.assertEqual(cv.device_resource_name, "r1")
        self.assertEqual(cv.value, "hello")

    def test_create_command_value_with_origin(self):
        cv = create_command_value_with_origin("r1", VALUETYPE_INT32, 5, origin=123)
        self.assertEqual(cv.origin, 123)

    def test_str_repr(self):
        cv = _cv(VALUETYPE_STRING, "x")
        self.assertIn("r1", str(cv))


class TestScalarGetters(unittest.TestCase):
    def test_bool_value(self):
        self.assertIs(_cv(VALUETYPE_BOOL, True).bool_value(), True)
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_BOOL, "yes").bool_value()
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_STRING, "x").bool_value()

    def test_string_value(self):
        self.assertEqual(_cv(VALUETYPE_STRING, "hi").string_value(), "hi")
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_STRING, 42).string_value()

    def test_int_getters(self):
        self.assertEqual(_cv(VALUETYPE_INT8, -5).int8_value(), -5)
        self.assertEqual(_cv(VALUETYPE_INT16, -5).int16_value(), -5)
        self.assertEqual(_cv(VALUETYPE_INT32, 5).int32_value(), 5)
        self.assertEqual(_cv(VALUETYPE_UINT64, 5).uint64_value(), 5)
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_INT32, "5").int32_value()
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_STRING, "5").int32_value()

    def test_float_getters(self):
        self.assertEqual(_cv(VALUETYPE_FLOAT32, 1.5).float32_value(), 1.5)
        self.assertEqual(_cv(VALUETYPE_FLOAT64, 1.5).float64_value(), 1.5)
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_FLOAT64, 1).float64_value()
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_INT32, 1).float64_value()


class TestArrayGetters(unittest.TestCase):
    def test_bool_array(self):
        self.assertEqual(_cv(VALUETYPE_BOOL_ARRAY, [True, False]).bool_array_value(),
                         [True, False])
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_BOOL_ARRAY, [True, 1]).bool_array_value()

    def test_string_array(self):
        self.assertEqual(_cv(VALUETYPE_STRING_ARRAY, ["a", "b"]).string_array_value(),
                         ["a", "b"])
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_STRING_ARRAY, ["a", 1]).string_array_value()

    def test_int_array(self):
        self.assertEqual(_cv("Int32Array", [1, 2]).int32_array_value(), [1, 2])
        self.assertEqual(_cv("Int8Array", [1, 2]).int8_array_value(), [1, 2])
        self.assertEqual(_cv("Int16Array", [1, 2]).int16_array_value(), [1, 2])
        with self.assertRaises(ValueTypeError):
            _cv("Int32Array", [1, "x"]).int32_array_value()

    def test_uint_array(self):
        self.assertEqual(_cv("Uint8Array", [1, 2]).uint8_array_value(), [1, 2])
        self.assertEqual(_cv("Uint16Array", [1, 2]).uint16_array_value(), [1, 2])
        self.assertEqual(_cv("Uint32Array", [1, 2]).uint32_array_value(), [1, 2])
        self.assertEqual(_cv(VALUETYPE_UINT64 + "Array", [1, 2]).uint64_array_value(),
                         [1, 2])
        with self.assertRaises(ValueTypeError):
            _cv("Uint8Array", [1, "x"]).uint8_array_value()

    def test_float_array(self):
        self.assertEqual(_cv(VALUETYPE_FLOAT32_ARRAY, [1.5]).float32_array_value(), [1.5])
        self.assertEqual(_cv(VALUETYPE_FLOAT64 + "Array", [1.5]).float64_array_value(), [1.5])

    def test_object_array(self):
        self.assertEqual(_cv(VALUETYPE_OBJECT_ARRAY, [{"a": 1}]).object_array_value(),
                         [{"a": 1}])
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_OBJECT_ARRAY, [[1]]).object_array_value()


class TestOtherGetters(unittest.TestCase):
    def test_binary_value(self):
        self.assertEqual(_cv(VALUETYPE_BINARY, b"abc").binary_value(), b"abc")
        self.assertIsNone(_cv(VALUETYPE_BINARY, None).binary_value())
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_BINARY, "abc").binary_value()

    def test_object_value(self):
        self.assertEqual(_cv(VALUETYPE_OBJECT, {"a": 1}).object_value(), {"a": 1})
        with self.assertRaises(ValueTypeError):
            _cv(VALUETYPE_STRING, "x").object_value()

    def test_value_to_string_binary_truncated(self):
        cv = _cv(VALUETYPE_BINARY, b"\x00" * 100)
        s = cv.value_to_string()
        self.assertTrue(s.startswith("Binary: ["))
        self.assertIn("...]", s)

    def test_value_to_string_simple(self):
        self.assertEqual(_cv(VALUETYPE_STRING, "abc").value_to_string(), "abc")


if __name__ == "__main__":
    unittest.main()
