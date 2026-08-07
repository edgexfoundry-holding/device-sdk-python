# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Zero-dependency EdgeX client implementations (stdlib only).

This package provides in-memory/stdlib-backed implementations of the EdgeX
supporting services that the Device SDK depends on:

- Logger: wraps stdlib logging with EdgeX-compatible interface
- SecretProvider: in-memory secret store
- MetricsManager: in-memory counters, gauges, timers

No external dependencies beyond Python standard library.
"""

from .logger import Logger
from .secret import SecretProvider
from .metrics import MetricsManager, Counter, Gauge, GaugeFloat64, Timer

__all__ = [
    "Logger",
    "SecretProvider",
    "MetricsManager",
    "Counter",
    "Gauge",
    "GaugeFloat64",
    "Timer",
]