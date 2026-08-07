# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Interfaces for the EdgeX Device Service SDK.

Exports:
    ProtocolDriver: Abstract base class implemented by device services to interact with
        a specific class of devices.
    DeviceServiceSDK: Abstract base class defining the Device Service SDK interface.
    UpdatableConfig: Marker ABC for services using custom configuration.
    AUTHENTICATED / UNAUTHENTICATED: Route authentication markers.
"""

from .protocoldriver import ProtocolDriver, Protocols
from .service import (
    AUTHENTICATED,
    UNAUTHENTICATED,
    DeviceServiceSDK,
    UpdatableConfig,
)

__all__ = [
    "AUTHENTICATED",
    "UNAUTHENTICATED",
    "DeviceServiceSDK",
    "ProtocolDriver",
    "Protocols",
    "UpdatableConfig",
]
