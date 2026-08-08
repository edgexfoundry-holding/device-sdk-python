# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Auto-discovery scheduler for EdgeX Device Service.

Mirrors `device-sdk-go/internal/autodiscovery/autodiscovery.go` and `discovery.go`.
Provides scheduled device discovery based on configuration interval.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..cache import Devices, ProvisionWatchers
from ...interfaces import ExtendedProtocolDriver

if TYPE_CHECKING:
    from ...service.device_service import DeviceService

_LOGGER = logging.getLogger(__name__)

# Maximum time to wait for discovery to complete before forcing stop
DISCOVERY_TIMEOUT = 300  # seconds


class _DiscoveryLocker:
    """Thread-safe locker to prevent concurrent discovery runs."""

    def __init__(self):
        self._busy = False
        self._lock = threading.Lock()

    @contextmanager
    def lock(self):
        """Acquire discovery lock. Returns False if already busy."""
        acquired = self._lock.acquire(blocking=False)
        try:
            if acquired and not self._busy:
                self._busy = True
                yield True
            else:
                yield False
        finally:
            if acquired:
                self._busy = False
                self._lock.release()


_locker = _DiscoveryLocker()


def run_discovery_wrapper(
    driver: ExtendedProtocolDriver,
    ctx_cancel: threading.Event,
    dic: Any,
) -> None:
    """Wrapper to run device discovery with proper locking and error handling.

    Mirrors `autodiscovery.DiscoveryWrapper` from Go SDK.

    Args:
        driver: The protocol driver implementing ExtendedProtocolDriver.
        ctx_cancel: Cancellation event to signal shutdown.
        dic: Dependency injection container (not used in Python port).
    """
    with _locker.lock() as acquired:
        if not acquired:
            _LOGGER.info("Another device discovery process is currently running")
            return

        _LOGGER.info("Starting scheduled device discovery")
        try:
            driver.discover()
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.exception("Scheduled device discovery failed: %s", exc)
        finally:
            _LOGGER.info("Scheduled device discovery completed")


def run_discovery_scheduler(
    driver: ExtendedProtocolDriver,
    device_service: "DeviceService",
    ctx_cancel: threading.Event,
    discovery_interval: float,
) -> None:
    """Run the auto-discovery scheduler loop.

    This runs in a background thread and triggers device discovery at the
    configured interval until the context is cancelled.

    Args:
        driver: The protocol driver.
        device_service: The DeviceService instance.
        ctx_cancel: Cancellation event.
        discovery_interval: Interval in seconds between discovery runs.
    """
    _LOGGER.info("Auto-discovery scheduler started with interval %s seconds", discovery_interval)

    while not ctx_cancel.is_set():
        # Wait for the interval or until cancelled
        ctx_cancel.wait(timeout=discovery_interval)
        if ctx_cancel.is_set():
            break

        # Run discovery
        run_discovery_wrapper(driver, ctx_cancel, None)


def bootstrap_autodiscovery(
    driver: ExtendedProtocolDriver,
    device_service: "DeviceService",
    shutdown_event: threading.Event,
    discovery_interval: float,
) -> threading.Thread:
    """Initialize and start the auto-discovery scheduler.

    Mirrors `autodiscovery.BootstrapHandler` from Go SDK.

    Args:
        driver: The protocol driver.
        device_service: The DeviceService instance.
        shutdown_event: Event to signal shutdown.
        discovery_interval: Discovery interval in seconds.

    Returns:
        The started scheduler thread.
    """
    if discovery_interval <= 0:
        return None

    thread = threading.Thread(
        target=run_discovery_scheduler,
        args=(driver, device_service, shutdown_event, discovery_interval),
        daemon=True,
        name="autodiscovery-scheduler",
    )
    thread.start()
    return thread