# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The Device Service bootstrap.

Exports:
    Bootstrap: The SDK bootstrap container.
    bootstrap: Convenience entry point that initializes the caches and creates the
        DeviceService.
    run: Convenience entry point that creates the DeviceService and starts it
        (blocking).
"""

from .bootstrap import (
    Bootstrap,
    bootstrap,
    run,
)

__all__ = [
    "Bootstrap",
    "bootstrap",
    "run",
]
