# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The read-result value transformations - ported from
`device-sdk-go/internal/transformer/transformresult.go`.

`transform_read_result` performs the outgoing (read) data transformation on a CommandValue
using the ResourceProperties of its DeviceResource.  The transformation order is Mask,
Shift, Base, Scale, Offset as defined by the EdgeX Device Service data transformation ADR
(https://docs.edgexfoundry.org/4.0/design/adr/device-service/0011-DeviceService-Rest-API/#data-transformations).

The Go functions return `errors.EdgeX`; this port raises exceptions instead.  The
`TransformerError` hierarchy mirrors the Go error kinds used here: `OverflowTransformerError`
for `KindOverflowError`, `NaNTransformerError` for `KindNaNError`.
"""

from __future__ import annotations

import math
import struct
from typing import Any, Dict, Optional

from ...models import (
    VALUETYPE_FLOAT32,
    VALUETYPE_FLOAT64,
    VALUETYPE_INT8,
    VALUETYPE_INT16,
    VALUETYPE_INT32,
    VALUETYPE_INT64,
    VALUETYPE_STRING,
    VALUETYPE_UINT8,
    VALUETYPE_UINT16,
    VALUETYPE_UINT32,
    VALUETYPE_UINT64,
    CommandValue,
    new_command_value,
)
from .checknan import is_nan
from .transformvaluechecker import (
    _INTEGER_VALUE_TYPES,
    check_transformed_value_in_range,
)

# Default values for the optional transformation properties (Go constants).
DEFAULT_BASE = 0.0
DEFAULT_SCALE = 1.0
DEFAULT_OFFSET = 0.0
DEFAULT_MASK = 0
DEFAULT_SHIFT = 0

#: The string values used to replace a reading that overflows / is NaN (Go constants).
OVERFLOW = "overflow"
NAN = "NaN"

_NUMERIC_VALUE_TYPES = frozenset({
    VALUETYPE_UINT8, VALUETYPE_UINT16, VALUETYPE_UINT32, VALUETYPE_UINT64,
    VALUETYPE_INT8, VALUETYPE_INT16, VALUETYPE_INT32, VALUETYPE_INT64,
    VALUETYPE_FLOAT32, VALUETYPE_FLOAT64,
})

_SIGNED_VALUE_TYPES = frozenset({
    VALUETYPE_INT8, VALUETYPE_INT16, VALUETYPE_INT32, VALUETYPE_INT64,
})

# Bit width of each integer value type (used to emulate Go integer truncation).
_INT_WIDTHS = {
    VALUETYPE_UINT8: 8,
    VALUETYPE_UINT16: 16,
    VALUETYPE_UINT32: 32,
    VALUETYPE_UINT64: 64,
    VALUETYPE_INT8: 8,
    VALUETYPE_INT16: 16,
    VALUETYPE_INT32: 32,
    VALUETYPE_INT64: 64,
}


class TransformerError(Exception):
    """Base class for value transformation errors.

    Python counterpart of the `errors.EdgeX` errors returned by the Go transformer
    functions.
    """


class OverflowTransformerError(TransformerError):
    """Raised when a transformed value does not fit in its original value type range.

    Python counterpart of `errors.KindOverflowError`.
    """


class NaNTransformerError(TransformerError):
    """Raised when a floating point CommandValue holds a NaN value.

    Python counterpart of `errors.KindNaNError`.
    """


def is_numeric_value_type(cv: CommandValue) -> bool:
    """Return True when the CommandValue holds one of the numeric value types.

    Mirrors `isNumericValueType(cv *models.CommandValue)` in transformresult.go.
    """
    return cv.value_type in _NUMERIC_VALUE_TYPES


def _is_integer_value_type(value_type: str) -> bool:
    return value_type in _INTEGER_VALUE_TYPES


def _to_float32(value: float) -> float:
    """Round a float64 to the closest float32 and return it as a Python float.

    Python only has float64, so this emulates Go's `float32` conversion used by the
    Float32 value transforms.
    """
    return struct.unpack("f", struct.pack("f", value))[0]


def _trunc_div(a: int, b: int) -> int:
    """Integer division truncating toward zero (matches Go `/` on integers)."""
    if b == 0:
        raise OverflowTransformerError("integer divide by zero")
    quotient = abs(a) // abs(b)
    return quotient if (a >= 0) == (b >= 0) else -quotient


def _to_signed(value: int, bits: int) -> int:
    """Interpret a bit pattern of `bits` width as a signed two's-complement integer."""
    if value & (1 << (bits - 1)):
        return value - (1 << bits)
    return value


def _truncate_int(value: int, value_type: str) -> int:
    """Truncate an integer to the bit width of the given value type.

    Emulates the implicit Go type conversions (e.g. `uint8(...)`, `int8(...)`) applied to
    the result of a bitwise / arithmetic transformation.
    """
    bits = _INT_WIDTHS[value_type]
    masked = value & ((1 << bits) - 1)
    if value_type in _SIGNED_VALUE_TYPES:
        return _to_signed(masked, bits)
    return masked


def command_value_for_transform(cv: CommandValue) -> Any:
    """Return the numeric value of the CommandValue ready for transformation.

    Mirrors `commandValueForTransform(cv *models.CommandValue)` in transformresult.go.
    Raises `TransformerError` for an unsupported (non-numeric) value type.  A `None` value
    is returned as-is.
    """
    if cv.value is None:
        return None
    if not is_numeric_value_type(cv):
        raise TransformerError(f"unsupported ValueType for transformation: {cv.value_type}")
    return cv.value


def transform_mask(value: int, mask: int, value_type: str) -> int:
    """Apply a bitwise AND mask to the value, truncated to the value type width.

    Mirrors `transformMask(value any, mask uint64)` in transformresult.go.  `value_type`
    is passed explicitly since Python ints do not carry a width; the mask is truncated to
    the type width and the result is sign-interpreted for signed types, exactly like the
    Go `v & uint8(mask)` / `v & int8(mask)` expressions.
    """
    bits = _INT_WIDTHS[value_type]
    truncated_mask = mask & ((1 << bits) - 1)
    result = value & truncated_mask
    if value_type in _SIGNED_VALUE_TYPES:
        return _to_signed(result, bits)
    return result


def transform_shift(value: int, shift: int, value_type: str) -> int:
    """Apply a bit shift to the value, truncated to the value type width.

    Mirrors `transformShift(value any, shift int64)` in transformresult.go.  Positive
    values indicate a right shift, negative a left shift.  The result is truncated to the
    value type width to emulate the Go type conversion of the shifted value.
    """
    if shift > 0:
        return _truncate_int(value >> shift, value_type)
    return _truncate_int(value << (-shift), value_type)


def _to_value_float(value: Any, value_type: str) -> float:
    """Convert the value to the float64 used by the transformations.

    Float32 values are rounded to float32 precision first, emulating the Go `float32`
    storage of the value.
    """
    if value_type == VALUETYPE_FLOAT32:
        return _to_float32(float(value))
    return float(value)


def _from_value_float(value_float: float, value_type: str) -> Any:
    """Convert the transformed float64 back to the original value type.

    Emulates the Go `uint8(...)` / `int8(...)` / `float32(...)` conversions.
    """
    if value_type == VALUETYPE_FLOAT32:
        return _to_float32(value_float)
    if value_type == VALUETYPE_FLOAT64:
        return value_float
    return _truncate_int(int(value_float), value_type)


def _check_in_range(value: Any, value_type: str, value_float: float) -> None:
    if not check_transformed_value_in_range(value, value_type, value_float):
        raise OverflowTransformerError(
            f"transformed value out of its original type ({value_type}) range")


def transform_base(value: Any, base: float, read: bool, value_type: str) -> Any:
    """Apply the base transformation (`base ** value` when reading, the inverse otherwise).

    Mirrors `transformBase(value any, base float64, read bool)` in transformresult.go.
    Raises `OverflowTransformerError` when the transformed value leaves the original type
    range.
    """
    value_float = _to_value_float(value, value_type)
    if read:
        value_float = math.pow(base, value_float)
    else:
        value_float = math.log(value_float) / math.log(base)
    _check_in_range(value, value_type, value_float)
    return _from_value_float(value_float, value_type)


def transform_scale(value: Any, scale: float, read: bool, value_type: str) -> Any:
    """Apply the scale transformation (`value * scale` when reading, `/` otherwise).

    Mirrors `transformScale(value any, scale float64, read bool)` in transformresult.go.
    For integer value types the Go implementation computes the result with integer
    arithmetic using the truncated scale; this is replicated here.  Raises
    `OverflowTransformerError` when the transformed value leaves the original type range.
    """
    value_float = _to_value_float(value, value_type)
    if read:
        value_float = value_float * scale
    else:
        value_float = value_float / scale
    _check_in_range(value, value_type, value_float)

    if value_type in _INTEGER_VALUE_TYPES:
        int_scale = int(scale)
        if read:
            return _truncate_int(value * int_scale, value_type)
        return _truncate_int(_trunc_div(value, int_scale), value_type)
    if value_type == VALUETYPE_FLOAT32:
        return _to_float32(value_float)
    return value_float


def transform_offset(value: Any, offset: float, read: bool, value_type: str) -> Any:
    """Apply the offset transformation (`value + offset` when reading, `-` otherwise).

    Mirrors `transformOffset(value any, offset float64, read bool)` in transformresult.go.
    For integer value types the Go implementation computes the result with integer
    arithmetic using the truncated offset; this is replicated here.  Raises
    `OverflowTransformerError` when the transformed value leaves the original type range.
    """
    value_float = _to_value_float(value, value_type)
    if read:
        value_float = value_float + offset
    else:
        value_float = value_float - offset
    _check_in_range(value, value_type, value_float)

    if value_type in _INTEGER_VALUE_TYPES:
        int_offset = int(offset)
        if read:
            return _truncate_int(value + int_offset, value_type)
        return _truncate_int(value - int_offset, value_type)
    if value_type == VALUETYPE_FLOAT32:
        return _to_float32(value_float)
    return value_float


def transform_read_result(cv: CommandValue, properties: Any) -> None:
    """Transform the outgoing (read) value of the CommandValue in place.

    Mirrors `TransformReadResult(cv *models.CommandValue, pv models.ResourceProperties)`
    in transformresult.go.  Applies Mask, Shift, Base, Scale and Offset in that order to
    numeric values only.

    Raises:
        NaNTransformerError: When the value is NaN (Float32 / Float64).
        OverflowTransformerError: When a transformed value leaves its type range.
        TransformerError: For an unsupported value type.
    """
    if cv.value is None:
        # In Go the caller (CommandValuesToEventDTO) guards the call with `cv.Value != nil`,
        # so a nil value is never transformed.
        return
    if not is_numeric_value_type(cv):
        return
    if is_nan(cv):
        raise NaNTransformerError(f"NaN error for DeviceResource {cv.device_resource_name}")

    value = command_value_for_transform(cv)
    new_value = value

    if properties.mask is not None and properties.mask != DEFAULT_MASK and \
            _is_integer_value_type(cv.value_type):
        new_value = transform_mask(new_value, properties.mask, cv.value_type)
    if properties.shift is not None and properties.shift != DEFAULT_SHIFT and \
            _is_integer_value_type(cv.value_type):
        new_value = transform_shift(new_value, properties.shift, cv.value_type)
    if properties.base is not None and properties.base != DEFAULT_BASE:
        new_value = transform_base(new_value, properties.base, True, cv.value_type)
    if properties.scale is not None and properties.scale != DEFAULT_SCALE:
        new_value = transform_scale(new_value, properties.scale, True, cv.value_type)
    if properties.offset is not None and properties.offset != DEFAULT_OFFSET:
        new_value = transform_offset(new_value, properties.offset, True, cv.value_type)

    if value != new_value:
        cv.value = new_value


def check_assertion(cv: CommandValue, assertion: str) -> None:
    """Verify that the CommandValue's string form matches the assertion string.

    Mirrors `checkAssertion(cv, assertion, deviceName, lc, dc)` in transformresult.go,
    minus the Go side effect of setting the Device OperatingState to Down (which is a
    service-level concern handled by the caller of `command_values_to_event`).  Raises
    `TransformerError` when the assertion is set and does not match.
    """
    if assertion != "" and cv.value_to_string() != assertion:
        raise TransformerError(
            f"Assertion failed for DeviceResource {cv.device_resource_name}, "
            f"with value {cv.value_to_string()}")


def map_command_value(cv: CommandValue, mappings: Dict[str, str]) -> Optional[CommandValue]:
    """Map the CommandValue's string form to the mapped value, producing a String
    CommandValue.

    Mirrors `mapCommandValue(value *models.CommandValue, mappings map[string]string)` in
    transformresult.go.  Returns None when no mapping exists for the value.
    """
    key = cv.value_to_string()
    if key not in mappings:
        return None
    return new_command_value(cv.device_resource_name, VALUETYPE_STRING, mappings[key])


# PascalCase aliases kept for parity with the Go exported identifiers.
TransformReadResult = transform_read_result
