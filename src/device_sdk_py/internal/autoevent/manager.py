# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""`device-sdk-go/internal/autoevent/*.go` (the `BootstrapHandler` / `Manager` logic).

``DeviceService._start_auto_events`` lazily imports this module and builds an
``AutoEventManager``. Until now the module did not exist, so every ``AutoEvent``
declared on a Device was silently ignored. The manager now:

* enumerates the Devices held by the cache,
* for each Device schedules one ``AutoEventExecutor`` per ``AutoEvent``,
* reads the source ``Command``/``DeviceResource`` of an auto event through the very
  same driver invocation used by REST GET commands (``_handle_read_commands``), so
  scheduled reads run through the cache -> command -> transform pipeline,
* wraps the raw ``CommandValue``s into ``AsyncValues`` and pushes them onto the
  DeviceService ``async_values_channel``. An async-values pump (started by
  ``DeviceService.run``) turns those into an ``Event`` and publishes it through
  ``RestController.send_event`` (i.e. the message-bus / ``send_event_handler`` hook).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ...models import AsyncValues, CommandRequest, CommandValue
from ..application.command import _handle_read_commands
from ..cache import (
    ADMIN_STATE_LOCKED,
    AutoEvent,
    Device,
    Devices,
    Profiles,
)
from ..common.consts import OPERATING_STATE_DOWN
from ..application.command import _device_option
from .executor import AutoEventExecutor, create_executor

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from ..service.device_service import DeviceService

_logger = logging.getLogger(__name__)


class AutoEventManager:
    """Schedule one ``AutoEventExecutor`` per ``AutoEvent`` of each Device.

    Mirrors ``device-sdk-go/internal/common/manager``-style bootstrap plus the
    per-device ``Executor`` bookkeeping in ``autoevent.go``.
    """

    def __init__(self, device_service: "DeviceService") -> None:
        self._device_service = device_service
        self._logger = device_service._logger
        self._executors: Dict[str, List[AutoEventExecutor]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start_auto_events(self) -> None:
        """Schedule the auto events of every device currently held by the cache."""
        self._logger.debug("Starting AutoEvents")
        with self._lock:
            self._executors.clear()
        for device in Devices().all():
            self._start_for_device(device)

    def restart_for_device(self, device_name: str) -> None:
        """Restart the auto events scheduled for a single device."""
        self._logger.debug("Restarting AutoEvents for device %s", device_name)
        with self._lock:
            existing = self._executors.pop(device_name, [])
        for executor in existing:
            executor.stop()
        device, ok = Devices().for_name(device_name)
        if ok:
            self._start_for_device(device)
        else:
            self._logger.warning(
                "AutoEvent - cannot restart, device %s not in cache", device_name)

    def stop_all(self) -> None:
        """Stop every scheduled auto event executor."""
        with self._lock:
            executors = self._executors
            self._executors = {}
        for device_executors in executors.values():
            for executor in device_executors:
                try:
                    executor.stop()
                except Exception: # pragma: no cover - defensive
                    self._logger.exception("AutoEvent - error stopping executor")

    def stop_for_device(self, device_name: str) -> None:
        """Stop the auto events scheduled for a single device.

        Mirrors Go `AutoEventManager.StopForDevice`.
        """
        self._logger.debug("Stopping AutoEvents for device %s", device_name)
        with self._lock:
            existing = self._executors.pop(device_name, [])
        for executor in existing:
            try:
                executor.stop()
            except Exception:  # pragma: no cover - defensive
                self._logger.exception("AutoEvent - error stopping executor for %s",
                                       device_name)

    # ------------------------------------------------------------------ #
    # Scheduling
    # ------------------------------------------------------------------ #
    def _start_for_device(self, device: Device) -> None:
        if not device.auto_events:
            return
        # Mirrors Go's guard: skip administratively locked / disabled devices.
        if device.admin_state == ADMIN_STATE_LOCKED:
            self._logger.debug(
                "AutoEvent - skipping locked device %s", device.name)
            return
        if device.operating_state == OPERATING_STATE_DOWN:
            self._logger.debug(
                "AutoEvent - skipping down device %s", device.name)
            return

        with self._lock:
            device_executors = self._executors.setdefault(device.name, [])

        for auto_event in device.auto_events:
            source_name = auto_event.source_name
            if not source_name:
                self._logger.warning(
                    "AutoEvent on %s has no source name; skipping", device.name)
                continue
            # Read device config options for this auto event
            send_changed_readings_only = False
            if self._device_service.configuration is not None:
                device_opt = getattr(self._device_service.configuration, "device", None)
                if device_opt is not None:
                    send_changed_readings_only = getattr(device_opt, "send_changed_readings_only", False)
            try:
                executor = create_executor(
                    device_name=device.name,
                    auto_event=auto_event,
                    read_handler=self._read,
                    send_handler=self._send,
                    send_changed_readings_only=send_changed_readings_only)
            except Exception: # pragma: no cover - defensive
                self._logger.exception(
                    "AutoEvent - failed to create executor for %s/%s",
                    device.name, source_name)
                continue
            device_executors.append(executor)
            self._logger.debug(
                "AutoEvent scheduled for %s on %s", source_name, device.name)
            executor.start()

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #
    def _read(self, device_name: str, source_name: str) -> Optional[List[CommandValue]]:
        """Read a scheduled source (Command/DeviceResource), returning the raw typed
        ``CommandValue``s before stringification. The consumer on the async-values pump
        turns them into an ``Event`` (stringifying once, exactly as REST GET does)."""
        driver = self._device_service.driver
        if driver is None:
            return None
        device, ok = Devices().for_name(device_name)
        if not ok:
            return None
        requests = self._build_requests(device, source_name)
        if not requests:
            return None
        try:
            results = _handle_read_commands(
                driver, device, requests,
                f"error reading AutoEvent source {source_name} for {device.name}")
        except Exception:  # noqa: BLE001 - an auto event should never kill the scheduler
            self._logger.exception(
                "AutoEvent - read failed for %s/%s", device_name, source_name)
            return None
        return results

    def _build_requests(self, device: Device,
                        source_name: str) -> Optional[List[CommandRequest]]:
        """Resolve an auto-event ``source_name`` to the ``CommandRequest`` list the
        ProtocolDriver expects - mirrors the resolution done in
        ``_read_device_command`` but without stringifying the result."""
        command, found = Profiles().device_command(device.profile_name, source_name)
        if found:
            requests: List[CommandRequest] = []
            for operation in command.resource_operations:
                resource, ok = Profiles().device_resource(
                    device.profile_name, operation.device_resource)
                if not ok:
                    continue
                requests.append(CommandRequest(
                    resource_name=resource.name,
                    attributes=dict(resource.attributes),
                    value_type=resource.properties.value_type))
            return requests or None

        resource, found = Profiles().device_resource(device.profile_name, source_name)
        if found:
            return [CommandRequest(
                resource_name=resource.name,
                attributes=dict(resource.attributes),
                value_type=resource.properties.value_type)]

        self._logger.warning(
            "AutoEvent source %s not found in profile %s",
            source_name, device.profile_name)
        return None

    def _send(self, async_values: AsyncValues) -> None:
        """Push the scheduled readings onto the DeviceService async-values channel."""
        try:
            self._device_service.async_values_channel().put(async_values)
        except Exception:  # pragma: no cover - defensive
            self._logger.exception("AutoEvent - failed to enqueue AsyncValues")
