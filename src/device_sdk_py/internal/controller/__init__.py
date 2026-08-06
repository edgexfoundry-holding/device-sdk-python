# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The internal controllers of the EdgeX Device Service SDK - ported from
`device-sdk-go/internal/controller`.

Sub-packages:
    http: The REST API controllers (device command, discovery, profile scan and the
        common ping / version / config / metrics endpoints).
    messaging: The message bus command subscription (ported in a later phase).
"""

__all__ = ["http"]
