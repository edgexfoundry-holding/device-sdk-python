# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""

`DiscoveredDevice` defines the required information for a device found during protocol
specific device discovery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

ProtocolProperties = Dict[str, Any]


@dataclass
class DiscoveredDevice:
    """A device discovered by a ProtocolDriver.

    The Python SDK's
    `ProtocolProperties` type is the `dict[str, Any]` alias reused from
    `app_functions_sdk_py.contracts.dtos.protocolproperties`.

    Attributes:
        name: The name of the discovered device.
        protocols: A map of protocol name to its properties.
        description: An optional description of the discovered device.
        labels: Optional labels applied to the discovered device.
    """
    name: str = ""
    protocols: Dict[str, ProtocolProperties] = field(default_factory=dict)
    description: str = ""
    labels: List[str] = field(default_factory=list)
