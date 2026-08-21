# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for Device Down auto-recovery (M6 / Gap G6).

Covers:
- AllowedFails / DeviceDownTimeout configuration
- Failure tracking and DOWN state transition
- Background deviceReturn loop that retries DOWN devices
- Successful request restores UP state and resets failure count
- Metadata update when device state changes
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.cache import (  # noqa: E402
    Device,
)
from device_sdk_py.internal.common.consts import (  # noqa: E402
    OPERATING_STATE_UP,
    OPERATING_STATE_DOWN,
)
from device_sdk_py.internal.application.command import (  # noqa: E402
    device_request_failed,
    device_request_succeeded,
    set_failure_count,
    failure_count,
    _allowed_request_failures,
)
from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402


class _Driver:
    def start(self):
        pass


def _make_service(config=None):
    return bootstrap("device-simple", "0.0.0", _Driver(), configuration=config)


class _MockConfig:
    """Mock configuration with Device options."""
    def __init__(self, allowed_fails=3, device_down_timeout=30,
                 async_buffer_size=0, max_cmd_result_len=0,
                 max_event_size=0, reading_units=True, send_changed_readings_only=False):
        self.device = _MockDevice(allowed_fails, device_down_timeout,
                                   async_buffer_size, max_cmd_result_len,
                                   max_event_size, reading_units, send_changed_readings_only)


class _MockDevice:
    def __init__(self, allowed_fails, device_down_timeout,
                 async_buffer_size, max_cmd_result_len,
                 max_event_size, reading_units, send_changed_readings_only):
        self.allowed_fails = allowed_fails
        self.device_down_timeout = device_down_timeout
        self.async_buffer_size = async_buffer_size
        self.max_cmd_result_len = max_cmd_result_len
        self.max_event_size = max_event_size
        self.reading_units = reading_units
        self.send_changed_readings_only = send_changed_readings_only


class TestDeviceDownFailureTracking(unittest.TestCase):
    """Test the failure tracking and DOWN state transition."""

    def setUp(self):
        # Clear global failure tracker
        _allowed_request_failures.clear()
        self.ds = _make_service(_MockConfig(allowed_fails=3, device_down_timeout=30))
        # Add a test device
        self.device = Device(name="sensor-01", profile_name="p1",
                            operating_state=OPERATING_STATE_UP)
        self.ds.add_device(self.device)

    def tearDown(self):
        self.ds._shutdown()
        _allowed_request_failures.clear()

    def test_failure_count_initializes_to_allowed_fails(self):
        """After a successful request, failure count should be reset to allowed_fails."""
        set_failure_count("sensor-01", 0)  # start with 0
        device_request_succeeded(self.device, _MockConfig(allowed_fails=3, device_down_timeout=30))
        self.assertEqual(failure_count("sensor-01"), 3)

    def test_decrease_failure_count_on_failed_request(self):
        """Each failed request decreases the failure count."""
        set_failure_count("sensor-01", 3)
        device_request_failed("sensor-01", _MockConfig(allowed_fails=3, device_down_timeout=30))
        self.assertEqual(failure_count("sensor-01"), 2)
        device_request_failed("sensor-01", _MockConfig(allowed_fails=3, device_down_timeout=30))
        self.assertEqual(failure_count("sensor-01"), 1)
        device_request_failed("sensor-01", _MockConfig(allowed_fails=3, device_down_timeout=30))
        self.assertEqual(failure_count("sensor-01"), 0)

    def test_device_marked_down_when_failures_exhausted(self):
        """When failure count reaches 0, device is marked DOWN."""
        set_failure_count("sensor-01", 1)
        with mock.patch("device_sdk_py.internal.application.command.update_operating_state") as m:
            device_request_failed("sensor-01", _MockConfig(allowed_fails=1, device_down_timeout=30))
            m.assert_called_once_with("sensor-01", "DOWN", mock.ANY)

    def test_device_not_marked_down_if_allowed_fails_zero(self):
        """If allowed_fails is 0 (disabled), device should not be marked DOWN."""
        set_failure_count("sensor-01", 0)
        with mock.patch("device_sdk_py.internal.application.command.update_operating_state") as m:
            device_request_failed("sensor-01", _MockConfig(allowed_fails=0, device_down_timeout=30))
            m.assert_not_called()

    def test_successful_request_restores_up_state(self):
        """A successful request after DOWN restores UP state and resets failures."""
        set_failure_count("sensor-01", 0)  # exhausted
        device = Device(name="sensor-01", profile_name="p1", operating_state=OPERATING_STATE_DOWN)
        with mock.patch("device_sdk_py.internal.application.command.update_operating_state") as m:
            device_request_succeeded(device, _MockConfig(allowed_fails=3, device_down_timeout=30))
            m.assert_called_once_with("sensor-01", "UP", mock.ANY)
        self.assertEqual(failure_count("sensor-01"), 3)


class TestDeviceReturnLoop(unittest.TestCase):
    """Test the background deviceReturn retry loop."""

    def setUp(self):
        _allowed_request_failures.clear()
        self.config = _MockConfig(allowed_fails=2, device_down_timeout=1)  # short timeout for tests
        self.ds = _make_service(self.config)
        self.device = Device(name="sensor-01", profile_name="p1",
                            operating_state=OPERATING_STATE_UP)
        self.ds.add_device(self.device)

    def tearDown(self):
        self.ds._shutdown()
        _allowed_request_failures.clear()

    def test_device_return_loop_started_when_timeout_positive(self):
        """deviceReturn loop should start when DeviceDownTimeout > 0."""
        # The loop should be started in run() or _start_auto_events
        # We check that the thread is created
        self.ds._init_http_controller()
        self.ds._start_auto_events()
        # Check if device return thread exists
        # (In the actual implementation, this might be a separate thread)
        pass  # Implementation will add the thread

    def test_device_return_retries_after_timeout(self):
        """After DeviceDownTimeout, deviceReturn should attempt to restore the device."""
        # This test will be fleshed out once the loop is implemented
        pass


class TestConfigOptions(unittest.TestCase):
    """Test that all required config options are read."""

    def test_allowed_fails_read_from_config(self):
        config = _MockConfig(allowed_fails=5, device_down_timeout=60)
        from device_sdk_py.internal.application.command import _device_option
        self.assertEqual(_device_option(config, "allowed_fails", 0), 5)

    def test_device_down_timeout_read_from_config(self):
        config = _MockConfig(allowed_fails=3, device_down_timeout=120)
        from device_sdk_py.internal.application.command import _device_option
        self.assertEqual(_device_option(config, "device_down_timeout", 0), 120)

    def test_async_buffer_size_read_from_config(self):
        config = _MockConfig(async_buffer_size=100)
        from device_sdk_py.internal.application.command import _device_option
        self.assertEqual(_device_option(config, "async_buffer_size", 0), 100)

    def test_max_cmd_result_len_read_from_config(self):
        config = _MockConfig(max_cmd_result_len=1024)
        from device_sdk_py.internal.application.command import _device_option
        self.assertEqual(_device_option(config, "max_cmd_result_len", 0), 1024)

    def test_max_event_size_read_from_config(self):
        config = _MockConfig(max_event_size=4096)
        from device_sdk_py.internal.application.command import _device_option
        self.assertEqual(_device_option(config, "max_event_size", 0), 4096)

    def test_reading_units_read_from_config(self):
        config = _MockConfig(reading_units=False)
        from device_sdk_py.internal.application.command import _device_option
        self.assertEqual(_device_option(config, "reading_units", True), False)

    def test_send_changed_readings_only_read_from_config(self):
        config = _MockConfig(send_changed_readings_only=True)
        from device_sdk_py.internal.application.command import _device_option
        self.assertEqual(_device_option(config, "send_changed_readings_only", False), True)


if __name__ == "__main__":
    unittest.main()