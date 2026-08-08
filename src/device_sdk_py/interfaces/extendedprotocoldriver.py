# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Extended ProtocolDriver interface for EdgeX Device Services.

Provides additional protocol driver features beyond the basic ProtocolDriver:
- ProfileScan: triggers device profile scanning
- StopDeviceDiscovery: stops ongoing device discovery
- StopProfileScan: stops ongoing profile scanning
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict

from ..models import DeviceProfile
from .protocoldriver import ProtocolDriver

if TYPE_CHECKING:
    from .service import DeviceServiceSDK


class ExtendedProtocolDriver(ProtocolDriver):
    """Extended protocol driver interface for advanced device service features.

    This interface extends the base ProtocolDriver with additional capabilities
    for device profile scanning and discovery control. Device services that
    support these advanced features should implement this interface in addition
    to the base ProtocolDriver interface.

    Mirrors: edgexfoundry/device-sdk-go/v4/pkg/interfaces/extendedprotocoldriver.go
    """

    @abstractmethod
    def profile_scan(self, device_name: str, profile_name: str, request_id: str,
                     options: Dict[str, Any]) -> DeviceProfile:
        """Trigger a profile scan for the specified device.

        Performs a device profile scan operation, discovering the device's
        resources and commands. The scan runs asynchronously and publishes
        progress events via the system event publisher.

        Args:
            device_name: Name of the device to scan.
            profile_name: Name of the profile to apply/create from the scan.
            request_id: Correlation ID for the scan request.
            options: Additional scan options from the request.

        Returns:
            The discovered/created DeviceProfile.

        Raises:
            EdgexError: If the profile scan fails.
        """
        ...

    @abstractmethod
    def stop_device_discovery(self, request_id: str, options: Dict[str, Any]) -> None:
        """Stop a running device discovery operation.

        Signals the driver to stop an ongoing device discovery operation
        identified by the request_id.

        Args:
            request_id: The correlation ID of the discovery to stop.
            options: Additional stop options.
        """
        ...

    @abstractmethod
    def stop_profile_scan(self, device_name: str, options: Dict[str, Any]) -> None:
        """Stop a running profile scan operation.

        Signals the driver to stop a profile scan for the specified device.

        Args:
            device_name: Name of the device whose profile scan should stop.
            options: Additional stop options.
        """
        ...