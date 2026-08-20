# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Zero-dependency EdgeX client implementations (stdlib only).

This package provides in-memory/stdlib-backed implementations of the EdgeX
supporting services that the Device SDK depends on:

- Logger: wraps stdlib logging with EdgeX-compatible interface
- SecretProvider: in-memory (insecure) or OpenBao (secure) secret store
- MetricsManager: in-memory counters, gauges, timers

No external dependencies beyond Python standard library.
"""

from .logger import Logger
from .secret import (
    SecretProvider,
    InMemorySecretProvider,
    OpenBaoSecretProvider,
    create_secret_provider,
)
from .metrics import MetricsManager, Counter, Gauge, GaugeFloat64, Timer
from .data import CoreDataClient, CoreDataClientConfig, create_coredata_client
from .command import CoreCommandClient, CoreCommandClientConfig, create_corecommand_client
from .tls import TLSManager, TLSConfig, CertificateInfo, create_self_signed_cert, create_server_ssl_context, create_client_ssl_context

__all__ = [
    "Logger",
    "SecretProvider",
    "InMemorySecretProvider",
    "OpenBaoSecretProvider",
    "create_secret_provider",
    "MetricsManager",
    "Counter",
    "Gauge",
    "GaugeFloat64",
    "Timer",
    "CoreDataClient",
    "CoreDataClientConfig",
    "create_coredata_client",
    "CoreCommandClient",
    "CoreCommandClientConfig",
    "create_corecommand_client",
    "TLSManager",
    "TLSConfig",
    "CertificateInfo",
    "create_self_signed_cert",
    "create_server_ssl_context",
    "create_client_ssl_context",
]