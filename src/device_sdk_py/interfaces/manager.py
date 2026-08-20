# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/v4/pkg/interfaces/manager.go`.

AutoEventManager interface for managing automatic events.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AutoEventManager(ABC):
    """Interface for managing automatic events in the device service."""

    @abstractmethod
    def start_auto_events(self) -> None:
        """Start all the AutoEvents of the device service."""

    @abstractmethod
    def restart_for_device(self, name: str) -> None:
        """Restart all the AutoEvents of the specific device.

        Args:
            name: The device name.
        """

    @abstractmethod
    def stop_for_device(self, name: str) -> None:
        """Stop all the AutoEvents of the specific device.

        Args:
            name: The device name.
        """