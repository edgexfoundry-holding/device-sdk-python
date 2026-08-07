# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for internal/application/command.py

Covers command_read, command_write, device_request_failed/succeeded,
and the internal helpers (_command_values_to_event, _validate_service_and_device_state, etc.)
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

from device_sdk_py.internal.cache import (  # noqa: E402
    Device,
    DeviceProfile,
    DeviceResource,
    ResourceProperties,
    ResourceOperation,
    AutoEvent,
    Devices,
    Profiles,
    ADMIN_STATE_UNLOCKED,
)
from device_sdk_py.internal.common.consts import (  # noqa: E402
    OPERATING_STATE_UP,
    OPERATING_STATE_DOWN,
)
from device_sdk_py.internal.common.utils import (  # noqa: E402
    EdgexErrorKind,
    KIND_SERVICE_LOCKED,
    KIND_CONTRACT_INVALID,
    KIND_ENTITY_DOES_NOT_EXIST,
    create_edgx_error,
)
from device_sdk_py.internal.application import command  # noqa: E402
from device_sdk_py.models import CommandRequest, CommandValue  # noqa: E402
from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402


class _Driver:
    def start(self):
        pass


def _make_service(config=None):
    return bootstrap("device-simple", "0.0.0", _Driver(), configuration=config)


class _MockConfig:
    def __init__(self, **options):
        self.device = type('_MockDevice', (), options)()


class TestCommandApplication(unittest.TestCase):
    """Test the command application layer."""

    def setUp(self):
        self.ds = bootstrap("device-simple", "0.0.0", _Driver())
        # Add a test device and profile
        profile = DeviceProfile(name="p1")
        resource = DeviceResource(
            name="res1",
            properties=ResourceProperties(value_type="String", units="C"),
            attributes={}
        )
        profile.device_resources = [resource]
        Profiles().add(profile)
        
        self.device = Device(name="sensor-01", profile_name="p1", operating_state="UP")
        Devices().add(self.device)

    def tearDown(self):
        self.ds._shutdown()

    def test_device_option_reads_config(self):
        """_device_option should read from configuration.device."""
        cfg = type('Config', (), {'device': type('Device', (), {'allowed_fails': 5})()})()
        self.assertEqual(command._device_option(cfg, "allowed_fails", 0), 5)
        self.assertEqual(command._device_option(cfg, "nonexistent", 42), 42)
        
        # No device config
        cfg2 = type('Config', (), {})()
        self.assertEqual(command._device_option(cfg2, "allowed_fails", 0), 0)

    def test_failure_count_tracking(self):
        """Failure count should be tracked per device."""
        from device_sdk_py.internal.application.command import (
            set_failure_count, failure_count, decrease_failure_count,
            _allowed_request_failures
        )
        _allowed_request_failures.clear()
        
        set_failure_count("dev1", 3)
        self.assertEqual(failure_count("dev1"), 3)
        self.assertEqual(failure_count("dev2"), 0)
        
        decrease_failure_count("dev1")
        self.assertEqual(failure_count("dev1"), 2)
        
        decrease_failure_count("dev1")
        self.assertEqual(failure_count("dev1"), 1)
        decrease_failure_count("dev1")
        self.assertEqual(failure_count("dev1"), 0)

    def test_validate_service_and_device_state_locked(self):
        """Should raise when service is locked."""
        mock_service = mock.MagicMock()
        mock_service.admin_state = "LOCKED"
        
        with self.assertRaises(Exception) as ctx:
            command._validate_service_and_device_state("dev", None, mock_service)
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVICE_LOCKED)

    def test_validate_service_and_device_state_down(self):
        """Should allow DOWN device when failure_count is 0 (device return attempt)."""
        self.ds.configuration = _MockConfig(allowed_fails=3, device_down_timeout=30)
        
        # Device in DOWN state - need to update existing device
        device, _ = Devices().for_name("sensor-01")
        device.operating_state = OPERATING_STATE_DOWN
        Devices().update(device)
        
        # Should NOT raise when failure_count is 0 (device return attempt allowed)
        device = command._validate_service_and_device_state("sensor-01", self.ds.configuration, self.ds)
        self.assertEqual(device.name, "sensor-01")
        
        # But should raise when failure_count > 0
        from device_sdk_py.internal.application.command import set_failure_count
        set_failure_count("sensor-01", 0)  # exhaust failures
        with self.assertRaises(Exception) as ctx:
            command._validate_service_and_device_state("sensor-01", self.ds.configuration, self.ds)
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.SERVICE_LOCKED)

    def test_validate_service_and_device_state_ok(self):
        """Should succeed when device is UP."""
        self.ds.configuration = _MockConfig()
        # Device already added in setUp with operating_state=UP
        device = command._validate_service_and_device_state("sensor-01", self.ds.configuration, self.ds)
        self.assertEqual(device.name, "sensor-01")

    def test_command_read_success(self):
        """command_read should return an Event for valid read."""
        # Mock driver to return a CommandValue
        mock_driver = mock.MagicMock()
        mock_driver.handle_read_commands.return_value = [
            CommandValue(device_resource_name="res1", value_type="String", value="25")
        ]
        
        cfg = _MockConfig()
        event = command.command_read(
            device_name="sensor-01",
            request_id="req-123",
            command_name="res1",
            driver=mock_driver,
            configuration=cfg,
            attributes="",
            regex_cmd=False,
            device_service=self.ds,
            logger=mock.MagicMock()
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.device_name, "sensor-01")
        mock_driver.handle_read_commands.assert_called_once()

    def test_command_read_device_not_found(self):
        """command_read should raise when device not found."""
        mock_driver = mock.MagicMock()
        cfg = _MockConfig()
        
        with self.assertRaises(Exception) as ctx:
            command.command_read(
                device_name="nonexistent",
                request_id="req-123",
                command_name="res1",
                driver=mock_driver,
                configuration=cfg,
                attributes="",
                regex_cmd=False,
                device_service=self.ds,
                logger=mock.MagicMock()
            )
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_command_read_write_only_resource(self):
        """command_read should raise for write-only resource."""
        # Add a write-only resource
        resource = DeviceResource(
            name="write_only",
            properties=ResourceProperties(value_type="String", read_write="W"),
            attributes={}
        )
        profile = Profiles().for_name("p1")[0]
        profile.device_resources = [resource]
        Profiles().update(profile)
        
        mock_driver = mock.MagicMock()
        cfg = _MockConfig()
        
        with self.assertRaises(Exception) as ctx:
            command.command_read(
                device_name="sensor-01",
                request_id="req-123",
                command_name="write_only",
                driver=mock_driver,
                configuration=cfg,
                attributes="",
                regex_cmd=False,
                device_service=self.ds,
                logger=mock.MagicMock()
            )
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.NOT_ALLOWED)

    def test_command_write_set_command(self):
        """command_write should execute DeviceCommand."""
        # First add a DeviceCommand to the profile
        from device_sdk_py.internal.cache import DeviceCommand, ResourceOperation
        cmd = DeviceCommand(name="cmd1", resource_operations=[
            ResourceOperation(device_resource="res1")
        ])
        profile = Profiles().for_name("p1")[0]
        profile.device_commands = [cmd]
        Profiles().update(profile)
        
        mock_driver = mock.MagicMock()
        mock_driver.handle_write_commands.return_value = None
        cfg = _MockConfig()
        
        event = command.command_write(
            device_name="sensor-01",
            request_id="req-123",
            command_name="cmd1",
            driver=mock_driver,
            configuration=cfg,
            requests={"res1": "value1"},
            attributes="",
            device_service=self.ds,
            logger=mock.MagicMock()
        )
        # Should not raise and return None for write-only
        mock_driver.handle_write_commands.assert_called_once()

    def test_command_write_device_not_found(self):
        """command_write should raise when device not found."""
        cfg = _MockConfig()
        with self.assertRaises(Exception) as ctx:
            command.command_write(
                device_name="nonexistent",
                request_id="req-123",
                command_name="cmd1",
                driver=mock.MagicMock(),
                configuration=cfg,
                requests={},
                attributes="",
                device_service=self.ds,
                logger=mock.MagicMock()
            )
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.ENTITY_DOES_NOT_EXIST)

    def test_device_request_failed_tracks_failures(self):
        """device_request_failed should track failure count."""
        from device_sdk_py.internal.application.command import (
            set_failure_count, failure_count, _allowed_request_failures
        )
        _allowed_request_failures.clear()
        
        set_failure_count("sensor-01", 2)
        cfg = _MockConfig(allowed_fails=2)
        
        command.device_request_failed("sensor-01", cfg, mock.MagicMock())
        self.assertEqual(failure_count("sensor-01"), 1)
        
        command.device_request_failed("sensor-01", cfg, mock.MagicMock())
        self.assertEqual(failure_count("sensor-01"), 0)

    def test_device_request_succeeded_resets_failures(self):
        """device_request_succeeded should reset failure count."""
        from device_sdk_py.internal.application.command import (
            set_failure_count, failure_count, _allowed_request_failures
        )
        _allowed_request_failures.clear()
        
        device = Device(name="sensor-01", profile_name="p1")
        set_failure_count("sensor-01", 0)  # exhausted
        
        cfg = _MockConfig(allowed_fails=3)
        command.device_request_succeeded(device, cfg, mock.MagicMock())
        self.assertEqual(failure_count("sensor-01"), 3)

    def test_device_request_failed_no_allowed_fails(self):
        """device_request_failed should not track when allowed_fails=0."""
        from device_sdk_py.internal.application.command import _allowed_request_failures
        _allowed_request_failures.clear()
        
        cfg = _MockConfig(allowed_fails=0)
        command.device_request_failed("sensor-01", cfg, mock.MagicMock())
        # Should not have created entry
        self.assertNotIn("sensor-01", _allowed_request_failures)


class TestCommandValueCreation(unittest.TestCase):
    """Test create_command_value_from_device_resource and related."""

    def test_create_command_value(self):
        """create_command_value_from_device_resource should create CommandValue."""
        resource = DeviceResource(
            name="res1",
            properties=ResourceProperties(value_type="Int32"),
            attributes={}
        )
        cv = command.create_command_value_from_device_resource(resource, 42)
        self.assertEqual(cv.value_type, "Int32")
        self.assertEqual(cv.value, 42)

    def test_create_command_value_none(self):
        """None value should produce CommandValue with None."""
        resource = DeviceResource(
            name="res1",
            properties=ResourceProperties(value_type="String"),
            attributes={}
        )
        cv = command.create_command_value_from_device_resource(resource, None)
        self.assertIsNone(cv.value)

    def test_transform_write_parameter(self):
        """_transform_write_parameter should apply transformations."""
        resource = DeviceResource(
            name="res1",
            properties=ResourceProperties(value_type="Int32", minimum="0", maximum="100"),
            attributes={}
        )
        cv = CommandValue(device_resource_name="res1", value_type="Int32", value=50)
        # Should not raise
        command._transform_write_parameter(cv, resource.properties)
        
        # Test overflow
        cv.value = 200
        with self.assertRaises(Exception):
            command._transform_write_parameter(cv, resource.properties)


if __name__ == "__main__":
    unittest.main()