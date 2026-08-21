# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Core Keeper registry client and DeviceService registry wiring."""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device_sdk_py.internal.registry import CoreKeeperRegistryClient, RegistryError  # noqa: E402

import requests  # noqa: E402


def _make_client(**overrides):
    kwargs = dict(
        host="edgex-core-keeper",
        port=59890,
        service_id="device-simple",
        service_host="edgex-device-simple",
        service_port=59990,
        check_interval="10s",
        logger=logging.getLogger("test.registry"),
    )
    kwargs.update(overrides)
    return CoreKeeperRegistryClient(**kwargs)


class TestRegistryClient(unittest.TestCase):
    """Wire-level tests for the Core Keeper registry REST client."""

    def test_register_posts_expected_payload(self):
        client = _make_client()
        response = MagicMock(status_code=201)
        with patch.object(requests, "post", return_value=response) as mpost:
            self.assertTrue(client.register())
        args, kwargs = mpost.call_args
        self.assertEqual(args[0], "http://edgex-core-keeper:59890/api/v3/registry")
        payload = kwargs["json"]
        self.assertEqual(payload["apiVersion"], "v3")
        reg = payload["registration"]
        self.assertEqual(reg["serviceId"], "device-simple")
        self.assertEqual(reg["host"], "edgex-device-simple")
        self.assertEqual(reg["port"], 59990)
        self.assertEqual(reg["healthCheck"]["interval"], "10s")
        self.assertEqual(reg["healthCheck"]["path"], "/api/v3/ping")
        self.assertEqual(reg["healthCheck"]["type"], "http")

    def test_register_raises_on_http_error(self):
        response = MagicMock(status_code=400, text="bad request")
        with patch.object(requests, "post", return_value=response):
            with self.assertRaises(RegistryError):
                _make_client().register()

    def test_register_raises_on_network_error(self):
        with patch.object(requests, "post", side_effect=requests.ConnectionError("boom")):
            with self.assertRaises(RegistryError):
                _make_client().register()

    def test_is_alive_true_on_200(self):
        response = MagicMock(status_code=200)
        with patch.object(requests, "get", return_value=response):
            self.assertTrue(_make_client().is_alive())

    def test_is_alive_false_on_network_error(self):
        with patch.object(requests, "get", side_effect=requests.ConnectionError("boom")):
            self.assertFalse(_make_client().is_alive())

    def test_deregister_deletes_service_id_route(self):
        response = MagicMock(status_code=204)
        with patch.object(requests, "delete", return_value=response) as mdelete:
            self.assertTrue(_make_client().deregister())
        args, _ = mdelete.call_args
        self.assertEqual(args[0], "http://edgex-core-keeper:59890/api/v3/registry/serviceId/device-simple")

    def test_deregister_best_effort(self):
        with patch.object(requests, "delete", side_effect=requests.ConnectionError("boom")):
            self.assertFalse(_make_client().deregister())

    def test_register_with_retry_success_first_attempt(self):
        client = _make_client(check_interval="1s")
        with patch.object(CoreKeeperRegistryClient, "is_alive", return_value=True), \
             patch.object(CoreKeeperRegistryClient, "register", return_value=True) as mregister:
            self.assertTrue(client.register_with_retry(startup_timeout=5.0))
        mregister.assert_called_once()

    def test_register_with_retry_deadline_elapses(self):
        client = _make_client(check_interval="1s")
        with patch.object(CoreKeeperRegistryClient, "is_alive", return_value=False):
            self.assertFalse(client.register_with_retry(startup_timeout=0.3))

    def test_register_with_retry_recovers_after_failure(self):
        client = _make_client(check_interval="1s")
        outcomes = iter([False, True])  # registry down, then up
        with patch.object(CoreKeeperRegistryClient, "is_alive", side_effect=lambda: next(outcomes)), \
             patch.object(CoreKeeperRegistryClient, "register", return_value=True) as mregister:
            self.assertTrue(client.register_with_retry(startup_timeout=10.0))
        mregister.assert_called_once()


class TestDurationParsing(unittest.TestCase):
    def test_parse_variants(self):
        from device_sdk_py.internal.registry.client import _parse_duration
        self.assertEqual(_parse_duration("10s", 30.0), 10.0)
        self.assertEqual(_parse_duration("1m", 30.0), 60.0)
        self.assertEqual(_parse_duration("", 30.0), 30.0)
        self.assertEqual(_parse_duration("garbage", 30.0), 30.0)


class TestDeviceServiceRegistryWiring(unittest.TestCase):
    """DeviceService._register_with_registry / _deregister_from_registry wiring."""

    def _make_service(self, registry):
        from device_sdk_py.service.device_service import DeviceService

        service = DeviceService.__new__(DeviceService)
        service.service_key = "device-simple"
        service._logger = logging.getLogger("test.service")
        service._shutdown_event = __import__("threading").Event()
        service._registry_client = None
        configuration = MagicMock()
        if registry is None:
            del configuration.Registry
        else:
            configuration.Registry = registry
        configuration.Service.HealthCheckInterval = "10s"
        service.configuration = configuration
        return service

    def test_skips_when_registry_not_configured(self):
        service = self._make_service(None)
        with patch.object(CoreKeeperRegistryClient, "register_with_retry") as mretry:
            service._register_with_registry()
        mretry.assert_not_called()
        self.assertIsNone(service._registry_client)

    def test_skips_when_registry_incomplete(self):
        from device_sdk_py.internal.common.configuration import RegistryInfo
        service = self._make_service(RegistryInfo(Host="", Port=0, Type=""))
        with patch.object(CoreKeeperRegistryClient, "register_with_retry") as mretry:
            service._register_with_registry()
        mretry.assert_not_called()

    def test_registers_and_deregisters(self):
        from device_sdk_py.internal.common.configuration import RegistryInfo
        service = self._make_service(RegistryInfo(Host="edgex-core-keeper", Port=59890, Type="core-keeper"))
        with patch.object(type(service), "_advertised_host", return_value="edgex-device-simple"), \
             patch.object(type(service), "_http_host_port", return_value=("0.0.0.0", 59990)), \
             patch.object(CoreKeeperRegistryClient, "register_with_retry", return_value=True) as mretry:
            service._register_with_registry()
        mretry.assert_called_once()
        self.assertIsNotNone(service._registry_client)
        with patch.object(service._registry_client, "deregister", return_value=True) as mdereg:
            service._deregister_from_registry()
        mdereg.assert_called_once()
        self.assertIsNone(service._registry_client)


if __name__ == "__main__":
    unittest.main()
