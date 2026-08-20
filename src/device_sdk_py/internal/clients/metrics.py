# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Zero-dependency MetricsManager implementation using in-memory storage.

Mirrors the EdgeX MetricsManager interface with Counter, Gauge, GaugeFloat64, Timer.
No external dependencies beyond stdlib.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class _Counter:
    """Thread-safe counter."""
    value: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, n: int = 1) -> None:
        with self._lock:
            self.value += n


@dataclass
class _Gauge:
    """Thread-safe integer gauge."""
    value: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, n: int) -> None:
        with self._lock:
            self.value = n

    def add(self, n: int) -> None:
        with self._lock:
            self.value += n

    def sub(self, n: int) -> None:
        with self._lock:
            self.value -= n


@dataclass
class _GaugeFloat64:
    """Thread-safe float gauge."""
    value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, n: float) -> None:
        with self._lock:
            self.value = n

    def add(self, n: float) -> None:
        with self._lock:
            self.value += n

    def sub(self, n: float) -> None:
        with self._lock:
            self.value -= n


@dataclass
class _Timer:
    """Thread-safe timer for measuring durations."""
    _start: Optional[float] = field(default=None, repr=False)
    _value: float = field(default=0.0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _running: bool = field(default=False, repr=False)

    def start(self) -> None:
        with self._lock:
            self._start = time.perf_counter()
            self._running = True

    def stop(self) -> float:
        with self._lock:
            if self._start is None or not self._running:
                return 0.0
            elapsed = time.perf_counter() - self._start
            self._value = elapsed
            self._running = False
            self._start = None
            return elapsed

    def value(self) -> float:
        with self._lock:
            return self._value

    def __enter__(self) -> "_Timer":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


class Counter:
    """Counter metric interface."""

    def __init__(self, counter: _Counter) -> None:
        self._counter = counter

    def inc(self, n: int = 1) -> None:
        self._counter.inc(n)

    def value(self) -> int:
        return self._counter.value


class Gauge:
    """Gauge metric interface (integer)."""

    def __init__(self, gauge: _Gauge) -> None:
        self._gauge = gauge

    def set(self, n: int) -> None:
        self._gauge.set(n)

    def add(self, n: int) -> None:
        self._gauge.add(n)

    def sub(self, n: int) -> None:
        self._gauge.sub(n)

    def value(self) -> int:
        return self._gauge.value


class GaugeFloat64:
    """GaugeFloat64 metric interface."""

    def __init__(self, gauge: _GaugeFloat64) -> None:
        self._gauge = gauge

    def set(self, n: float) -> None:
        self._gauge.set(n)

    def add(self, n: float) -> None:
        self._gauge.add(n)

    def sub(self, n: float) -> None:
        self._gauge.sub(n)

    def value(self) -> float:
        return self._gauge.value


class Timer:
    """Timer metric interface."""

    def __init__(self, timer: _Timer) -> None:
        self._timer = timer

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> float:
        return self._timer.stop()

    def value(self) -> float:
        return self._timer.value()

    def __enter__(self) -> "Timer":
        self._timer.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._timer.stop()


class MetricsManager:
    """In-memory metrics manager compatible with EdgeX MetricsManager interface."""

    def __init__(self) -> None:
        self._counters: Dict[str, _Counter] = {}
        self._gauges: Dict[str, _Gauge] = {}
        self._gauge_floats: Dict[str, _GaugeFloat64] = {}
        self._timers: Dict[str, _Timer] = {}
        self._lock = threading.Lock()

    def _make_key(self, name: str, labels: Optional[Dict[str, str]]) -> str:
        """Create a unique key from name and sorted labels."""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    # -- Counter --------------------------------------------------------------

    def new_counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> Counter:
        """Create or get a counter with the given name and labels."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._counters:
                self._counters[key] = _Counter()
            return Counter(self._counters[key])

    # -- Gauge ----------------------------------------------------------------

    def new_gauge(self, name: str, labels: Optional[Dict[str, str]] = None) -> Gauge:
        """Create or get an integer gauge with the given name and labels."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._gauges:
                self._gauges[key] = _Gauge()
            return Gauge(self._gauges[key])

    # -- GaugeFloat64 ---------------------------------------------------------

    def new_gauge_float64(self, name: str, labels: Optional[Dict[str, str]] = None) -> GaugeFloat64:
        """Create or get a float gauge with the given name and labels."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._gauge_floats:
                self._gauge_floats[key] = _GaugeFloat64()
            return GaugeFloat64(self._gauge_floats[key])

    # -- Timer ----------------------------------------------------------------

    def new_timer(self, name: str, labels: Optional[Dict[str, str]] = None) -> Timer:
        """Create or get a timer with the given name and labels."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._timers:
                self._timers[key] = _Timer()
            return Timer(self._timers[key])

    # -- Inspection -----------------------------------------------------------

    def get_all_metrics(self) -> Dict[str, Any]:
        """Return a snapshot of all metrics with their current values."""
        with self._lock:
            result = {}
            for k, v in self._counters.items():
                result[k] = {"type": "counter", "value": v.value}
            for k, v in self._gauges.items():
                result[k] = {"type": "gauge", "value": v.value}
            for k, v in self._gauge_floats.items():
                result[k] = {"type": "gauge_float64", "value": v.value}
            for k, v in self._timers.items():
                result[k] = {"type": "timer", "value": v.value}
            return result

    def register_counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> Counter:
        """Register a counter with the given name and labels.
        
        This is the Python equivalent of Go's InitializeSentMetrics - it registers
        the EventsSent and ReadingsSent counters with the MetricsManager at startup
        so they can be reported via the metrics endpoint.
        """
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._counters:
                self._counters[key] = _Counter()
            return Counter(self._counters[key])

    def register_gauge(self, name: str, labels: Optional[Dict[str, str]] = None) -> Gauge:
        """Register a gauge with the given name and labels."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._gauges:
                self._gauges[key] = _Gauge()
            return Gauge(self._gauges[key])

    def register_gauge_float64(self, name: str, labels: Optional[Dict[str, str]] = None) -> GaugeFloat64:
        """Register a float gauge with the given name and labels."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._gauge_floats:
                self._gauge_floats[key] = _GaugeFloat64()
            return GaugeFloat64(self._gauges[key])