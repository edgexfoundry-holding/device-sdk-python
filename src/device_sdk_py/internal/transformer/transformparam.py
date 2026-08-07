# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/internal/transformer/transformparam.go`.

`transform_write_parameter` performs the incoming (write) data transformation on a
CommandValue using the ResourceProperties of its DeviceResource. The incoming data
transformation order (Maximum / Minimum validation, Offset, Scale, Base, Shift, Mask) is
the reverse of the outgoing order and is defined by the EdgeX Device Service data
transformation ADR.

The Go functions return `errors.EdgeX`; this port raises exceptions instead.
"""

from __future__ import annotations

from typing import Any

from ..cache import ResourceProperties
from ...models import (
    VALUETYPE_FLOAT32,
    VALUETYPE_FLOAT64,
    CommandValue,
)
from .transformresult import (
    DEFAULT_BASE,
    DEFAULT_MASK,
    DEFAULT_OFFSET,
    DEFAULT_SCALE,
    DEFAULT_SHIFT,
    TransformerError,
    _INTEGER_VALUE_TYPES,
    _is_integer_value_type,
    _to_float32,
    command_value_for_transform,
    is_numeric_value_type,
    transform_base,
    transform_mask,
    transform_offset,
    transform_scale,
    transform_shift,
)


class WriteParameterError(TransformerError):
    """Raised when a write parameter is out of the allowed Maximum / Minimum range.

    KindContractInvalid` error returned by the Go
    `validateWriteMaximum` / `validateWriteMinimum` functions.
    """


def _maximum_message(maximum: float) -> str:
    return f"set command parameter out of maximum value {maximum}"


def _minimum_message(minimum: float) -> str:
    return f"set command parameter out of minimum value {minimum}"


def validate_write_maximum(value: Any, value_type: str, maximum: float) -> None:
    """Raise `WriteParameterError` when the value exceeds the configured maximum.

    The
    maximum is truncated to the value type (Go `uint8(maximum)` / `int8(maximum)` /
    `float32(maximum)`) before comparison.
    """
    if _is_integer_value_type(value_type):
        if value > int(maximum):
            raise WriteParameterError(_maximum_message(maximum))
    elif value_type == VALUETYPE_FLOAT32:
        if value > _to_float32(maximum):
            raise WriteParameterError(_maximum_message(maximum))
    elif value_type == VALUETYPE_FLOAT64:
        if value > maximum:
            raise WriteParameterError(_maximum_message(maximum))


def validate_write_minimum(value: Any, value_type: str, minimum: float) -> None:
    """Raise `WriteParameterError` when the value is below the configured minimum.

    The
    minimum is truncated to the value type (Go `uint8(minimum)` / `int8(minimum)` /
    `float32(minimum)`) before comparison.
    """
    if _is_integer_value_type(value_type):
        if value < int(minimum):
            raise WriteParameterError(_minimum_message(minimum))
    elif value_type == VALUETYPE_FLOAT32:
        if value < _to_float32(minimum):
            raise WriteParameterError(_minimum_message(minimum))
    elif value_type == VALUETYPE_FLOAT64:
        if value < minimum:
            raise WriteParameterError(_minimum_message(minimum))


def transform_write_parameter(cv: CommandValue, properties: ResourceProperties) -> None:
    """Transform the incoming (write) value of the CommandValue in place.

Validates the value against the configured Maximum / Minimum and
then applies Offset, Scale, Base, Shift and Mask in that order. A `None` value or a
    non-numeric value type is left untouched.

    Raises:
        WriteParameterError: When the value is out of the Maximum / Minimum range.
        OverflowTransformerError: When a transformed value leaves its type range.
        TransformerError: For an unsupported value type.
    """
    if cv.value is None:
        return
    if not is_numeric_value_type(cv):
        return

    value = command_value_for_transform(cv)
    new_value = value

    if properties.maximum is not None:
        validate_write_maximum(value, cv.value_type, properties.maximum)
    if properties.minimum is not None:
        validate_write_minimum(value, cv.value_type, properties.minimum)
    if properties.offset is not None and properties.offset != DEFAULT_OFFSET:
        new_value = transform_offset(new_value, properties.offset, False, cv.value_type)
    if properties.scale is not None and properties.scale != DEFAULT_SCALE:
        new_value = transform_scale(new_value, properties.scale, False, cv.value_type)
    if properties.base is not None and properties.base != DEFAULT_BASE:
        new_value = transform_base(new_value, properties.base, False, cv.value_type)
    if properties.shift is not None and properties.shift != DEFAULT_SHIFT and \
            _is_integer_value_type(cv.value_type):
        # use a negated shift value to reuse the shift function for the reversed operation
        new_value = transform_shift(new_value, -properties.shift, cv.value_type)
    if properties.mask is not None and properties.mask != DEFAULT_MASK and \
            _is_integer_value_type(cv.value_type):
        new_value = transform_mask(new_value, properties.mask, cv.value_type)

    if value != new_value:
        cv.value = new_value


