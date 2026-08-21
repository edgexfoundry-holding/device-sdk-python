# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
`device-sdk-go/v4/internal/application/devicereturn.go`.

Device return logic for handling device down/retry scenarios.  The Go
`DeviceRequestFailed` / `DeviceRequestSucceeded` entry points are ported in
`command.py` (they operate on the device configuration / logger directly); this
module carries the background retry loop (`deviceReturn`).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..common.configuration import ConfigurationStruct

_logger = logging.getLogger(__name__)


def device_return(device_name: str, configuration: Any,
                  device_service: Any = None, logger: Optional[Any] = None) -> None:
    """Background retry loop for a DOWN device.

    Polls the Device every ``device_down_timeout`` seconds; when the Device answers a
    read command successfully the OperatingState is set back to ``Up`` and the loop
    exits.

    Mirrors the Go `deviceReturn` goroutine started by `DeviceRequestFailed`.
    """
    from ..cache import Devices, Profiles
    from ..common.consts import (
        READ_WRITE_R,
        READ_WRITE_RW,
        READ_WRITE_WR,
        OPERATING_STATE_UP,
        OPERATING_STATE_DOWN,
    )
    from .command import command_read

    log = logger or _logger

    timeout = int(getattr(configuration, "device_down_timeout", 0)
                  if hasattr(configuration, "device_down_timeout")
                  else getattr(getattr(configuration, "device", None), "device_down_timeout", 0) or 0)

    while True:
        time.sleep(timeout)
        log.info("Checking operational state for device: %s", device_name)

        device, found = Devices().for_name(device_name)
        if not found:
            log.warning("Device %s not found. Exiting retry loop.", device_name)
            return
        if getattr(device, "operating_state", OPERATING_STATE_DOWN) == OPERATING_STATE_UP:
            log.info("Device %s is already operational. Exiting retry loop.", device_name)
            return

        profile, found = Profiles().for_name(getattr(device, "profile_name", ""))
        if not found:
            log.warning("Device %s has no profile. Cannot set operational state automatically.",
                        device_name)
            return

        readable = False
        for dr in getattr(profile, "device_resources", []):
            rw = getattr(getattr(dr, "properties", None), "read_write", "")
            if rw not in (READ_WRITE_R, READ_WRITE_RW, READ_WRITE_WR):
                continue
            readable = True
            event = command_read(
                device_name,
                "",
                getattr(dr, "name", ""),
                driver=getattr(device_service, "driver", None),
                configuration=configuration,
                device_service=device_service,
                logger=log,
            )
            if event is not None:
                log.info("Device %s responsive: setting operational state to up.", device_name)
                if device_service is not None and hasattr(
                        device_service, "update_device_operating_state"):
                    device_service.update_device_operating_state(device_name, OPERATING_STATE_UP)
                else:
                    from ..common.utils import update_operating_state
                    update_operating_state(device_name, OPERATING_STATE_UP, log)
                return
            log.error("Device %s unresponsive: retrying in %s seconds.",
                      device_name, timeout)

        if not readable:
            log.info("Device %s has no readable resources. Setting operational state to up "
                     "without checking.", device_name)
            if device_service is not None and hasattr(
                    device_service, "update_device_operating_state"):
                device_service.update_device_operating_state(device_name, OPERATING_STATE_UP)
            else:
                from ..common.utils import update_operating_state
                update_operating_state(device_name, OPERATING_STATE_UP, log)
            return


def start_device_return(device_name: str, configuration: Any,
                        device_service: Any = None, logger: Optional[Any] = None) -> threading.Thread:
    """Start the device-return retry loop on a background daemon thread.

    Returns:
        The started thread.
    """
    thread = threading.Thread(
        target=device_return,
        args=(device_name, configuration, device_service, logger),
        daemon=True,
        name=f"device-return-{device_name}",
    )
    thread.start()
    return thread