# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the auto-discovery scheduler (M11).

Covers the discovery locker, scheduler loop, and bootstrap handler that
mirror device-sdk-go's internal/autodiscovery package.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.autodiscovery import (  # noqa: E402
    _DiscoveryLocker,
    bootstrap_autodiscovery,
    run_discovery_scheduler,
    run_discovery_wrapper,
)


class _DiscoveringDriver:
    """Test driver implementing discover()."""

    def __init__(self):
        self.discover_calls = 0
        self.raise_error = False

    def discover(self):
        self.discover_calls += 1
        if self.raise_error:
            raise RuntimeError("discovery exploded")


class TestDiscoveryLocker(unittest.TestCase):
    """Test the lock preventing concurrent discovery runs."""

    def setUp(self):
        self.locker = _DiscoveryLocker()

    def test_lock_acquired_once(self):
        with self.locker.lock() as acquired:
            self.assertTrue(acquired)
            # Second acquisition while busy should be refused
            with self.locker.lock() as second:
                self.assertFalse(second)
        # After exiting, lock is free again
        with self.locker.lock() as acquired:
            self.assertTrue(acquired)

    def test_lock_releases_on_exception(self):
        with self.assertRaises(RuntimeError):
            with self.locker.lock() as acquired:
                self.assertTrue(acquired)
                raise RuntimeError("boom")
        with self.locker.lock() as acquired:
            self.assertTrue(acquired)


class TestRunDiscoveryWrapper(unittest.TestCase):
    """Test the discovery wrapper with a real driver."""

    def setUp(self):
        self.driver = _DiscoveringDriver()
        self.cancel = threading.Event()

    def test_wrapper_calls_driver(self):
        run_discovery_wrapper(self.driver, self.cancel, None)
        self.assertEqual(self.driver.discover_calls, 1)

    def test_wrapper_handles_exception(self):
        self.driver.raise_error = True
        run_discovery_wrapper(self.driver, self.cancel, None)
        self.assertEqual(self.driver.discover_calls, 1)

    def test_wrapper_skips_when_busy(self):
        with mock.patch("device_sdk_py.internal.autodiscovery._locker") as locker:
            locker.lock.return_value.__enter__ = mock.Mock(return_value=False)
            locker.lock.return_value.__exit__ = mock.Mock(return_value=False)
            run_discovery_wrapper(self.driver, self.cancel, None)
            self.assertEqual(self.driver.discover_calls, 0)


class TestScheduler(unittest.TestCase):
    """Test the scheduler loop and bootstrap handler."""

    def setUp(self):
        self.driver = _DiscoveringDriver()
        self.cancel = threading.Event()

    def test_bootstrap_returns_none_for_non_positive_interval(self):
        thread = bootstrap_autodiscovery(self.driver, None, self.cancel, 0)
        self.assertIsNone(thread)

    def test_bootstrap_starts_thread(self):
        thread = bootstrap_autodiscovery(self.driver, None, self.cancel, 0.05)
        self.assertIsNotNone(thread)
        self.assertTrue(thread.is_alive())
        self.cancel.set()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())

    def test_scheduler_runs_discovery_at_interval(self):
        scheduler = threading.Thread(
            target=run_discovery_scheduler,
            args=(self.driver, None, self.cancel, 0.02),
            daemon=True,
        )
        scheduler.start()
        time.sleep(0.15)
        self.cancel.set()
        scheduler.join(timeout=2.0)
        self.assertGreaterEqual(self.driver.discover_calls, 2)

    def test_scheduler_stops_on_cancel(self):
        self.cancel.set()
        run_discovery_scheduler(self.driver, None, self.cancel, 0.01)
        self.assertEqual(self.driver.discover_calls, 0)


import time  # noqa: E402


if __name__ == "__main__":
    unittest.main()
