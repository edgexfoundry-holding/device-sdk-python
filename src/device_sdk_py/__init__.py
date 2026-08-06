# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The EdgeX Foundry Device Service SDK for Python.

A Python port of `edgexfoundry/device-sdk-go` that reuses the client / bootstrap /
configuration / messaging modules from `edgexfoundry-holding/app-functions-sdk-python`.

Modules:
    interfaces:    Abstract base classes (ProtocolDriver, DeviceServiceSDK).
    models:        Data models (CommandValue, CommandRequest, AsyncValues,
                   DiscoveredDevice, Notify).
    internal:      Internal implementation modules (cache, transformer, controller, etc.)
    service:       DeviceService implementation and bootstrap.
"""

__version__ = '4.0.0'

__all__ = ["__version__", "interfaces", "models", "internal", "service"]
