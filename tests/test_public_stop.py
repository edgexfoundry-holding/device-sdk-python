# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for public stop() method (M8 / Gap G8).

Covers the public stop() method that gracefully shuts down the Device Service.
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

from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402


class _Driver:
    def start(self):
        pass


def _make_service(config=None):
    return bootstrap("device-simple", "0.0.0", _Driver(), configuration=config)


class TestPublicStop(unittest.TestCase):
    """Test the public stop() method."""

    def setUp(self):
        self.ds = _make_service()

    def tearDown(self):
        # Don't call _shutdown here - we're testing stop()
        pass

    def test_stop_method_exists(self):
        """DeviceService should have a public stop() method."""
        self.assertTrue(hasattr(self.ds, "stop"))
        self.assertTrue(callable(getattr(self.ds, "stop", None)))

    def test_stop_calls_shutdown(self):
        """stop() should call the internal _shutdown logic."""
        with mock.patch.object(self.ds, "_shutdown") as m:
            self.ds.stop()
            m.assert_called_once()

    def test_stop_is_idempotent(self):
        """Calling stop() twice should not raise errors."""
        self.ds.stop()
        self.ds.stop()  # Should not raise

    def test_stop_stops_background_threads(self):
        """After stop(), background threads should be joined."""
        # Start some pumps
        self.ds._start_async_pumps()
        self.ds.stop()
        # Threads should be stopped
        for t in (self.ds._async_pump_thread, self.ds._discovered_pump_thread):
            if t is not None:
                self.assertFalse(t.is_alive(), "Thread should be stopped after stop()")

    def test_stop_stops_device_return_pump(self):
        """stop() should stop the device return pump if running."""
        # This test verifies the device return pump is also stopped
        self.ds.stop()
        if self.ds._device_return_thread is not None:
            self.assertFalse(self.ds._device_return_thread.is_alive())

    def test_stop_stops_config_watch_threads(self):
        """stop() should stop any config watch threads."""
        self.ds.stop()
        if hasattr(self.ds, "_config_watch_threads"):
            for t in self.ds._config_watch_threads.values():
                self.assertFalse(t.is_alive(), "Config watch thread should be stopped")

    def test_stop_stops_metadata_executor(self):
        """stop() should shutdown the metadata executor."""
        self.ds.stop()
        # If executor was created, it should be shutdown
        if self.ds._metadata_executor is not None:
            self.assertTrue(self.ds._metadata_executor._shutdown)

    def test_stop_disconnects_messaging_client(self):
        """stop() should disconnect messaging client if present."""
        mock_client = mock.MagicMock()
        self.ds._messaging_client = mock_client
        self.ds.stop()
        mock_client.disconnect.assert_called_once()

    def test_stop_logs_completion(self):
        """stop() should log shutdown completion."""
        with mock.patch.object(self.ds._logger, "info") as m:
            self.ds.stop()
            # Check that shutdown complete was logged
            calls = [call.args[0] for call in m.call_args_list]
            self.assertTrue(any("shutdown complete" in c.lower() for c in calls))


class TestStopInterface(unittest.TestCase):
    """Test that the stop() method is in the interface."""

    def test_stop_in_device_service_sdk_interface(self):
        """DeviceServiceSDK interface should declare stop()."""
        from device_sdk_py.interfaces.service import DeviceServiceSDK
        self.assertTrue(hasattr(DeviceServiceSDK, "stop"))
        self.assertTrue(callable(getattr(DeviceServiceSDK, "stop", None)))


if __name__ == "__main__":
    unittest.main()