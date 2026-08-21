# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""Core Keeper registry client for service self-registration.

Mirrors ``go-mod-bootstrap`` ``bootstrap/registration`` + ``go-mod-registry``: the
device service registers itself with the Core Keeper registry at startup (with retry
until the startup deadline elapses) and deregisters on shutdown. Core Keeper probes
the registered health-check endpoint and flips ``status`` between UP and DOWN.
"""

from .client import CoreKeeperRegistryClient, RegistryError

__all__ = ["CoreKeeperRegistryClient", "RegistryError"]
