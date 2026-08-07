# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""

`AutoEventExecutor` periodically (at the interval defined by an AutoEvent) reads the
CommandValues of a Device source through a user-supplied `read_handler` and sends them as
`AsyncValues` through a `send_handler`. When `on_change` is set, an execution is skipped
unless at least one reading changed; the `on_change_threshold` controls how much a numeric
value must change to be considered different.

The Go implementation runs each executor on a goroutine (an `ants` worker pool) and sends
the resulting Event with `sdkCommon.SendEvent`; this port runs the executor loop on its own
daemon thread and hands `AsyncValues` to the `send_handler` callback (typically a wrapper
around the SDK's async values channel).
"""

from __future__ import annotations

import logging
import threading
import time
import zlib
from typing import Any, Callable, Dict, List, Optional

from ..cache import AutoEvent
from ...models import (
    VALUETYPE_BINARY,
    VALUETYPE_FLOAT32,
    VALUETYPE_FLOAT64,
    VALUETYPE_INT8,
    VALUETYPE_INT16,
    VALUETYPE_INT32,
    VALUETYPE_INT64,
    VALUETYPE_UINT8,
    VALUETYPE_UINT16,
    VALUETYPE_UINT32,
    VALUETYPE_UINT64,
    AsyncValues,
    CommandValue,
)

#: The numeric value types compared with the on_change threshold.
_NUMERIC_VALUE_TYPES = frozenset({
    VALUETYPE_UINT8, VALUETYPE_UINT16, VALUETYPE_UINT32, VALUETYPE_UINT64,
    VALUETYPE_INT8, VALUETYPE_INT16, VALUETYPE_INT32, VALUETYPE_INT64,
    VALUETYPE_FLOAT32, VALUETYPE_FLOAT64,
})

#: Go time.ParseDuration unit factors, converted to seconds.
_DURATION_UNIT_FACTORS = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,
    "μs": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}

_logger = logging.getLogger(__name__)


class AutoEventError(Exception):
    """Raised when an AutoEvent cannot be processed (e.g. an invalid interval).

    EdgeX` returned by `NewExecutor` in executor.go.
    """


def parse_duration(interval: str) -> float:
    """Parse a Go duration string (e.g. ``"300ms"``, ``"1.5h"``, ``"2h45m"``, ``"0"``)
    and return the duration in seconds.

    Raises `AutoEventError` for invalid
    strings.
    """
    original = interval
    if interval == "0":
        return 0.0
    negative = False
    if interval.startswith("+"):
        interval = interval[1:]
    elif interval.startswith("-"):
        negative = True
        interval = interval[1:]
        if interval == "0":
            return 0.0
    if not interval:
        raise AutoEventError(f"invalid duration {original}")

    total = 0.0
    index = 0
    length = len(interval)
    while index < length:
        # consume the number (integer or decimal fraction)
        start = index
        while index < length and (interval[index].isdigit() or interval[index] == "."):
            index += 1
        if start == index:
            raise AutoEventError(f"invalid duration {original}")
        try:
            number = float(interval[start:index])
        except ValueError:
            raise AutoEventError(f"invalid duration {original}") from None

        # consume the unit (a run of letters, e.g. "ms", "us", "h")
        unit_start = index
        while index < length and interval[index].isalpha():
            index += 1
        unit = interval[unit_start:index]
        if unit not in _DURATION_UNIT_FACTORS:
            raise AutoEventError(f"unknown unit {unit!r} in duration {original}")

        total += number * _DURATION_UNIT_FACTORS[unit]

    if negative:
        total = -total
    return total


def _checksum(binary_value: bytes) -> int:
    """Return a checksum of a binary value (Python counterpart of `xxhash.Checksum64`)."""
    return zlib.crc32(bytes(binary_value))


def _to_float(value: Any) -> float:
    """Convert a stored reading value to float, returning 0.0 when it cannot be converted
( behaviour for unparseable values)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class AutoEventExecutor:
    """Periodically reads and sends the readings of one AutoEvent source for a Device.

    The executor runs in its own daemon thread
    started by `start()` and stopped by `stop()`; the read / send side effects are delegated
    to the `read_handler` / `send_handler` callables supplied at construction time.
    """

    def __init__(self, device_name: str, auto_event: AutoEvent,
                 read_handler: Callable[[str, str], Optional[List[CommandValue]]],
                 send_handler: Optional[Callable[[AsyncValues], None]] = None,
                 send_changed_readings_only: bool = False):
        self.device_name = device_name
        self.source_name = auto_event.source_name
        self.on_change = auto_event.on_change
        self.on_change_threshold = auto_event.on_change_threshold
        #: The interval parsed to seconds; raises `AutoEventError` for an invalid interval.
        self._interval_seconds = parse_duration(auto_event.interval)
        self._read_handler = read_handler
        self._send_handler = send_handler
        self._send_changed_readings_only = send_changed_readings_only
        self._last_readings: Dict[str, Any] = {}
        self._changed_readings: List[CommandValue] = []
        self._mutex = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def interval(self) -> float:
        """The interval in seconds between two executions."""
        return self._interval_seconds

    def start(self) -> None:
        """Start the executor loop on a new daemon thread (one executor per AutoEvent)."""
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self.run, name=f"AutoEvent-{self.device_name}-{self.source_name}",
                daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Mark this executor stopped; the loop exits at the next deadline check."""
        self._stop_event.set()

    def run(self) -> None:
        """The executor loop: read the source at a fixed rate until `stop()` is called.

        : the deadline is advanced by the interval
        after each execution so readings happen on a fixed schedule.
        """
        deadline = time.monotonic() + self._interval_seconds
        while not self._stop_event.is_set():
            wait = deadline - time.monotonic()
            if wait > 0:
                self._stop_event.wait(wait)
                if self._stop_event.is_set():
                    return
            deadline = deadline + self._interval_seconds
            self._execute_once()

    def _execute_once(self) -> None:
        _logger.debug("AutoEvent - reading %s", self.source_name)
        try:
            command_values = self._read_handler(self.device_name, self.source_name)
        except Exception:
            _logger.exception(
                "AutoEvent - error occurs when reading resource %s", self.source_name)
            return

        if not command_values:
            _logger.debug("AutoEvent - no event generated when reading resource %s",
                          self.source_name)
            return

        if self.on_change and self._compare_readings(command_values):
            _logger.debug("AutoEvent - source '%s' readings are the same as previous one",
                          self.source_name)
            return

        async_values = AsyncValues(
            device_name=self.device_name,
            source_name=self.source_name,
            command_values=list(command_values),
            origin=time.time_ns())

        with self._mutex:
            if self.on_change and self._send_changed_readings_only and \
                    len(self._changed_readings) != 0:
                async_values.command_values = list(self._changed_readings)

        if self._send_handler is not None:
            self._send_handler(async_values)

    def _compare_readings(self, command_values: List[CommandValue]) -> bool:
        """Compare the current readings with the previous ones, updating the stored
        readings and the list of changed readings.

        Returns True when the readings
        are unchanged (so the caller can skip sending them).
        """
        with self._mutex:
            current = {cv.device_resource_name: cv for cv in command_values}
            if len(self._last_readings) != len(current):
                self._renew_last_readings(command_values)
                self._changed_readings = list(command_values)
                return False

            result = True
            self._changed_readings = []
            for cv in command_values:
                last = self._last_readings.get(cv.device_resource_name)
                if last is None:
                    self._renew_last_readings(command_values)
                    self._changed_readings = list(command_values)
                    return False
                if cv.value_type == VALUETYPE_BINARY:
                    checksum = _checksum(cv.value)
                    if last != checksum:
                        self._last_readings[cv.device_resource_name] = checksum
                        result = False
                        self._changed_readings.append(cv)
                elif cv.value_type in _NUMERIC_VALUE_TYPES:
                    if abs(_to_float(last) - _to_float(cv.value)) > self.on_change_threshold:
                        self._last_readings[cv.device_resource_name] = cv.value
                        result = False
                        self._changed_readings.append(cv)
                else:
                    if last != cv.value:
                        self._last_readings[cv.device_resource_name] = cv.value
                        result = False
                        self._changed_readings.append(cv)
            return result

    def _renew_last_readings(self, command_values: List[CommandValue]) -> None:
        self._last_readings = {}
        for cv in command_values:
            if cv.value_type == VALUETYPE_BINARY:
                self._last_readings[cv.device_resource_name] = _checksum(cv.value)
            else:
                self._last_readings[cv.device_resource_name] = cv.value


def create_executor(device_name: str, auto_event: AutoEvent,
                 read_handler: Callable[[str, str], Optional[List[CommandValue]]],
                 send_handler: Optional[Callable[[AsyncValues], None]] = None,
                 send_changed_readings_only: bool = False) -> AutoEventExecutor:
    """Create an `AutoEventExecutor` for an AutoEvent (mirrors `NewExecutor` in
executor.go). Raises `AutoEventError` when the AutoEvent interval cannot be parsed.
    """
    return AutoEventExecutor(
        device_name=device_name,
        auto_event=auto_event,
        read_handler=read_handler,
        send_handler=send_handler,
        send_changed_readings_only=send_changed_readings_only)


