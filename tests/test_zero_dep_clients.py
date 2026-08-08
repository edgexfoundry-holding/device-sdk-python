# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for zero-dependency EdgeX clients (M5 / Gap G5).

Covers the stdlib-backed implementations of:
- Logger (wrapping stdlib logging)
- SecretProvider (in-memory secret store)
- MetricsManager (in-memory counters/gauges/timers)

No external dependencies beyond stdlib.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


class TestLogger(unittest.TestCase):
    """Test the zero-dependency Logger implementation."""

    def test_logger_debug_info_warn_error(self):
        from device_sdk_py.internal.clients.logger import Logger
        logger = Logger("test")
        # Should not raise
        logger.debug("debug msg")
        logger.info("info msg")
        logger.warn("warn msg")
        logger.error("error msg")

    def test_logger_with_args(self):
        from device_sdk_py.internal.clients.logger import Logger
        logger = Logger("test")
        logger.info("hello %s", "world")
        logger.error("code %d", 404)

    def test_logger_with_field_returns_logger(self):
        from device_sdk_py.internal.clients.logger import Logger
        logger = Logger("test")
        child = logger.with_field("key", "value")
        self.assertIsInstance(child, Logger)
        child.info("test")

    def test_logger_with_fields_returns_logger(self):
        from device_sdk_py.internal.clients.logger import Logger
        logger = Logger("test")
        child = logger.with_fields({"a": 1, "b": 2})
        self.assertIsInstance(child, Logger)
        child.info("test")

    def test_logger_set_level(self):
        from device_sdk_py.internal.clients.logger import Logger
        logger = Logger("test")
        logger.set_level("DEBUG")
        logger.set_level("INFO")
        logger.set_level("WARN")
        logger.set_level("ERROR")


class TestSecretProvider(unittest.TestCase):
    """Test the zero-dependency SecretProvider implementation."""

    def setUp(self):
        from device_sdk_py.internal.clients.secret import InMemorySecretProvider
        self.provider = InMemorySecretProvider()

    def test_store_and_get_secret(self):
        self.provider.store_secret("my-path", "username", "admin")
        self.provider.store_secret("my-path", "password", "secret123")
        self.assertEqual(self.provider.get_secret("my-path", "username"), "admin")
        self.assertEqual(self.provider.get_secret("my-path", "password"), "secret123")

    def test_get_all_secrets(self):
        self.provider.store_secret("db", "user", "u1")
        self.provider.store_secret("db", "pass", "p1")
        secrets = self.provider.get_all_secrets("db")
        self.assertEqual(secrets, {"user": "u1", "pass": "p1"})

    def test_delete_secret(self):
        self.provider.store_secret("path", "key", "value")
        self.provider.delete_secret("path", "key")
        with self.assertRaises(KeyError):
            self.provider.get_secret("path", "key")

    def test_get_nonexistent_raises(self):
        with self.assertRaises(KeyError):
            self.provider.get_secret("nope", "key")

    def test_get_all_nonexistent_path_returns_empty(self):
        self.assertEqual(self.provider.get_all_secrets("nonexistent"), {})


class TestMetricsManager(unittest.TestCase):
    """Test the zero-dependency MetricsManager implementation."""

    def setUp(self):
        from device_sdk_py.internal.clients.metrics import MetricsManager
        self.manager = MetricsManager()

    def test_counter_register_and_increment(self):
        counter = self.manager.new_counter("requests_total", {"method": "GET"})
        counter.inc()
        counter.inc(2)
        self.assertEqual(counter.value(), 3)

    def test_counter_without_labels(self):
        counter = self.manager.new_counter("total")
        counter.inc()
        self.assertEqual(counter.value(), 1)

    def test_gauge_register_and_set(self):
        gauge = self.manager.new_gauge("queue_size", {"queue": "async"})
        gauge.set(10)
        self.assertEqual(gauge.value(), 10)
        gauge.add(5)
        self.assertEqual(gauge.value(), 15)
        gauge.sub(3)
        self.assertEqual(gauge.value(), 12)

    def test_gauge_float64(self):
        gauge = self.manager.new_gauge_float64("cpu_usage", {"core": "0"})
        gauge.set(12.5)
        self.assertAlmostEqual(gauge.value(), 12.5)
        gauge.add(1.5)
        self.assertAlmostEqual(gauge.value(), 14.0)

    def test_timer_register_and_record(self):
        import time
        timer = self.manager.new_timer("request_duration", {"endpoint": "/api"})
        timer.start()
        time.sleep(0.01)
        elapsed = timer.stop()
        self.assertGreater(elapsed, 0.005)
        self.assertLess(elapsed, 0.1)

    def test_timer_context_manager(self):
        import time
        timer = self.manager.new_timer("ctx_timer")
        with timer:
            time.sleep(0.01)
        # Context manager should record the duration
        self.assertGreater(timer.value(), 0.005)

    def test_get_all_metrics(self):
        self.manager.new_counter("c1").inc()
        self.manager.new_counter("c2").inc(5)
        self.manager.new_gauge("g1").set(42)
        metrics = self.manager.get_all_metrics()
        self.assertIn("c1", metrics)
        self.assertIn("c2", metrics)
        self.assertIn("g1", metrics)


class TestDeviceServiceIntegration(unittest.TestCase):
    """Test that DeviceService returns the zero-dep clients."""

    def test_logging_client_returns_logger(self):
        from device_sdk_py.service.bootstrap import bootstrap
        from device_sdk_py.internal.clients.logger import Logger

        class _Driver:
            def start(self): pass

        ds = bootstrap("test", "1.0", _Driver())
        client = ds.logging_client()
        self.assertIsInstance(client, Logger)
        ds._shutdown()

    def test_secret_provider_returns_provider(self):
        from device_sdk_py.service.bootstrap import bootstrap
        from device_sdk_py.internal.clients.secret import SecretProvider

        class _Driver:
            def start(self): pass

        ds = bootstrap("test", "1.0", _Driver())
        provider = ds.secret_provider()
        self.assertIsInstance(provider, SecretProvider)
        ds._shutdown()

    def test_metrics_manager_returns_manager(self):
        from device_sdk_py.service.bootstrap import bootstrap
        from device_sdk_py.internal.clients.metrics import MetricsManager

        class _Driver:
            def start(self): pass

        ds = bootstrap("test", "1.0", _Driver())
        manager = ds.metrics_manager()
        self.assertIsInstance(manager, MetricsManager)
        ds._shutdown()


if __name__ == "__main__":
    unittest.main()