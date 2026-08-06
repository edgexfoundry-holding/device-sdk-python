# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The HTTP REST controllers of the EdgeX Device Service SDK - ported from
`device-sdk-go/internal/controller/http`.

Exports:
    RestController: The REST controller owning the FastAPI application.  It registers
        the SDK reserved routes (device command, discovery, profile scan, ping / version
        / config / metrics) and supports service-specific custom routes.
    CommandController / DiscoveryController: The handler mixins contributed to the
        `RestController`.
"""

from .command import CommandController
from .discovery import DiscoveryController
from .router import RestController

__all__ = [
    "RestController",
    "CommandController",
    "DiscoveryController",
]
