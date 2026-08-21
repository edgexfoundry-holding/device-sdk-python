# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""

`AsyncValues` is the struct used by ProtocolDrivers to send Device readings asynchronously
to the SDK (which forwards them to Core Data).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .command_value import CommandValue


@dataclass
class AsyncValues:
    """An asynchronous batch of readings produced by a ProtocolDriver.


    Attributes:
        device_name: The name of the Device that produced the readings.
        source_name: The name of the source (e.g. the Device Resource or command source).
        command_values: The list of CommandValues to be sent asynchronously.
        origin: An int64 timestamp (in nanoseconds) for the readings.
    """
    device_name: str = ""
    source_name: str = ""
    command_values: List[CommandValue] = field(default_factory=list)
    origin: int = 0
