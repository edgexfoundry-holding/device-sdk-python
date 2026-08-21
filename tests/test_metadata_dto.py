# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for the Core Metadata DTO serializers (M10 cleanup).

Covers `internal/metadata/dto.py`: the snake_case model -> camelCase Core Metadata
request JSON serialization for devices, profiles, services and provision watchers.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.cache import (  # noqa: E402
    ADMIN_STATE_LOCKED,
    ADMIN_STATE_UNLOCKED,
    AutoEvent,
    Device,
    DeviceCommand,
    DeviceProfile,
    DeviceResource,
    ProvisionWatcher,
    ResourceOperation,
    ResourceProperties,
)
from device_sdk_py.internal.metadata import dto  # noqa: E402


def _full_props():
    return ResourceProperties(
        value_type="Int16",
        read_write="RW",
        units="degC",
        minimum=-40.0,
        maximum=85.0,
        default_value="20",
        mask=0xFF,
        shift=2,
        scale=0.5,
        offset=1.0,
        base=10.0,
        assertion="ok",
        media_type="text/plain",
    )


def _full_resource():
    return DeviceResource(
        name="temperature",
        description="Room temperature",
        is_hidden=True,
        tag="temp-tag",
        properties=_full_props(),
        attributes={"primary": True},
    )


def _full_operation():
    return ResourceOperation(
        device_resource="temperature",
        default_value="21",
        mappings={"0": "off", "1": "on"},
        attributes={"table": "T1"},
    )


def _full_command():
    return DeviceCommand(
        name="read-temperature",
        is_hidden=True,
        read_write="R",
        resource_operations=[_full_operation()],
    )


class TestResourcePropertiesDto(unittest.TestCase):
    """Test _resource_properties_to_dto optional fields."""

    def test_full_properties(self):
        result = dto._resource_properties_to_dto(_full_props())
        self.assertEqual(result["valueType"], "Int16")
        self.assertEqual(result["readWrite"], "RW")
        self.assertEqual(result["units"], "degC")
        self.assertEqual(result["minimum"], -40.0)
        self.assertEqual(result["maximum"], 85.0)
        self.assertEqual(result["defaultValue"], "20")
        self.assertEqual(result["mask"], 0xFF)
        self.assertEqual(result["shift"], 2)
        self.assertEqual(result["scale"], 0.5)
        self.assertEqual(result["offset"], 1.0)
        self.assertEqual(result["base"], 10.0)
        self.assertEqual(result["assertion"], "ok")
        self.assertEqual(result["mediaType"], "text/plain")

    def test_minimal_properties(self):
        result = dto._resource_properties_to_dto(ResourceProperties(value_type="Bool", read_write="R"))
        self.assertEqual(result, {"valueType": "Bool", "readWrite": "R"})


class TestDeviceResourceDto(unittest.TestCase):
    """Test _device_resource_to_dto."""

    def test_full_resource(self):
        result = dto._device_resource_to_dto(_full_resource())
        self.assertEqual(result["name"], "temperature")
        self.assertEqual(result["description"], "Room temperature")
        self.assertIs(result["isHidden"], True)
        self.assertEqual(result["tag"], "temp-tag")
        self.assertEqual(result["attributes"], {"primary": True})
        self.assertEqual(result["properties"]["valueType"], "Int16")

    def test_minimal_resource(self):
        result = dto._device_resource_to_dto(DeviceResource(name="r"))
        self.assertEqual(result, {"name": "r", "properties": {"valueType": "", "readWrite": ""}})


class TestOperationDto(unittest.TestCase):
    """Test _operation_to_dto."""

    def test_full_operation(self):
        result = dto._operation_to_dto(_full_operation())
        self.assertEqual(result["deviceResource"], "temperature")
        self.assertEqual(result["defaultValue"], "21")
        self.assertEqual(result["mappings"], {"0": "off", "1": "on"})
        self.assertEqual(result["attributes"], {"table": "T1"})

    def test_minimal_operation(self):
        result = dto._operation_to_dto(ResourceOperation(device_resource="x"))
        self.assertEqual(result, {"deviceResource": "x"})


class TestCommandDto(unittest.TestCase):
    """Test _command_to_dto."""

    def test_full_command(self):
        result = dto._command_to_dto(_full_command())
        self.assertEqual(result["name"], "read-temperature")
        self.assertIs(result["isHidden"], True)
        self.assertEqual(result["readWrite"], "R")
        self.assertEqual(len(result["resourceOperations"]), 1)

    def test_minimal_command(self):
        result = dto._command_to_dto(DeviceCommand(name="c"))
        self.assertEqual(result, {"name": "c", "resourceOperations": []})


class TestDeviceProfileDto(unittest.TestCase):
    """Test device_profile_to_dto + add_device_profile_request."""

    def _profile(self):
        return DeviceProfile(
            name="p1",
            description="desc",
            manufacturer="acme",
            model="m1",
            labels=["a", "b"],
            device_resources=[_full_resource()],
            device_commands=[_full_command()],
            add_tags={"zone": "north"},
            properties={"k": "v"},
        )

    def test_full_profile(self):
        result = dto.device_profile_to_dto(self._profile())
        self.assertEqual(result["name"], "p1")
        self.assertEqual(result["description"], "desc")
        self.assertEqual(result["manufacturer"], "acme")
        self.assertEqual(result["model"], "m1")
        self.assertEqual(result["labels"], ["a", "b"])
        self.assertEqual(len(result["deviceResources"]), 1)
        self.assertEqual(len(result["deviceCommands"]), 1)
        self.assertEqual(result["addTags"], {"zone": "north"})
        self.assertEqual(result["properties"], {"k": "v"})

    def test_minimal_profile(self):
        result = dto.device_profile_to_dto(DeviceProfile(name="p1"))
        self.assertEqual(result["name"], "p1")
        self.assertEqual(result["deviceResources"], [])
        self.assertEqual(result["deviceCommands"], [])

    def test_add_device_profile_request_envelope(self):
        req = dto.add_device_profile_request(self._profile())
        self.assertEqual(req["apiVersion"], "v3")
        self.assertIn("requestId", req)
        self.assertEqual(req["profile"]["name"], "p1")


class TestAutoEventDto(unittest.TestCase):
    """Test _auto_event_to_dto."""

    def test_full_event(self):
        event = AutoEvent(source_name="s1", on_change=True, on_change_threshold=0.5, interval="5s")
        result = dto._auto_event_to_dto(event)
        self.assertEqual(result["sourceName"], "s1")
        self.assertIs(result["onChange"], True)
        self.assertEqual(result["onChangeThreshold"], 0.5)
        self.assertEqual(result["interval"], "5s")

    def test_minimal_event(self):
        result = dto._auto_event_to_dto(AutoEvent(source_name="s1"))
        self.assertEqual(result, {"sourceName": "s1"})


class TestDeviceDto(unittest.TestCase):
    """Test device_to_dto + add_device_request."""

    def _device(self):
        return Device(
            id="dev-1",
            name="sensor-01",
            description="a sensor",
            admin_state=ADMIN_STATE_LOCKED,
            operating_state="UP",
            labels=["lab"],
            location={"floor": 3},
            service_name="device-simple",
            profile_name="p1",
            auto_events=[AutoEvent(source_name="s1", interval="10s")],
            protocols={"modbus": {"unit": "1"}},
            last_connected=100,
            last_reported=200,
            tags={"a": "b"},
            properties={"x": 1},
        )

    def test_full_device(self):
        result = dto.device_to_dto(self._device())
        self.assertEqual(result["id"], "dev-1")
        self.assertEqual(result["name"], "sensor-01")
        self.assertEqual(result["description"], "a sensor")
        self.assertEqual(result["adminState"], "LOCKED")
        self.assertEqual(result["operatingState"], "UP")
        self.assertEqual(result["labels"], ["lab"])
        self.assertEqual(result["location"], {"floor": 3})
        self.assertEqual(result["serviceName"], "device-simple")
        self.assertEqual(result["profileName"], "p1")
        self.assertEqual(result["autoEvents"][0]["sourceName"], "s1")
        self.assertEqual(result["protocols"], {"modbus": {"unit": "1"}})
        self.assertEqual(result["lastConnected"], 100)
        self.assertEqual(result["lastReported"], 200)
        self.assertEqual(result["tags"], {"a": "b"})
        self.assertEqual(result["properties"], {"x": 1})

    def test_minimal_device(self):
        result = dto.device_to_dto(Device(name="sensor-01"))
        self.assertEqual(result["name"], "sensor-01")
        self.assertEqual(result["protocols"], {})

    def test_add_device_request_envelope(self):
        req = dto.add_device_request(self._device())
        self.assertEqual(req["apiVersion"], "v3")
        self.assertIn("requestId", req)
        self.assertEqual(req["device"]["name"], "sensor-01")


class TestUpdateDeviceRequest(unittest.TestCase):
    """Test update_device_request field mapping."""

    def test_maps_known_fields(self):
        req = dto.update_device_request("sensor-01", {
            "description": "new desc",
            "admin_state": "LOCKED",
            "operating_state": "DOWN",
            "service_name": "device-simple",
            "profile_name": "p2",
            "labels": ["x"],
            "location": {"a": 1},
            "tags": {"t": "1"},
            "properties": {"p": 2},
        })
        body = req["device"]
        self.assertEqual(body["name"], "sensor-01")
        self.assertEqual(body["description"], "new desc")
        self.assertEqual(body["adminState"], "LOCKED")
        self.assertEqual(body["operatingState"], "DOWN")
        self.assertEqual(body["serviceName"], "device-simple")
        self.assertEqual(body["profileName"], "p2")
        self.assertEqual(body["labels"], ["x"])
        self.assertEqual(body["location"], {"a": 1})
        self.assertEqual(body["tags"], {"t": "1"})
        self.assertEqual(body["properties"], {"p": 2})

    def test_drops_none_values(self):
        req = dto.update_device_request("sensor-01", {
            "description": None,
            "admin_state": None,
        })
        body = req["device"]
        self.assertEqual(body, {"name": "sensor-01"})

    def test_drops_unknown_keys(self):
        req = dto.update_device_request("sensor-01", {
            "unknown_key": "x",
            "id": "should-not-be-here",
        })
        self.assertEqual(req["device"], {"name": "sensor-01"})


class TestDeviceServiceDto(unittest.TestCase):
    """Test device_service_to_dto + add_device_service_request."""

    def test_full_service(self):
        result = dto.device_service_to_dto(
            "device-simple", "http://localhost:59986", "LOCKED", ["a"], {"k": "v"})
        self.assertEqual(result["name"], "device-simple")
        self.assertEqual(result["baseAddress"], "http://localhost:59986")
        self.assertEqual(result["adminState"], "LOCKED")
        self.assertEqual(result["labels"], ["a"])
        self.assertEqual(result["properties"], {"k": "v"})

    def test_defaults(self):
        result = dto.device_service_to_dto("device-simple", "", "", None, None)
        self.assertEqual(result["name"], "device-simple")
        self.assertNotIn("baseAddress", result)
        self.assertEqual(result["adminState"], "UNLOCKED")
        self.assertEqual(result["properties"], {})

    def test_add_device_service_request(self):
        svc = {"name": "device-simple", "adminState": "UNLOCKED"}
        req = dto.add_device_service_request(svc)
        self.assertEqual(req["apiVersion"], "v3")
        self.assertEqual(req["service"], svc)


class TestProvisionWatcherDto(unittest.TestCase):
    """Test provision_watcher_to_dto + add/update requests."""

    def _watcher(self):
        return ProvisionWatcher(
            id="pw-1",
            name="discover-by-address",
            description="watcher desc",
            service_name="device-simple",
            labels=["l"],
            identifiers={"address": "192.168.1.1"},
            blocking_identifiers={"address": ["192.168.1.0", "192.168.1.1"]},
            admin_state=ADMIN_STATE_LOCKED,
            profile_name="p1",
        )

    def test_full_watcher(self):
        result = dto.provision_watcher_to_dto(self._watcher())
        self.assertEqual(result["id"], "pw-1")
        self.assertEqual(result["name"], "discover-by-address")
        self.assertEqual(result["description"], "watcher desc")
        self.assertEqual(result["serviceName"], "device-simple")
        self.assertEqual(result["labels"], ["l"])
        self.assertEqual(result["identifiers"], {"address": "192.168.1.1"})
        self.assertEqual(result["blockingIdentifiers"]["address"],
                         ["192.168.1.0", "192.168.1.1"])
        self.assertEqual(result["adminState"], "LOCKED")
        self.assertEqual(result["profileName"], "p1")
        self.assertEqual(result["discoveredDevice"]["adminState"], "LOCKED")
        self.assertEqual(result["discoveredDevice"]["profileName"], "p1")

    def test_minimal_watcher(self):
        result = dto.provision_watcher_to_dto(ProvisionWatcher(name="w"))
        self.assertEqual(result["name"], "w")
        self.assertEqual(result["discoveredDevice"], {"adminState": "UNLOCKED"})

    def test_add_watcher_request(self):
        req = dto.add_provision_watcher_request(self._watcher())
        self.assertEqual(req["provisionwatcher"]["name"], "discover-by-address")
        self.assertEqual(req["provisionwatcher"]["discoveredDevice"]["adminState"], "LOCKED")

    def test_update_watcher_request_excludes_discovered_device(self):
        req = dto.update_provision_watcher_request(self._watcher())
        body = req["provisionwatcher"]
        self.assertEqual(body["name"], "discover-by-address")
        self.assertNotIn("discoveredDevice", body)
        self.assertEqual(body["blockingIdentifiers"]["address"],
                         ["192.168.1.0", "192.168.1.1"])
        self.assertEqual(body["adminState"], "LOCKED")
        self.assertEqual(body["profileName"], "p1")


class TestRequestEnvelope(unittest.TestCase):
    """Test the shared _request envelope helper."""

    def test_request_has_api_version_and_request_id(self):
        req = dto._request({"device": {"name": "x"}})
        self.assertEqual(req["apiVersion"], "v3")
        self.assertIsInstance(req["requestId"], str)
        self.assertTrue(req["requestId"])
        self.assertEqual(req["device"], {"name": "x"})


if __name__ == "__main__":
    unittest.main()
