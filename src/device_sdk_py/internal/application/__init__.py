# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/internal/application`.

This package contains the application logic invoked by the REST controllers (and the
command subscription of the messaging layer). The Go functions receive their
dependencies from the DI container; the Python port passes them as explicit arguments
instead.

Exports:
    command_read / command_write: Execute a Get / Set command on a Device.
    create_command_value_from_device_resource: Coerce a request value to a CommandValue
        according to the DeviceResource value type.
    set_failure_count / failure_count / decrease_failure_count / device_request_failed /
        device_request_succeeded: The Device Down auto-recovery failure tracking.
"""

from .command import (
    command_read,
    command_write,
    create_command_value_from_device_resource,
    decrease_failure_count,
    device_request_failed,
    device_request_succeeded,
    failure_count,
    set_failure_count,
)

__all__ = [
    "command_read",
    "command_write",
    "create_command_value_from_device_resource",
    "set_failure_count",
    "failure_count",
    "decrease_failure_count",
    "device_request_failed",
    "device_request_succeeded",
]
