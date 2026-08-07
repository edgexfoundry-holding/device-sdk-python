# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""

The Go file defines `Progress` (embedded into `DeviceDiscoveryProgress`); those two are
reproduced here together with a general `Notify` data class that bundles the fields used
when publishing device discovery / profile scan progress system events.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Progress:
    """Progress of an asynchronous operation (e.g. device discovery or profile scan).

    """
    request_id: str = ""
    progress: int = 0
    message: str = ""


@dataclass
class DeviceDiscoveryProgress(Progress):
    """Device discovery progress notification.

    and adds the discovered device count.
    """
    discovered_device_count: int = 0


@dataclass
class Notify:
    """A generic notification payload published as a system event.

    Bundles the request id, progress value, optional message and the discovered device
    count used by `PublishDeviceDiscoveryProgressSystemEvent` /
    `PublishProfileScanProgressSystemEvent` on the `DeviceServiceSDK` interface.
    """
    request_id: str = ""
    progress: int = 0
    message: str = ""
    discovered_device_count: int = 0
