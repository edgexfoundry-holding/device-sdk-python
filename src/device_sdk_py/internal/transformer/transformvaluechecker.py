# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The transformed-value range checker - ported from
`device-sdk-go/internal/transformer/transformvaluechecker.go`.

`check_transformed_value_in_range` verifies that a float64 value produced by one of the
value transformations still fits in the range of the original value type.  Python ints and
floats do not carry a width / precision, so - unlike the Go version which dispatches on the
concrete Go type - this port takes the EdgeX value type as an explicit parameter.
"""

from __future__ import annotations

import math
from typing import Any

from ...models import (
    VALUETYPE_FLOAT32,
    VALUETYPE_FLOAT64,
    VALUETYPE_INT8,
    VALUETYPE_INT16,
    VALUETYPE_INT32,
    VALUETYPE_INT64,
    VALUETYPE_UINT8,
    VALUETYPE_UINT16,
    VALUETYPE_UINT32,
    VALUETYPE_UINT64,
)

# Maximum float32 value (Go `math.MaxFloat32`).
MAX_FLOAT32 = 3.4028234663852886e+38

_MAX_UINT8 = 2 ** 8 - 1
_MAX_UINT16 = 2 ** 16 - 1
_MAX_UINT32 = 2 ** 32 - 1
# Go converts math.MaxUint64 to float64 which rounds up to 2**64.
_MAX_UINT64 = float(2 ** 64 - 1)
_MIN_INT8 = -(2 ** 7)
_MAX_INT8 = 2 ** 7 - 1
_MIN_INT16 = -(2 ** 15)
_MAX_INT16 = 2 ** 15 - 1
_MIN_INT32 = -(2 ** 31)
_MAX_INT32 = 2 ** 31 - 1
_MIN_INT64 = float(-(2 ** 63))
_MAX_INT64 = float(2 ** 63 - 1)

_INTEGER_VALUE_TYPES = frozenset({
    VALUETYPE_UINT8, VALUETYPE_UINT16, VALUETYPE_UINT32, VALUETYPE_UINT64,
    VALUETYPE_INT8, VALUETYPE_INT16, VALUETYPE_INT32, VALUETYPE_INT64,
})


def check_transformed_value_in_range(origin: Any, value_type: str, transformed: float) -> bool:
    """Return True when `transformed` can be represented as the original value type.

    Mirrors `checkTransformedValueInRange(origin, transformed)` in
    transformvaluechecker.go.  For integer types the value must be an exact integer within
    the type range; for float32 it must not be NaN and its absolute value must not exceed
    `MAX_FLOAT32`; for float64 it must not be NaN or infinite.  `value_type` is passed
    explicitly since Python values do not carry a width/precision (the Go version dispatches
    on the concrete type of `origin`).  `origin` is kept for parity with the Go signature.
    """
    if value_type == VALUETYPE_UINT8:
        return 0 <= transformed <= _MAX_UINT8 and math.trunc(transformed) == transformed
    if value_type == VALUETYPE_UINT16:
        return 0 <= transformed <= _MAX_UINT16 and math.trunc(transformed) == transformed
    if value_type == VALUETYPE_UINT32:
        return 0 <= transformed <= _MAX_UINT32 and math.trunc(transformed) == transformed
    if value_type == VALUETYPE_UINT64:
        return 0 <= transformed <= _MAX_UINT64 and math.trunc(transformed) == transformed
    if value_type == VALUETYPE_INT8:
        return _MIN_INT8 <= transformed <= _MAX_INT8 and math.trunc(transformed) == transformed
    if value_type == VALUETYPE_INT16:
        return _MIN_INT16 <= transformed <= _MAX_INT16 and math.trunc(transformed) == transformed
    if value_type == VALUETYPE_INT32:
        return _MIN_INT32 <= transformed <= _MAX_INT32 and math.trunc(transformed) == transformed
    if value_type == VALUETYPE_INT64:
        return _MIN_INT64 <= transformed <= _MAX_INT64 and math.trunc(transformed) == transformed
    if value_type == VALUETYPE_FLOAT32:
        return not math.isnan(float(transformed)) and math.fabs(transformed) <= MAX_FLOAT32
    if value_type == VALUETYPE_FLOAT64:
        return not math.isnan(float(transformed)) and not math.isinf(float(transformed))
    return False
