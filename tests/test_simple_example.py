# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache 2.0
"""End-to-end tests for the ``device-simple`` example and the ``res/`` loader pipeline.

Covers:
  * the ``provision`` loader (JSON + YAML, snake-case coercion),
  * ``DeviceService.initialize_resources`` registering profiles/devices/watchers,
  * the device-simple example serving ``/ping`` / ``/version`` and the
    ``GET /api/v3/device/name/{name}/{command}`` route end-to-end through the driver.

Runs with::

    python -m pytest tests/test_simple_example.py
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_EXAMPLE_DIR = os.path.abspath(os.path.join(_HERE, "..", "examples", "simple"))

from device_sdk_py.internal.provision import (  # noqa: E402
    load_devices,
    load_profiles,
    load_provision_watchers,
)
from device_sdk_py.internal.cache import (  # noqa: E402
    Devices,
    Profiles,
    ProvisionWatchers,
)
from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402


def _load_simple_example():
    """Import ``examples/simple/device_service.py`` as an isolated module."""
    spec = _ilu.spec_from_file_location(
        "simple_device_service", os.path.join(_EXAMPLE_DIR, "device_service.py"))
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIMPLE = _load_simple_example()
RES_ROOT = os.path.join(_EXAMPLE_DIR, "res")


class TestProvisionLoader(unittest.TestCase):
    """Unit tests for the pre-defined resource loader."""

    def test_load_profiles(self):
        profiles = load_profiles(os.path.join(RES_ROOT, "profiles"))
        self.assertEqual([p.name for p in profiles], ["fake-profile"])
        profile = profiles[0]
        self.assertEqual([r.name for r in profile.device_resources],
                         ["random-number", "switch"])
        # properties were snake-cased from the JSON valueType / readWrite keys
        random_resource = profile.device_resources[0]
        self.assertEqual(random_resource.properties.value_type, "Int32")
        self.assertEqual(random_resource.properties.read_write, "R")
        self.assertEqual(random_resource.properties.units, "count")
        self.assertEqual([c.name for c in profile.device_commands], ["Get", "Set"])
        op = profile.device_commands[0].resource_operations[0]
        self.assertEqual(op.device_resource, "random-number")

    def test_load_devices(self):
        devices = load_devices(os.path.join(RES_ROOT, "devices"))
        self.assertEqual([d.name for d in devices], ["fake"])
        device = devices[0]
        self.assertEqual(device.profile_name, "fake-profile")
        self.assertEqual(device.service_name, "device-simple")
        self.assertEqual(device.operating_state, "UP")
        self.assertEqual(device.protocols["simple"]["Address"], "localhost")

    def test_load_provision_watchers(self):
        watchers = load_provision_watchers(
            os.path.join(RES_ROOT, "provisionwatchers"))
        self.assertEqual([w.name for w in watchers], ["simple"])
        watcher = watchers[0]
        self.assertEqual(watcher.profile_name, "fake-profile")
        self.assertEqual(watcher.service_name, "device-simple")
        self.assertEqual(watcher.identifiers["Address"], "*")

    def test_yaml_profile_is_loaded(self):
        import tempfile
        import yaml as _yaml
        yaml_profile = {
            "name": "yaml-profile",
            "deviceResources": [
                {"name": "x",
                 "properties": {"valueType": "String", "readWrite": "R"}}
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "yaml-profile.yaml"), "w") as handle:
                _yaml.safe_dump(yaml_profile, handle)
            loaded = load_profiles(tmp)
        self.assertEqual([p.name for p in loaded], ["yaml-profile"])
        res = loaded[0].device_resources[0]
        self.assertEqual(res.name, "x")
        self.assertEqual(res.properties.value_type, "String")

    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(load_profiles("/no/such/directory/here"), [])
        self.assertEqual(load_devices("/no/such/directory/here"), [])
        self.assertEqual(load_provision_watchers("/no/such/directory/here"), [])


class TestInitializeResources(unittest.TestCase):
    """``DeviceService.initialize_resources`` populates the cache singletons."""

    def setUp(self):
        # The bootstrap calls initialize_resources itself, so the cache is already
        # populated after setUp runs.
        self.ds = bootstrap("device-simple", "0.0.0", SIMPLE.SimpleDriver(),
                            configuration=SIMPLE.Configuration(res_root=RES_ROOT))

    def test_devices_loaded_into_cache(self):
        self.assertTrue(Devices().device_exists("fake"))
        device = Devices().for_name("fake")[0]
        self.assertEqual(device.profile_name, "fake-profile")
        self.assertEqual(device.service_name, "device-simple")
        self.assertEqual(device.operating_state, "UP")

    def test_profiles_and_watchers_loaded_into_cache(self):
        self.assertTrue(Profiles().for_name("fake-profile")[1])
        self.assertTrue(ProvisionWatchers().for_name("simple")[1])

    def test_initialize_resources_is_idempotent(self):
        counts = self.ds.initialize_resources(res_root=RES_ROOT)
        self.assertEqual(counts, {"profiles": 0, "devices": 0, "watchers": 0})

    def test_initialize_resources_allow_list_filters(self):
        counts = self.ds.initialize_resources(
            res_root=RES_ROOT, device_names=["does-not-exist"])
        self.assertEqual(counts["devices"], 0)

    def test_initialize_resources_defaults_operating_state(self):
        # A device file with no OperatingState must default to UP.
        import json
        import tempfile
        device = {"name": "no-state",
                  "serviceName": "device-simple",
                  "profileName": "fake-profile"}
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "profiles"))
            os.makedirs(os.path.join(tmp, "devices"))
            with open(os.path.join(tmp, "profiles", "p.json"), "w") as handle:
                json.dump({"name": "fake-profile",
                           "deviceResources": [
                               {"name": "random-number",
                                "properties": {"valueType": "Int32", "readWrite": "R"}}
                           ]}, handle)
            with open(os.path.join(tmp, "devices", "d.json"), "w") as handle:
                json.dump(device, handle)
            ds = bootstrap("device-simple", "0.0.0", SIMPLE.SimpleDriver(),
                           configuration=SIMPLE.Configuration(res_root=tmp))
        fetched, ok = Devices().for_name("no-state")
        self.assertTrue(ok)
        self.assertEqual(fetched.operating_state, "UP")


class TestSimpleExampleHttp(unittest.TestCase):
    """End-to-end HTTP checks through FastAPI's TestClient (no uvicorn needed)."""

    def setUp(self):
        self.ds = SIMPLE.build_service()
        self.ds._init_http_controller()
        from starlette.testclient import TestClient
        self.client = TestClient(self.ds.controller.app())

    def test_ping(self):
        response = self.client.get("/api/v3/ping")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["apiVersion"], "v3")
        self.assertEqual(body["serviceName"], "device-simple")

    def test_version(self):
        response = self.client.get("/api/v3/version")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["apiVersion"], "v3")
        self.assertEqual(body["version"], "0.0.0")

    def test_command_read(self):
        response = self.client.get("/api/v3/device/name/fake/Get")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["apiVersion"], "v3")
        event = body["event"]
        self.assertEqual(event["deviceName"], "fake")
        self.assertEqual(event["sourceName"], "Get")
        reading = event["readings"][0]
        self.assertEqual(reading["resourceName"], "random-number")
        self.assertEqual(reading["valueType"], "Int32")
        self.assertTrue(0 <= int(reading["value"]) <= 100)

    def test_command_read_unknown_device(self):
        response = self.client.get("/api/v3/device/name/nope/Get")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
