# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for `service/device_service.py` runtime plumbing.

Covers the metadata write-back helpers (cache-first + rollback), the security
registration methods, config-derived getters, `_advertised_host`, message-bus config
parsing, auto-event wiring and the custom route / config APIs. Complements
`test_bootstrap.py` (startup / HTTP) and `test_secure_mode.py` (JWT / secret providers).
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
    ProvisionWatcher,
    Devices,
    Profiles,
    ProvisionWatchers,
)
from device_sdk_py.internal.cache.devices import create_device_cache  # noqa: E402
from device_sdk_py.internal.cache.profiles import create_profile_cache  # noqa: E402
from device_sdk_py.internal.cache.provisionwatchers import (  # noqa: E402
    create_provision_watcher_cache,
)
from device_sdk_py.internal.common.utils import EdgexError  # noqa: E402
from device_sdk_py.internal.metadata.client import MetadataError  # noqa: E402
from device_sdk_py.service.device_service import DeviceService  # noqa: E402


class _Driver:
    def start(self):
        pass

    def validate_device(self, device):
        pass


class _MockDeviceOpts:
    def __init__(self, **options):
        for key, value in options.items():
            setattr(self, key, value)


class _MockConfig:
    def __init__(self, **sections):
        for key, value in sections.items():
            setattr(self, key, value)


def _service(config=None, driver=None):
    return DeviceService("device-simple", "0.0.0", driver or _Driver(),
                         configuration=config)


def _device(name="dev1"):
    return Device(name=name, profile_name="p1")


class DeviceServiceTestCase(unittest.TestCase):
    def setUp(self):
        create_device_cache([])
        create_profile_cache([])
        create_provision_watcher_cache([])

    def tearDown(self):
        os.environ.pop("EDGEX_SECURE_MODE", None)
        os.environ.pop("EDGEX_SERVICE_ADDRESS", None)
        os.environ.pop("EDGEX_SERVICE_HOST", None)
        os.environ.pop("EDGEX_CORE_METADATA_HOST", None)
        os.environ.pop("EDGEX_CORE_METADATA_PORT", None)
        os.environ.pop("EDGEX_MESSAGEBUS_HOST", None)


class TestGetters(DeviceServiceTestCase):
    def test_name_version(self):
        ds = _service()
        self.assertEqual(ds.name(), "device-simple")
        self.assertEqual(ds.version(), "0.0.0")

    def test_async_readings_enabled(self):
        ds = _service(_MockConfig(
            device=_MockDeviceOpts(async_readings_enabled=True)))
        self.assertTrue(ds.async_readings_enabled())
        self.assertFalse(_service().async_readings_enabled())

    def test_device_discovery_enabled(self):
        ds = _service(_MockConfig(
            device=_MockDeviceOpts(discovery=_MockDeviceOpts(enabled=True))))
        self.assertTrue(ds.device_discovery_enabled())
        self.assertFalse(_service().device_discovery_enabled())
        self.assertFalse(_service(_MockConfig(device=_MockDeviceOpts())).device_discovery_enabled())

    def test_channels(self):
        ds = _service()
        self.assertIsNotNone(ds.async_values_channel())
        self.assertIsNotNone(ds.discovered_device_channel())

    def test_driver_configs(self):
        ds = _service(_MockConfig(driver={"rate": "1s"}))
        self.assertEqual(ds.driver_configs(), {"rate": "1s"})
        self.assertEqual(_service().driver_configs(), {})

    def test_logging_client(self):
        ds = _service()
        self.assertIsNotNone(ds.logging_client())
        self.assertIs(ds.logging_client(), ds.logging_client())

    def test_secret_provider(self):
        ds = _service()
        provider = ds.secret_provider()
        self.assertIsNotNone(provider)
        self.assertIs(provider, ds.secret_provider())

    def test_metrics_manager(self):
        ds = _service()
        self.assertIsNotNone(ds.metrics_manager())
        self.assertIs(ds.metrics_manager(), ds.metrics_manager())


class TestSecureModeConfig(DeviceServiceTestCase):
    def test_env_true(self):
        os.environ["EDGEX_SECURE_MODE"] = "true"
        self.assertTrue(_service()._read_secure_mode_config())

    def test_env_false(self):
        os.environ["EDGEX_SECURE_MODE"] = "false"
        self.assertFalse(_service()._read_secure_mode_config())

    def test_config_secure(self):
        ds = _service(_MockConfig(device=_MockDeviceOpts(secure_mode=True)))
        self.assertTrue(ds._read_secure_mode_config())

    def test_default_false(self):
        self.assertFalse(_service()._read_secure_mode_config())


class TestAdvertisedHost(DeviceServiceTestCase):
    def test_config_base_address(self):
        ds = _service(_MockConfig(
            service=_MockDeviceOpts(base_address="http://edgex-host:59986")))
        self.assertEqual(ds._advertised_host(), "http://edgex-host:59986")

    def test_env_host(self):
        os.environ["EDGEX_SERVICE_HOST"] = "10.0.0.5"
        self.assertEqual(_service()._advertised_host(), "10.0.0.5")

    def test_bind_host_concrete(self):
        ds = _service(_MockConfig(service=_MockDeviceOpts(host="192.168.1.10")))
        self.assertEqual(ds._advertised_host(), "192.168.1.10")

    def test_bind_host_any_wildcard_skipped(self):
        ds = _service(_MockConfig(service=_MockDeviceOpts(host="0.0.0.0",
                                                          auto_detect_host=False)))
        self.assertEqual(ds._advertised_host(), "localhost")

    def test_auto_detect_fallback(self):
        ds = _service(_MockConfig(service=_MockDeviceOpts(host="0.0.0.0")))
        with mock.patch("socket.socket") as sock:
            sock.return_value.connect.side_effect = OSError("no network")
            self.assertEqual(ds._advertised_host(), "localhost")


class TestMetadataBaseUrl(DeviceServiceTestCase):
    def test_from_clients_dict(self):
        ds = _service(_MockConfig(
            clients={"core-metadata": {"host": "md", "port": "59881"}}))
        self.assertEqual(ds._metadata_base_url(), "http://md:59881")

    def test_base_url_direct(self):
        ds = _service(_MockConfig(
            clients={"core-metadata": {"base_url": "http://md:59881"}}))
        self.assertEqual(ds._metadata_base_url(), "http://md:59881")

    def test_from_env(self):
        os.environ["EDGEX_CORE_METADATA_HOST"] = "md"
        os.environ["EDGEX_CORE_METADATA_PORT"] = "59881"
        self.assertEqual(_service()._metadata_base_url(), "http://md:59881")

    def test_none(self):
        self.assertIsNone(_service()._metadata_base_url())

    def test_metadata_client_none_when_unconfigured(self):
        self.assertIsNone(_service()._metadata_client())

    def test_metadata_client_lazy(self):
        ds = _service(_MockConfig(clients={"core-metadata": {"host": "md", "port": "1"}}))
        client = ds._metadata_client()
        self.assertIsNotNone(client)
        self.assertIs(client, ds._metadata_client())


class TestMessageBusConfig(DeviceServiceTestCase):
    def test_defaults(self):
        cfg = _service()._message_bus_config()
        self.assertEqual(cfg.broker_info.host, "127.0.0.1")
        self.assertEqual(cfg.broker_info.port, 1883)
        self.assertEqual(cfg.base_topic_prefix, "edgex")
        self.assertEqual(cfg.publish_topic_prefix, "events")

    def test_from_dict_config(self):
        ds = _service(_MockConfig(message_bus={
            "host": "mb", "port": "2883", "type": "mqtt",
            "base_topic_prefix": "edgex2", "optional": {"Username": "u"}}))
        cfg = ds._message_bus_config()
        self.assertEqual(cfg.broker_info.host, "mb")
        self.assertEqual(cfg.broker_info.port, 2883)
        self.assertEqual(cfg.message_bus_type, "mqtt")
        self.assertEqual(cfg.optional["Username"], "u")


class TestValidateDevice(DeviceServiceTestCase):
    def test_bypass_skips(self):
        driver = mock.Mock()
        driver.validate_device = mock.Mock(side_effect=AssertionError("no"))
        ds = _service(driver=driver)
        ds._validate_device(_device(), bypass_validation=True)
        driver.validate_device.assert_not_called()

    def test_no_driver_method(self):
        ds = _service(driver=_Driver())
        ds._validate_device(_device(), bypass_validation=False)

    def test_failure_raises_edgx_error(self):
        driver = mock.Mock()
        driver.validate_device = mock.Mock(side_effect=ValueError("bad"))
        ds = _service(driver=driver)
        with self.assertRaises(EdgexError):
            ds._validate_device(_device(), bypass_validation=False)


class TestMetadataWriteBack(DeviceServiceTestCase):
    def setUp(self):
        super().setUp()
        self.ds = _service()

    def test_add_device_success(self):
        Devices().add = mock.Mock()
        client = mock.Mock()
        client.add_device.return_value = "new-id"
        device = _device()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            result = self.ds._add_device_to_metadata(device, bypass_validation=True)
        self.assertEqual(result, "new-id")
        self.assertEqual(device.id, "new-id")
        self.assertEqual(device.service_name, "device-simple")

    def test_add_device_no_metadata_client(self):
        device = _device()
        with mock.patch.object(self.ds, "_metadata_client", return_value=None):
            result = self.ds._add_device_to_metadata(device, bypass_validation=True)
        self.assertTrue(result)

    def test_add_device_rolls_back_on_error(self):
        client = mock.Mock()
        client.add_device.side_effect = MetadataError("boom")
        device = _device()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            with self.assertRaises(EdgexError):
                self.ds._add_device_to_metadata(device, bypass_validation=True)
        self.assertFalse(Devices().for_name("dev1")[1])

    def test_patch_device_updates_cache(self):
        device = _device()
        Devices().add(device)
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._patch_device_in_metadata("dev1", {"description": "new"},
                                              bypass_validation=True)
        client.patch_device.assert_called_once()
        self.assertEqual(Devices().for_name("dev1")[0].description, "new")

    def test_patch_device_missing_noop(self):
        with mock.patch.object(self.ds, "_metadata_client") as client:
            self.ds._patch_device_in_metadata("ghost", {}, bypass_validation=True)
            client.assert_not_called()

    def test_patch_device_rolls_back(self):
        device = _device()
        device.description = "old"
        Devices().add(device)
        client = mock.Mock()
        client.patch_device.side_effect = MetadataError("boom")
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            with self.assertRaises(EdgexError):
                self.ds._patch_device_in_metadata("dev1", {"description": "new"},
                                                  bypass_validation=True)
        self.assertEqual(Devices().for_name("dev1")[0].description, "old")

    def test_delete_device_removes_from_cache(self):
        Devices().add(_device())
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._delete_device_from_metadata("dev1")
        self.assertFalse(Devices().for_name("dev1")[1])

    def test_delete_device_restores_on_error(self):
        Devices().add(_device())
        client = mock.Mock()
        client.delete_device.side_effect = MetadataError("boom")
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            with self.assertRaises(EdgexError):
                self.ds._delete_device_from_metadata("dev1")
        self.assertTrue(Devices().for_name("dev1")[1])

    def test_add_profile_success(self):
        client = mock.Mock()
        client.add_device_profile.return_value = "pid"
        profile = DeviceProfile(name="p1")
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            result = self.ds._add_profile_to_metadata(profile)
        self.assertEqual(result, "pid")
        self.assertEqual(profile.id, "pid")

    def test_add_profile_rolls_back(self):
        client = mock.Mock()
        client.add_device_profile.side_effect = MetadataError("boom")
        profile = DeviceProfile(name="p1")
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            with self.assertRaises(EdgexError):
                self.ds._add_profile_to_metadata(profile)
        self.assertFalse(Profiles().for_name("p1")[1])

    def test_update_profile_rolls_back(self):
        profile = DeviceProfile(name="p1", description="old")
        Profiles().add(profile)
        updated = Profiles().for_name("p1")[0]
        updated.description = "new"
        client = mock.Mock()
        client.update_device_profile.side_effect = MetadataError("boom")
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            with self.assertRaises(EdgexError):
                self.ds._update_profile_in_metadata(updated)
        self.assertEqual(Profiles().for_name("p1")[0].description, "old")

    def test_delete_profile(self):
        Profiles().add(DeviceProfile(name="p1"))
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._delete_profile_from_metadata("p1")
        client.delete_device_profile.assert_called_once_with("p1")

    def test_add_watcher_sets_service_name(self):
        client = mock.Mock()
        client.add_provision_watcher.return_value = "wid"
        watcher = ProvisionWatcher(name="w1", profile_name="p1")
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._add_provision_watcher_to_metadata(watcher)
        self.assertEqual(watcher.service_name, "device-simple")
        self.assertEqual(watcher.id, "wid")

    def test_update_watcher_rolls_back(self):
        watcher = ProvisionWatcher(name="w1", profile_name="p1", description="old")
        ProvisionWatchers().add(watcher)
        updated = ProvisionWatchers().for_name("w1")[0]
        updated.description = "new"
        client = mock.Mock()
        client.update_provision_watcher.side_effect = MetadataError("boom")
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            with self.assertRaises(EdgexError):
                self.ds._update_provision_watcher_in_metadata(updated)
        self.assertEqual(ProvisionWatchers().for_name("w1")[0].description, "old")

    def test_delete_watcher(self):
        ProvisionWatchers().add(ProvisionWatcher(name="w1", profile_name="p1"))
        client = mock.Mock()
        with mock.patch.object(self.ds, "_metadata_client", return_value=client):
            self.ds._delete_provision_watcher_from_metadata("w1")
        client.delete_provision_watcher.assert_called_once_with("w1")


class TestSecurityRegistration(DeviceServiceTestCase):
    def test_noop_when_insecure(self):
        ds = _service()
        with mock.patch.object(ds, "_register_secretstore_tokens") as tokens:
            with mock.patch.object(ds, "_register_api_gateway_route") as route:
                with mock.patch.object(ds, "_register_known_secrets") as secrets:
                    ds._register_with_security_services()
                    tokens.assert_not_called()
                    route.assert_not_called()
                    secrets.assert_not_called()

    def test_registers_when_secure(self):
        os.environ["EDGEX_SECURE_MODE"] = "true"
        ds = _service()
        with mock.patch.object(ds, "_register_secretstore_tokens") as tokens:
            with mock.patch.object(ds, "_register_api_gateway_route") as route:
                with mock.patch.object(ds, "_register_known_secrets") as secrets:
                    ds._register_with_security_services()
                    tokens.assert_called_once()
                    route.assert_called_once()
                    secrets.assert_called_once()

    def test_register_methods_log(self):
        os.environ["EDGEX_SECURE_MODE"] = "true"
        ds = _service(_MockConfig(
            service=_MockDeviceOpts(host="192.168.1.5", port=59986)))
        ds._logger = mock.Mock()
        ds._register_secretstore_tokens()
        ds._register_api_gateway_route()
        ds._register_known_secrets()
        self.assertGreaterEqual(ds._logger.info.call_count, 3)


class TestAutoEvents(DeviceServiceTestCase):
    def test_start_with_manager(self):
        ds = _service()
        manager = mock.Mock()
        with mock.patch.object(ds, "_auto_event_manager", return_value=manager):
            ds._start_auto_events()
            manager.start_auto_events.assert_called_once()

    def test_start_without_manager(self):
        ds = _service()
        ds._logger = mock.Mock()
        with mock.patch.object(ds, "_auto_event_manager", return_value=None):
            ds._start_auto_events()
            ds._logger.debug.assert_called()

    def test_restart_for_device(self):
        ds = _service()
        manager = mock.Mock()
        with mock.patch.object(ds, "_auto_event_manager", return_value=manager):
            ds._restart_auto_events("dev1")
            manager.restart_for_device.assert_called_once_with("dev1")

    def test_auto_event_manager_lazy_import(self):
        ds = _service()
        with mock.patch("importlib.import_module") as imp:
            imp.return_value = mock.Mock(AutoEventManager=mock.Mock(return_value="mgr"))
            self.assertEqual(ds._auto_event_manager(), "mgr")
            self.assertIs(ds._auto_event_manager(), "mgr")

    def test_auto_event_manager_import_error(self):
        ds = _service()
        ds._logger = mock.Mock()
        with mock.patch("importlib.import_module", side_effect=ImportError("no module")):
            self.assertIsNone(ds._auto_event_manager())


class TestCustomRouteAndConfig(DeviceServiceTestCase):
    def test_add_custom_route_queued_before_controller(self):
        ds = _service()
        handler = lambda: None
        ds.add_custom_route("/custom", False, handler)
        self.assertEqual(len(ds._pending_custom_routes), 1)

    def test_add_custom_route_with_controller(self):
        ds = _service()
        ds.controller = mock.Mock()
        handler = lambda: None
        ds.add_custom_route("/custom", False, handler)
        ds.controller.add_route.assert_called_once()

    def test_load_custom_config(self):
        ds = _service()
        ds.controller = mock.Mock()
        ds.load_custom_config({"a": 1}, "section")
        self.assertTrue(ds._custom_config_loaded)
        ds.controller.set_custom_config_info.assert_called_once()

    def test_listen_for_custom_config_changes_requires_load(self):
        ds = _service()
        with self.assertRaises(RuntimeError):
            ds.listen_for_custom_config_changes(mock.Mock(), "s", lambda c: None)

    def test_listen_requires_path_attr(self):
        ds = _service()
        ds.load_custom_config({}, "s")
        with self.assertRaises(AttributeError):
            ds.listen_for_custom_config_changes(object(), "s", lambda c: None)

    def test_listen_missing_file(self):
        ds = _service()
        ds.load_custom_config({}, "s")
        watcher = mock.Mock(custom_config_path="/nonexistent/nope.yaml")
        with self.assertRaises(FileNotFoundError):
            ds.listen_for_custom_config_changes(watcher, "s", lambda c: None)


class TestSystemEvents(DeviceServiceTestCase):
    def test_publish_generic_without_client_logged(self):
        ds = _service()
        ds._logger = mock.Mock()
        ds.publish_generic_system_event("discovery", "start", {})
        ds._logger.debug.assert_called()

    def test_publish_generic_with_client(self):
        ds = _service()
        ds._messaging_client = mock.Mock()
        ds._message_bus_config_obj = mock.Mock(base_topic_prefix="edgex")
        with mock.patch("device_sdk_py.service.device_service.publish_system_event") as pub:
            ds.publish_generic_system_event("discovery", "start", {})
            pub.assert_called_once()


class TestHttpHostPort(DeviceServiceTestCase):
    def test_defaults(self):
        ds = _service()
        self.assertEqual(ds._http_host_port(), ("0.0.0.0", 59986))

    def test_from_config(self):
        ds = _service(_MockConfig(service=_MockDeviceOpts(host="127.0.0.1", port="8080")))
        self.assertEqual(ds._http_host_port(), ("127.0.0.1", 8080))


if __name__ == "__main__":
    unittest.main()
