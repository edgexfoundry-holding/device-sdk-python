# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The Device Service bootstrap - ported from `device-sdk-go/service` (bootstrap.go /
service.go).

Exports:
    Bootstrap: The SDK bootstrap container (mirrors Go `service.Bootstrap`).
    new_bootstrap / NewBootstrap: Constructors of the bootstrap.
    bootstrap / BootstrapService: Convenience entry points that initialize the caches,
        create the DeviceService and (optionally) run it.
"""

from .bootstrap import (
    Bootstrap,
    NewBootstrap,
    bootstrap,
    new_bootstrap,
    run,
)

__all__ = [
    "Bootstrap",
    "NewBootstrap",
    "new_bootstrap",
    "bootstrap",
    "run",
]
