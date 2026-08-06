# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The ProtocolDriver abstract base class - ported from
`device-sdk-go/pkg/interfaces/protocoldriver.go`.

`ProtocolDriver` is a low-level device-specific interface used by other components of an
EdgeX Device Service to interact with a specific class of devices. Device service
implementations subclass this ABC and implement every method; the Go interface uses
`(T, error)` returns, so Python implementations signal errors by raising exceptions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List

from ..models import CommandRequest, CommandValue

if TYPE_CHECKING:
    from .service import DeviceServiceSDK

#: protocol name -> protocol properties map (matches `ProtocolProperties`).
Protocols = Dict[str, Dict[str, Any]]


class ProtocolDriver(ABC):
    """A low-level device-specific interface used by other components of an EdgeX
    Device Service to interact with a specific class of devices.

    Corresponds to `interfaces.ProtocolDriver` in protocoldriver.go. All methods are
    abstract; device services must implement each of them.
    """

    @abstractmethod
    def initialize(self, sdk: "DeviceServiceSDK") -> None:
        """Perform protocol-specific initialization for the device service.

        Go: `Initialize(sdk DeviceServiceSDK) error`.

        The given SDK can be used to push asynchronous events/readings to Core Data via
        `sdk.async_values_channel()` and to send discovered devices via
        `sdk.discovered_device_channel()`.

        Args:
            sdk: The initialized DeviceServiceSDK instance.
        """

    @abstractmethod
    def handle_read_commands(self, device_name: str, protocols: Protocols,
                             reqs: List[CommandRequest]) -> List[CommandValue]:
        """Handle a slice of CommandRequest structs, each representing a
        ResourceOperation for a specific device resource, and return the resulting
        CommandValues.

        Go: `HandleReadCommands(deviceName string,
        protocols map[string]models.ProtocolProperties, reqs []sdkModels.CommandRequest)
        ([]*sdkModels.CommandValue, error)`.

        Args:
            device_name: The name of the Device being read.
            protocols: The Device's protocol properties.
            reqs: The resource operations to execute.

        Returns:
            The list of read CommandValues. Raise an exception on failure.
        """

    @abstractmethod
    def handle_write_commands(self, device_name: str, protocols: Protocols,
                              reqs: List[CommandRequest],
                              params: List[CommandValue]) -> None:
        """Handle a slice of CommandRequest structs, each representing a
        ResourceOperation for a specific device resource. Since these are actuation
        commands, the params provide the parameters for the individual command.

        Go: `HandleWriteCommands(deviceName string,
        protocols map[string]models.ProtocolProperties, reqs []sdkModels.CommandRequest,
        params []*sdkModels.CommandValue) error`.

        Args:
            device_name: The name of the Device being written.
            protocols: The Device's protocol properties.
            reqs: The resource operations to execute.
            params: The parameters (CommandValues) for the individual commands.

        Raises:
            An exception if the write fails.
        """

    @abstractmethod
    def start(self) -> None:
        """Run Device Service startup tasks after the SDK has been completely initialized.
        This allows the Device Service to safely use DeviceServiceSDK interface features.

        Go: `Start() error`.
        """

    @abstractmethod
    def stop(self, force: bool) -> None:
        """Instruct the protocol-specific DS code to shutdown gracefully, or, if `force`
        is True, immediately. The driver is responsible for closing any in-use channels,
        including the channel used to send async readings (if supported).

        Go: `Stop(force bool) error`.

        Args:
            force: If True, shutdown immediately.
        """

    @abstractmethod
    def add_device(self, device_name: str, protocols: Protocols,
                   admin_state: str) -> None:
        """Callback invoked when a new Device associated with this Device Service is added.

        Go: `AddDevice(deviceName string, protocols map[string]models.ProtocolProperties,
        adminState models.AdminState) error`.

        Args:
            device_name: The name of the added Device.
            protocols: The Device's protocol properties.
            admin_state: The Device's admin state (e.g. "LOCKED" / "UNLOCKED").
        """

    @abstractmethod
    def update_device(self, device_name: str, protocols: Protocols,
                      admin_state: str) -> None:
        """Callback invoked when a Device associated with this Device Service is updated.

        Go: `UpdateDevice(deviceName string, protocols map[string]models.ProtocolProperties,
        adminState models.AdminState) error`.

        Args:
            device_name: The name of the updated Device.
            protocols: The Device's protocol properties.
            admin_state: The Device's admin state (e.g. "LOCKED" / "UNLOCKED").
        """

    @abstractmethod
    def remove_device(self, device_name: str, protocols: Protocols) -> None:
        """Callback invoked when a Device associated with this Device Service is removed.

        Go: `RemoveDevice(deviceName string,
        protocols map[string]models.ProtocolProperties) error`.

        Args:
            device_name: The name of the removed Device.
            protocols: The Device's protocol properties.
        """

    @abstractmethod
    def discover(self) -> None:
        """Trigger protocol specific device discovery. The discovered devices are written
        asynchronously to the channel passed to the implementation via `initialize()`;
        they may be added to the Device Service based on a set of acceptance criteria
        (i.e. Provision Watchers).

        Go: `Discover() error`.
        """

    @abstractmethod
    def validate_device(self, device: Any) -> None:
        """Trigger the device's protocol properties validation. Raise an exception if the
        validation fails; the incoming Device will not be added into EdgeX.

        Go: `ValidateDevice(device models.Device) error`.

        Args:
            device: The Device (models.Device) whose protocol properties are validated.
        """
