# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""

`CommandValue` represents the reading value of a Get command coming from ProtocolDrivers
or the parameter of a Put command sent to ProtocolDrivers. It carries the Device Resource
name, the declared value type, the raw value, an origin timestamp and optional tags.

This module also defines the EdgeX reading value type constants (`VALUETYPE_*`), a
`validate()` helper function, and a `ValueTypeError`
exception used in place of the Go `(value, error)` return convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Policy limit copied from Go (binary readings, 16 * 2^20 bytes).
MAX_BINARY_BYTES = 16777216

# EdgeX reading value types (matching go-mod-core-contracts/v4/common).
VALUETYPE_BOOL = "Bool"
VALUETYPE_STRING = "String"
VALUETYPE_UINT8 = "Uint8"
VALUETYPE_UINT16 = "Uint16"
VALUETYPE_UINT32 = "Uint32"
VALUETYPE_UINT64 = "Uint64"
VALUETYPE_INT8 = "Int8"
VALUETYPE_INT16 = "Int16"
VALUETYPE_INT32 = "Int32"
VALUETYPE_INT64 = "Int64"
VALUETYPE_FLOAT32 = "Float32"
VALUETYPE_FLOAT64 = "Float64"
VALUETYPE_BINARY = "Binary"
VALUETYPE_BOOL_ARRAY = "BoolArray"
VALUETYPE_STRING_ARRAY = "StringArray"
VALUETYPE_UINT8_ARRAY = "Uint8Array"
VALUETYPE_UINT16_ARRAY = "Uint16Array"
VALUETYPE_UINT32_ARRAY = "Uint32Array"
VALUETYPE_UINT64_ARRAY = "Uint64Array"
VALUETYPE_INT8_ARRAY = "Int8Array"
VALUETYPE_INT16_ARRAY = "Int16Array"
VALUETYPE_INT32_ARRAY = "Int32Array"
VALUETYPE_INT64_ARRAY = "Int64Array"
VALUETYPE_FLOAT32_ARRAY = "Float32Array"
VALUETYPE_FLOAT64_ARRAY = "Float64Array"
VALUETYPE_OBJECT = "Object"
VALUETYPE_OBJECT_ARRAY = "ObjectArray"

# The complete set of supported value types.
VALUETYPES: frozenset = frozenset({
    VALUETYPE_BOOL, VALUETYPE_STRING, VALUETYPE_UINT8, VALUETYPE_UINT16, VALUETYPE_UINT32,
    VALUETYPE_UINT64, VALUETYPE_INT8, VALUETYPE_INT16, VALUETYPE_INT32, VALUETYPE_INT64,
    VALUETYPE_FLOAT32, VALUETYPE_FLOAT64, VALUETYPE_BINARY, VALUETYPE_BOOL_ARRAY,
    VALUETYPE_STRING_ARRAY, VALUETYPE_UINT8_ARRAY, VALUETYPE_UINT16_ARRAY,
    VALUETYPE_UINT32_ARRAY, VALUETYPE_UINT64_ARRAY, VALUETYPE_INT8_ARRAY,
    VALUETYPE_INT16_ARRAY, VALUETYPE_INT32_ARRAY, VALUETYPE_INT64_ARRAY,
    VALUETYPE_FLOAT32_ARRAY, VALUETYPE_FLOAT64_ARRAY, VALUETYPE_OBJECT,
    VALUETYPE_OBJECT_ARRAY,
})


class ValueTypeError(Exception):
    """Raised when a value cannot be produced as / converted to the requested value type.

    Python counterpart of the error returned by the Go `(value, error)` methods.
    """


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_float(value: Any) -> bool:
    return isinstance(value, float)


def _is_binary(value: Any) -> bool:
    return isinstance(value, (bytes, bytearray))


def _is_bool_array(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, bool) for item in value)


def _is_string_array(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_int_array(value: Any) -> bool:
    return isinstance(value, list) and all(_is_int(item) for item in value)


def _is_float_array(value: Any) -> bool:
    return isinstance(value, list) and all(_is_float(item) for item in value)


def _is_object_array(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


# Maps each value type to a predicate asserting that the value is of the expected type.
_TYPE_CHECKERS: Dict[str, Callable[[Any], bool]] = {
    VALUETYPE_STRING: lambda value: isinstance(value, str),
    VALUETYPE_STRING_ARRAY: _is_string_array,
    VALUETYPE_BOOL: lambda value: isinstance(value, bool),
    VALUETYPE_BOOL_ARRAY: _is_bool_array,
    VALUETYPE_UINT8: _is_int,
    VALUETYPE_UINT8_ARRAY: _is_int_array,
    VALUETYPE_UINT16: _is_int,
    VALUETYPE_UINT16_ARRAY: _is_int_array,
    VALUETYPE_UINT32: _is_int,
    VALUETYPE_UINT32_ARRAY: _is_int_array,
    VALUETYPE_UINT64: _is_int,
    VALUETYPE_UINT64_ARRAY: _is_int_array,
    VALUETYPE_INT8: _is_int,
    VALUETYPE_INT8_ARRAY: _is_int_array,
    VALUETYPE_INT16: _is_int,
    VALUETYPE_INT16_ARRAY: _is_int_array,
    VALUETYPE_INT32: _is_int,
    VALUETYPE_INT32_ARRAY: _is_int_array,
    VALUETYPE_INT64: _is_int,
    VALUETYPE_INT64_ARRAY: _is_int_array,
    VALUETYPE_FLOAT32: _is_float,
    VALUETYPE_FLOAT32_ARRAY: _is_float_array,
    VALUETYPE_FLOAT64: _is_float,
    VALUETYPE_FLOAT64_ARRAY: _is_float_array,
    VALUETYPE_BINARY: _is_binary,
    VALUETYPE_OBJECT: lambda value: value is not None,
    VALUETYPE_OBJECT_ARRAY: _is_object_array,
}


def validate(value_type: str, value: Any) -> None:
    """Validate that `value` is compatible with `value_type`.

    Mirrors the Go `validate()` function in commandvalue.go. A `None` value passes
    validation (it is allowed); otherwise a `ValueTypeError` is raised when the type
    assertion fails, the value type is unrecognized, or a binary payload exceeds
    `MAX_BINARY_BYTES`.
    """
    if value is None:
        return

    checker = _TYPE_CHECKERS.get(value_type)
    if checker is None:
        raise ValueTypeError(f"unrecognized value type: {value_type}")

    if not checker(value):
        raise ValueTypeError(f"failed to convert value {value!r} to Type {value_type}")

    if value_type == VALUETYPE_BINARY and len(value) > MAX_BINARY_BYTES:
        raise ValueTypeError(
            f"value payload exceeds limit for binary readings ({MAX_BINARY_BYTES} bytes)")


@dataclass
class CommandValue:
    """The reading value of a Get command coming from ProtocolDrivers, or the
    parameter of a Put command sent to ProtocolDrivers.


    Attributes:
        device_resource_name: The name of the Device Resource for this command.
        value_type: The data type of the value (see the `VALUETYPE_*` constants).
        value: The value returned by a ProtocolDriver instance. It can be converted
            to its native type via the typed getters.
        origin: An int64 value indicating the time the reading was obtained by the
            ProtocolDriver instance.
        tags: Custom information added to the Event to help identify its origin
            before it is sent to the north side.
    """
    device_resource_name: str
    value_type: str
    value: Any
    origin: int = 0
    tags: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate(self.value_type, self.value)

    def _require_type(self, value_type: str) -> None:
        if self.value_type != value_type:
            raise ValueTypeError(
                f"cannot convert CommandValue of {self.value_type} to {value_type}")

    def value_to_string(self) -> str:
        """Return the string format of the value (mirrors `ValueToString()`)."""
        if self.value_type == VALUETYPE_BINARY:
            binary_value = self.value[:20]
            return f"Binary: [{binary_value!r}...]"
        return str(self.value)

    def __str__(self) -> str:
        return (f"DeviceResource: {self.device_resource_name}, "
                f"{self.value_type}: {self.value_to_string()}")

    # -- scalar getters ----------------------------------------------------

    def bool_value(self) -> bool:
        """Return the value as bool; raises ValueTypeError if the type is not Bool."""
        self._require_type(VALUETYPE_BOOL)
        if not isinstance(self.value, bool):
            raise ValueTypeError(f"failed to transform {self.value!r} to bool")
        return self.value

    def string_value(self) -> str:
        """Return the value as str; raises ValueTypeError if the type is not String."""
        self._require_type(VALUETYPE_STRING)
        if not isinstance(self.value, str):
            raise ValueTypeError(f"failed to transform {self.value!r} to str")
        return self.value

    def int8_value(self) -> int:
        """Return the value as int8; raises ValueTypeError if the type is not Int8."""
        return self._int_value(VALUETYPE_INT8)

    def int16_value(self) -> int:
        """Return the value as int16; raises ValueTypeError if the type is not Int16."""
        return self._int_value(VALUETYPE_INT16)

    def int32_value(self) -> int:
        """Return the value as int32; raises ValueTypeError if the type is not Int32."""
        return self._int_value(VALUETYPE_INT32)

    def int64_value(self) -> int:
        """Return the value as int64; raises ValueTypeError if the type is not Int64."""
        return self._int_value(VALUETYPE_INT64)

    def uint8_value(self) -> int:
        """Return the value as uint8; raises ValueTypeError if the type is not Uint8."""
        return self._int_value(VALUETYPE_UINT8)

    def uint16_value(self) -> int:
        """Return the value as uint16; raises ValueTypeError if the type is not Uint16."""
        return self._int_value(VALUETYPE_UINT16)

    def uint32_value(self) -> int:
        """Return the value as uint32; raises ValueTypeError if the type is not Uint32."""
        return self._int_value(VALUETYPE_UINT32)

    def uint64_value(self) -> int:
        """Return the value as uint64; raises ValueTypeError if the type is not Uint64."""
        return self._int_value(VALUETYPE_UINT64)

    def float32_value(self) -> float:
        """Return the value as float32; raises ValueTypeError if the type is not Float32."""
        return self._float_value(VALUETYPE_FLOAT32)

    def float64_value(self) -> float:
        """Return the value as float64; raises ValueTypeError if the type is not Float64."""
        return self._float_value(VALUETYPE_FLOAT64)

    # -- array getters -----------------------------------------------------

    def bool_array_value(self) -> List[bool]:
        """Return the value as a list of bool; raises ValueTypeError if not BoolArray."""
        self._require_type(VALUETYPE_BOOL_ARRAY)
        if not _is_bool_array(self.value):
            raise ValueTypeError(f"failed to transform {self.value!r} to List[bool]")
        return self.value

    def string_array_value(self) -> List[str]:
        """Return the value as a list of str; raises ValueTypeError if not StringArray."""
        self._require_type(VALUETYPE_STRING_ARRAY)
        if not _is_string_array(self.value):
            raise ValueTypeError(f"failed to transform {self.value!r} to List[str]")
        return self.value

    def int8_array_value(self) -> List[int]:
        """Return the value as a list of int8; raises ValueTypeError if not Int8Array."""
        return self._int_array_value(VALUETYPE_INT8_ARRAY)

    def int16_array_value(self) -> List[int]:
        """Return the value as a list of int16; raises ValueTypeError if not Int16Array."""
        return self._int_array_value(VALUETYPE_INT16_ARRAY)

    def int32_array_value(self) -> List[int]:
        """Return the value as a list of int32; raises ValueTypeError if not Int32Array."""
        return self._int_array_value(VALUETYPE_INT32_ARRAY)

    def int64_array_value(self) -> List[int]:
        """Return the value as a list of int64; raises ValueTypeError if not Int64Array."""
        return self._int_array_value(VALUETYPE_INT64_ARRAY)

    def uint8_array_value(self) -> List[int]:
        """Return the value as a list of uint8; raises ValueTypeError if not Uint8Array."""
        return self._int_array_value(VALUETYPE_UINT8_ARRAY)

    def uint16_array_value(self) -> List[int]:
        """Return the value as a list of uint16; raises ValueTypeError if not Uint16Array."""
        return self._int_array_value(VALUETYPE_UINT16_ARRAY)

    def uint32_array_value(self) -> List[int]:
        """Return the value as a list of uint32; raises ValueTypeError if not Uint32Array."""
        return self._int_array_value(VALUETYPE_UINT32_ARRAY)

    def uint64_array_value(self) -> List[int]:
        """Return the value as a list of uint64; raises ValueTypeError if not Uint64Array."""
        return self._int_array_value(VALUETYPE_UINT64_ARRAY)

    def float32_array_value(self) -> List[float]:
        """Return the value as a list of float32; raises ValueTypeError if not Float32Array."""
        return self._float_array_value(VALUETYPE_FLOAT32_ARRAY)

    def float64_array_value(self) -> List[float]:
        """Return the value as a list of float64; raises ValueTypeError if not Float64Array."""
        return self._float_array_value(VALUETYPE_FLOAT64_ARRAY)

    # -- other getters -----------------------------------------------------

    def binary_value(self) -> Optional[bytes]:
        """Return the value as bytes; raises ValueTypeError if the type is not Binary.

Returns None when the value is None ( behaviour).
        """
        if self.value is None:
            return None
        self._require_type(VALUETYPE_BINARY)
        if not isinstance(self.value, (bytes, bytearray)):
            raise ValueTypeError(f"failed to transform {self.value!r} to bytes")
        return bytes(self.value)

    def object_value(self) -> Any:
        """Return the value as object; raises ValueTypeError if the type is not Object."""
        self._require_type(VALUETYPE_OBJECT)
        return self.value

    def object_array_value(self) -> List[Dict[str, Any]]:
        """Return the value as a list of dict; raises ValueTypeError if not ObjectArray."""
        self._require_type(VALUETYPE_OBJECT_ARRAY)
        if not _is_object_array(self.value):
            raise ValueTypeError(f"failed to transform {self.value!r} to List[Dict]")
        return self.value

    # -- private helpers ---------------------------------------------------

    def _int_value(self, value_type: str) -> int:
        self._require_type(value_type)
        if not _is_int(self.value):
            raise ValueTypeError(f"failed to transform {self.value!r} to int")
        return self.value

    def _float_value(self, value_type: str) -> float:
        self._require_type(value_type)
        if not _is_float(self.value):
            raise ValueTypeError(f"failed to transform {self.value!r} to float")
        return self.value

    def _int_array_value(self, value_type: str) -> List[int]:
        self._require_type(value_type)
        if not _is_int_array(self.value):
            raise ValueTypeError(f"failed to transform {self.value!r} to List[int]")
        return self.value

    def _float_array_value(self, value_type: str) -> List[float]:
        self._require_type(value_type)
        if not _is_float_array(self.value):
            raise ValueTypeError(f"failed to transform {self.value!r} to List[float]")
        return self.value


def create_command_value(device_resource_name: str, value_type: str, value: Any) -> CommandValue:
    """Create a CommandValue, validating the value against the supplied value type.

    Validation failure raises `ValueTypeError` instead of returning
    an error tuple.
    """
    return CommandValue(device_resource_name=device_resource_name,
                        value_type=value_type,
                        value=value)


def create_command_value_with_origin(device_resource_name: str, value_type: str,
                                  value: Any, origin: int) -> CommandValue:
    """Create a CommandValue with the Origin field set.

    """
    command_value = create_command_value(device_resource_name, value_type, value)
    command_value.origin = origin
    return command_value


