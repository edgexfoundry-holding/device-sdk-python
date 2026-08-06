# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The CommandRequest data model - ported from `device-sdk-go/pkg/models/commandrequest.go`.

`CommandRequest` is the struct used to request a command from ProtocolDrivers. It carries
the Device Resource name, the resource attributes, the data type of the resource and, as a
Python extension, an optional options map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from .command_value import VALUETYPE_STRING


@dataclass
class CommandRequest:
    """A request for a command sent to a ProtocolDriver.

    Corresponds to `models.CommandRequest` in commandrequest.go.

    Attributes:
        resource_name: The name of the Device Resource for this command.
        attributes: A key/value map representing the attributes of the Device Resource.
        value_type: The data type of the Device Resource (see the `VALUETYPE_*` constants).
            Named `value_type` in Python since `type` shadows a builtin; the Go field is
            `Type` (also exposed via the `type` property alias).
        options: An optional key/value map with driver-specific request options.
    """
    resource_name: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    value_type: str = VALUETYPE_STRING
    options: Dict[str, Any] = field(default_factory=dict)

    @property
    def type(self) -> str:
        """Alias of `value_type` mirroring the Go field name `Type`."""
        return self.value_type

    @type.setter
    def type(self, value: str) -> None:
        self.value_type = value
