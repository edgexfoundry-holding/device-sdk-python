# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The NaN checker - ported from `device-sdk-go/internal/transformer/checkNaN.go`.

`is_nan` reports whether a CommandValue of a float value type holds a NaN value, which
cannot be transformed into a reading.
"""

from __future__ import annotations

import math
from typing import Any

from ...models import CommandValue, VALUETYPE_FLOAT32, VALUETYPE_FLOAT64


def is_nan(cv: CommandValue) -> bool:
    """Return True when the CommandValue holds a NaN floating point value.

    Mirrors `isNaN(cv *models.CommandValue)` in checkNaN.go.  Only `Float32` and `Float64`
    values are inspected; `None` values and other value types never produce NaN.
    """
    if cv.value_type not in (VALUETYPE_FLOAT32, VALUETYPE_FLOAT64):
        return False
    if cv.value is None:
        return False
    return math.isnan(float(cv.value))


def check_nan(value: Any, value_type: str) -> bool:
    """Return True when `value` is a NaN for the given (floating point) value type.

    Convenience form of `is_nan` taking the value and value type directly.
    """
    if value_type not in (VALUETYPE_FLOAT32, VALUETYPE_FLOAT64):
        return False
    if value is None:
        return False
    return math.isnan(float(value))


# PascalCase alias kept for parity with the Go exported identifier.
IsNaN = is_nan
