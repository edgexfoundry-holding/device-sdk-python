# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Data models for the EdgeX Device Service SDK - ported from `device-sdk-go/pkg/models`.

Exports:
    CommandValue: A reading value (Get) or command parameter (Put) with typed getters.
    CommandRequest: A request for a command sent to a ProtocolDriver.
    AsyncValues: An asynchronous batch of readings produced by a ProtocolDriver.
    DiscoveredDevice: Information about a device found during discovery.
    Notify / Progress / DeviceDiscoveryProgress: System event notification payloads.
"""

from .async_values import AsyncValues
from .command_request import CommandRequest
from .command_value import (
    MAX_BINARY_BYTES,
    VALUETYPE_BOOL,
    VALUETYPE_BOOL_ARRAY,
    VALUETYPE_BINARY,
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
    VALUETYPES,
    ValueTypeError,
    CommandValue,
    NewCommandValue,
    NewCommandValueWithOrigin,
    new_command_value,
    new_command_value_with_origin,
    validate,
)
from .discovered_device import DiscoveredDevice, ProtocolProperties
from .notify import DeviceDiscoveryProgress, Notify, Progress

__all__ = [
    "AsyncValues",
    "CommandRequest",
    "CommandValue",
    "DiscoveredDevice",
    "DeviceDiscoveryProgress",
    "Notify",
    "Progress",
    "ProtocolProperties",
    "ValueTypeError",
    "VALUETYPES",
    "MAX_BINARY_BYTES",
    "VALUETYPE_BOOL",
    "VALUETYPE_STRING",
    "VALUETYPE_UINT8",
    "VALUETYPE_UINT16",
    "VALUETYPE_UINT32",
    "VALUETYPE_UINT64",
    "VALUETYPE_INT8",
    "VALUETYPE_INT16",
    "VALUETYPE_INT32",
    "VALUETYPE_INT64",
    "VALUETYPE_FLOAT32",
    "VALUETYPE_FLOAT64",
    "VALUETYPE_BINARY",
    "VALUETYPE_BOOL_ARRAY",
    "VALUETYPE_STRING_ARRAY",
    "VALUETYPE_UINT8_ARRAY",
    "VALUETYPE_UINT16_ARRAY",
    "VALUETYPE_UINT32_ARRAY",
    "VALUETYPE_UINT64_ARRAY",
    "VALUETYPE_INT8_ARRAY",
    "VALUETYPE_INT16_ARRAY",
    "VALUETYPE_INT32_ARRAY",
    "VALUETYPE_INT64_ARRAY",
    "VALUETYPE_FLOAT32_ARRAY",
    "VALUETYPE_FLOAT64_ARRAY",
    "VALUETYPE_OBJECT",
    "VALUETYPE_OBJECT_ARRAY",
    "NewCommandValue",
    "NewCommandValueWithOrigin",
    "new_command_value",
    "new_command_value_with_origin",
    "validate",
]
