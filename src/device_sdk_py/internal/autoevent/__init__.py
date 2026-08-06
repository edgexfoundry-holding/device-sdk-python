# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""AutoEvent scheduling package (ported from `device-sdk-go/internal/autoevent`)."""

from .executor import AutoEventExecutor, new_executor
from .manager import AutoEventManager

__all__ = ["AutoEventExecutor", "AutoEventManager", "new_executor"]
