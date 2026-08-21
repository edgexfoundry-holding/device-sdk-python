# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/internal/controller/http`.

Exports:
RestController: The REST controller owning the FastAPI application. It registers
        the SDK reserved routes (device command, discovery, profile scan, secret,
        ping / version / config / metrics) and supports service-specific custom
        routes.
    CommandController / DiscoveryController / SecretController: The handler mixins
        contributed to the `RestController`.
"""

from .command import CommandController
from .discovery import DiscoveryController
from .router import RestController
from .secret import SecretController

__all__ = [
    "RestController",
    "CommandController",
    "DiscoveryController",
    "SecretController",
]
