# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Extended unit tests for the internal common utils, the secret clients and the
CommandRequest model.

Complements `test_command_application_extended.py` and `test_zero_dep_clients.py`
by covering the branches those files leave uncovered: the timestamp helpers and
the OperatingState / Event / Reading tag helpers in `internal/common/utils.py`, the
SecretProvider base class, the remaining InMemorySecretProvider edge cases, the
OpenBao HTTP calls (including error paths, token renewal and the secret store
registration helpers) and the `CommandRequest` model defaults and aliases.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.common.utils import (  # noqa: E402
    EdgexError,
    EdgexErrorKind,
    KIND_CONTRACT_INVALID,
    KIND_SERVER_ERROR,
    add_event_tags,
    add_reading_tags,
    create_edgx_error,
    current_time_millis,
    make_timestamp,
    make_uid,
    update_operating_state,
)
from device_sdk_py.internal.clients.secret import (  # noqa: E402
    InMemorySecretProvider,
    OpenBaoSecretProvider,
    SecretProvider,
    create_secret_provider,
)
from device_sdk_py.models import VALUETYPE_STRING  # noqa: E402
from device_sdk_py.models.command_request import CommandRequest  # noqa: E402


class TestUtilsSecEdgexError(unittest.TestCase):
    """Edge cases of the `EdgexError` hierarchy and its status mapping."""

    def test_kind_to_status_mapping(self):
        for kind in EdgexErrorKind:
            err = EdgexError(kind=kind, message="m")
            self.assertIsInstance(err.code, int)
        self.assertEqual(EdgexError(kind=KIND_CONTRACT_INVALID, message="x").code, 400)
        self.assertEqual(EdgexError(kind=KIND_SERVER_ERROR, message="x").code, 500)

    def test_unknown_kind_defaults_to_500(self):
        err = EdgexError(kind="BogusKind", message="x")
        self.assertEqual(err.code, 500)

    def test_error_attributes_and_debug_messages(self):
        err = create_edgx_error(KIND_SERVER_ERROR, "boom")
        self.assertIsInstance(err, EdgexError)
        self.assertEqual(err.kind, KIND_SERVER_ERROR)
        self.assertEqual(err.message, "boom")
        self.assertEqual(str(err), "boom")
        self.assertEqual(err.debug_messages(), "boom")

    def test_create_edgx_error_returns_error(self):
        err = create_edgx_error(KIND_CONTRACT_INVALID, "bad")
        self.assertEqual(err.kind, KIND_CONTRACT_INVALID)
        self.assertEqual(err.code, 400)


class TestUtilsSecTimeHelpers(unittest.TestCase):
    """Timestamp and unique-id helpers."""

    def test_make_timestamp(self):
        before = time.time_ns()
        ts = make_timestamp()
        after = time.time_ns()
        self.assertIsInstance(ts, int)
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)

    def test_current_time_millis(self):
        before = time.time_ns() // 1_000_000
        ms = current_time_millis()
        after = time.time_ns() // 1_000_000
        self.assertIsInstance(ms, int)
        self.assertGreaterEqual(ms, before)
        self.assertLessEqual(ms, after)

    def test_make_uid_unique(self):
        ids = [make_uid() for _ in range(100)]
        self.assertEqual(len(set(ids)), 100)
        self.assertTrue(all(isinstance(i, str) and i for i in ids))


class TestUtilsSecOperatingState(unittest.TestCase):
    """`update_operating_state` success, warning and exception branches."""

    def test_no_client_warns_and_returns(self):
        logger = mock.Mock()
        update_operating_state("dev", "UP", logger)
        logger.warning.assert_called_once()

    def test_success_delegates_to_client(self):
        logger = mock.Mock()
        client = mock.Mock()
        update_operating_state("dev", "UP", logger, client)
        client.update_operating_state.assert_called_once_with("dev", "UP")
        logger.exception.assert_not_called()

    def test_client_raises_logs_exception(self):
        logger = mock.Mock()
        client = mock.Mock()
        client.update_operating_state.side_effect = RuntimeError("boom")
        update_operating_state("dev", "UP", logger, client)
        logger.exception.assert_called_once()


class TestUtilsSecEventTags(unittest.TestCase):
    """`add_event_tags` merging of DeviceCommand and Device tags."""

    @staticmethod
    def _patch():
        return (
            mock.patch("device_sdk_py.internal.common.utils.Profiles"),
            mock.patch("device_sdk_py.internal.common.utils.Devices"),
        )

    def test_nothing_to_merge(self):
        with self._patch()[0] as mock_profiles, self._patch()[1] as mock_devices:
            mock_profiles.return_value.device_command.return_value = (mock.Mock(), False)
            mock_devices.return_value.for_name.return_value = (mock.Mock(), False)
            event = mock.Mock()
            event.tags = None
            add_event_tags(event)
            self.assertEqual(event.tags, {})

    def test_cmd_tags_merged(self):
        with self._patch()[0] as mock_profiles, self._patch()[1] as mock_devices:
            cmd = mock.Mock()
            cmd.tags = {"cmd": "v"}
            mock_profiles.return_value.device_command.return_value = (cmd, True)
            mock_devices.return_value.for_name.return_value = (mock.Mock(), False)
            event = mock.Mock()
            event.tags = None
            add_event_tags(event)
            self.assertEqual(event.tags, {"cmd": "v"})

    def test_empty_cmd_tags_skipped(self):
        with self._patch()[0] as mock_profiles, self._patch()[1] as mock_devices:
            cmd = mock.Mock()
            cmd.tags = {}
            mock_profiles.return_value.device_command.return_value = (cmd, True)
            device = mock.Mock()
            device.tags = {}
            mock_devices.return_value.for_name.return_value = (device, True)
            event = mock.Mock()
            event.tags = {}
            add_event_tags(event)
            self.assertEqual(event.tags, {})

    def test_device_tags_merged(self):
        with self._patch()[0] as mock_profiles, self._patch()[1] as mock_devices:
            cmd = mock.Mock()
            cmd.tags = {}
            mock_profiles.return_value.device_command.return_value = (cmd, True)
            device = mock.Mock()
            device.tags = {"dev": "v"}
            mock_devices.return_value.for_name.return_value = (device, True)
            event = mock.Mock()
            event.tags = {"existing": "x"}
            add_event_tags(event)
            self.assertEqual(event.tags, {"existing": "x", "dev": "v"})

    def test_device_tags_override_cmd_tags(self):
        with self._patch()[0] as mock_profiles, self._patch()[1] as mock_devices:
            cmd = mock.Mock()
            cmd.tags = {"shared": "cmd", "only-cmd": "1"}
            mock_profiles.return_value.device_command.return_value = (cmd, True)
            device = mock.Mock()
            device.tags = {"shared": "dev"}
            mock_devices.return_value.for_name.return_value = (device, True)
            event = mock.Mock()
            event.tags = None
            add_event_tags(event)
            self.assertEqual(event.tags, {"shared": "dev", "only-cmd": "1"})


class TestUtilsSecReadingTags(unittest.TestCase):
    """`add_reading_tags` resource tag merging branches."""

    @mock.patch("device_sdk_py.internal.common.utils.Profiles")
    def test_resource_not_found_noop(self, mock_profiles):
        mock_profiles.return_value.device_resource.return_value = (mock.Mock(), False)
        reading = mock.Mock()
        reading.tags = None
        add_reading_tags(reading)
        self.assertIsNone(reading.tags)

    @mock.patch("device_sdk_py.internal.common.utils.Profiles")
    def test_empty_tag_noop(self, mock_profiles):
        resource = mock.Mock()
        resource.tag = ""
        mock_profiles.return_value.device_resource.return_value = (resource, True)
        reading = mock.Mock()
        reading.tags = {}
        add_reading_tags(reading)
        self.assertEqual(reading.tags, {})

    @mock.patch("device_sdk_py.internal.common.utils.Profiles")
    def test_tag_initializes_tags_map(self, mock_profiles):
        resource = mock.Mock()
        resource.tag = "t1"
        mock_profiles.return_value.device_resource.return_value = (resource, True)
        reading = mock.Mock()
        reading.tags = None
        add_reading_tags(reading)
        self.assertEqual(reading.tags, {"tag": "t1"})

    @mock.patch("device_sdk_py.internal.common.utils.Profiles")
    def test_tag_merged_into_existing_tags(self, mock_profiles):
        resource = mock.Mock()
        resource.tag = "t2"
        mock_profiles.return_value.device_resource.return_value = (resource, True)
        reading = mock.Mock()
        reading.tags = {"existing": 1}
        add_reading_tags(reading)
        self.assertEqual(reading.tags, {"existing": 1, "tag": "t2"})


class TestUtilsSecSecretProviderABC(unittest.TestCase):
    """The base SecretProvider interface methods raise NotImplementedError."""

    def test_all_interface_methods_raise(self):
        provider = SecretProvider()
        with self.assertRaises(NotImplementedError):
            provider.store_secret("p", "k", "v")
        with self.assertRaises(NotImplementedError):
            provider.get_secret("p", "k")
        with self.assertRaises(NotImplementedError):
            provider.get_all_secrets("p")
        with self.assertRaises(NotImplementedError):
            provider.delete_secret("p", "k")


class TestUtilsSecInMemoryProvider(unittest.TestCase):
    """Remaining InMemorySecretProvider edge cases."""

    def setUp(self):
        self.provider = InMemorySecretProvider()

    def test_store_overwrites_existing_key(self):
        self.provider.store_secret("p", "k", "v1")
        self.provider.store_secret("p", "k", "v2")
        self.assertEqual(self.provider.get_secret("p", "k"), "v2")
        self.assertEqual(self.provider.get_all_secrets("p"), {"k": "v2"})

    def test_get_all_secrets_returns_copy(self):
        self.provider.store_secret("p", "k", "v")
        result = self.provider.get_all_secrets("p")
        result["k"] = "changed"
        self.assertEqual(self.provider.get_secret("p", "k"), "v")

    def test_delete_secret_missing_key_raises(self):
        self.provider.store_secret("p", "k", "v")
        with self.assertRaises(KeyError):
            self.provider.delete_secret("p", "other")
        with self.assertRaises(KeyError):
            self.provider.delete_secret("missing-path", "k")

    def test_delete_last_secret_removes_path(self):
        self.provider.store_secret("p", "k", "v")
        self.provider.delete_secret("p", "k")
        self.assertEqual(self.provider.list_paths(), [])
        with self.assertRaises(KeyError):
            self.provider.get_secret("p", "k")

    def test_has_secret(self):
        self.provider.store_secret("p", "k", "v")
        self.assertTrue(self.provider.has_secret("p", "k"))
        self.assertFalse(self.provider.has_secret("p", "nope"))
        self.assertFalse(self.provider.has_secret("nope", "k"))

    def test_delete_path(self):
        self.provider.store_secret("p", "k", "v")
        self.provider.store_secret("p2", "k", "v")
        self.provider.delete_path("p")
        self.assertEqual(self.provider.list_paths(), ["p2"])
        self.provider.delete_path("p")
        self.assertEqual(self.provider.list_paths(), ["p2"])


class TestUtilsSecOpenBaoHttp(unittest.TestCase):
    """OpenBao HTTP request paths with mocked sessions."""

    @staticmethod
    def _provider(**kwargs):
        opts = {"base_url": "http://openbao:8200/v1", "token": "t"}
        opts.update(kwargs)
        provider = OpenBaoSecretProvider(**opts)
        provider._start_token_renewal = mock.Mock()
        provider._session = mock.Mock()
        return provider

    def test_request_success_and_url(self):
        provider = self._provider()
        resp = mock.Mock(status_code=200, text="")
        provider._session.request.return_value = resp
        out = provider._request("get", "/secret/data/x")
        self.assertIs(out, resp)
        args, kwargs = provider._session.request.call_args
        self.assertEqual(args[0], "get")
        self.assertEqual(args[1], "http://openbao:8200/v1/secret/data/x")
        self.assertEqual(kwargs["headers"]["X-Vault-Token"], "t")
        provider.close()

    def test_request_http_error_raises(self):
        provider = self._provider()
        provider._session.request.return_value = mock.Mock(status_code=500, text="boom")
        with self.assertRaises(RuntimeError):
            provider._request("get", "x")
        provider.close()

    def test_get_secret_found(self):
        provider = self._provider()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"data": {"data": {"user": "admin"}}}
        provider._session.request.return_value = resp
        self.assertEqual(provider.get_secret("db", "user"), "admin")
        self.assertIn("secret/data/db", provider._session.request.call_args[0][1])
        provider.close()

    def test_get_secret_missing_key_raises(self):
        provider = self._provider()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"data": {"data": {}}}
        provider._session.request.return_value = resp
        with self.assertRaises(KeyError):
            provider.get_secret("db", "nope")
        provider.close()

    def test_get_all_secrets(self):
        provider = self._provider()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"data": {"data": {"a": "1"}}}
        provider._session.request.return_value = resp
        self.assertEqual(provider.get_all_secrets("db"), {"a": "1"})
        provider.close()

    def test_delete_secret_present_reads_and_writes_back(self):
        provider = self._provider()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"data": {"data": {"a": "1", "b": "2"}}}
        provider._session.request.return_value = resp
        provider.delete_secret("db", "a")
        calls = provider._session.request.call_args_list
        self.assertEqual(len(calls), 2)
        post_args, post_kwargs = calls[1]
        self.assertEqual(post_args[0], "post")
        self.assertEqual(post_kwargs["json"], {"data": {"b": "2"}})
        provider.close()

    def test_delete_secret_missing_key_raises(self):
        provider = self._provider()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"data": {"data": {"a": "1"}}}
        provider._session.request.return_value = resp
        with self.assertRaises(KeyError):
            provider.delete_secret("db", "zzz")
        provider.close()

    def test_has_secret_true(self):
        provider = self._provider()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"data": {"data": {"k": "v"}}}
        provider._session.request.return_value = resp
        self.assertTrue(provider.has_secret("db", "k"))
        provider.close()

    def test_has_secret_false(self):
        provider = self._provider()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"data": {"data": {}}}
        provider._session.request.return_value = resp
        self.assertFalse(provider.has_secret("db", "k"))
        provider.close()

    def test_list_paths(self):
        provider = self._provider()
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"data": {"keys": ["db", "mqtt"]}}
        provider._session.request.return_value = resp
        self.assertEqual(provider.list_paths(), ["db", "mqtt"])
        provider.close()

    def test_delete_path(self):
        provider = self._provider()
        provider._session.request.return_value = mock.Mock(status_code=200, text="")
        provider.delete_path("db")
        args = provider._session.request.call_args[0]
        self.assertEqual(args[0], "delete")
        self.assertIn("secret/metadata/db", args[1])
        provider.close()


class TestUtilsSecOpenBaoRenewal(unittest.TestCase):
    """Token renewal thread lifecycle and renewal loop branches."""

    def test_start_renewal_early_return_when_alive(self):
        provider = OpenBaoSecretProvider(base_url="http://openbao:8200/v1", token="t")
        thread = mock.Mock()
        thread.is_alive.return_value = True
        provider._renewal_thread = thread
        provider._start_token_renewal()
        thread.start.assert_not_called()
        provider.close()

    def test_stop_renewal_joins_alive_thread(self):
        provider = OpenBaoSecretProvider(base_url="http://openbao:8200/v1", token="t")
        thread = mock.Mock()
        thread.is_alive.return_value = True
        provider._renewal_thread = thread
        provider._stop_token_renewal()
        thread.join.assert_called_once_with(timeout=5.0)
        provider.close()

    def test_renewal_loop_renews_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("fresh-token\n")
            token_file = f.name
        provider = OpenBaoSecretProvider(
            base_url="http://openbao:8200/v1", token="initial",
            token_file=token_file, token_ttl=60,
            token_renewal_threshold=0.8)
        provider._token_expiry = time.time() - 10
        thread = threading.Thread(target=provider._renewal_loop, daemon=True)
        thread.start()
        deadline = time.time() + 2
        while time.time() < deadline and provider._token == "initial":
            time.sleep(0.01)
        provider._stop_renewal.set()
        thread.join(timeout=5)
        os.unlink(token_file)
        self.assertEqual(provider._token, "fresh-token")
        self.assertGreater(provider._token_expiry, time.time())

    def test_renewal_loop_failure_logs_warning(self):
        provider = OpenBaoSecretProvider(
            base_url="http://openbao:8200/v1", token="initial",
            token_file="/nonexistent/token/file", token_ttl=60)
        provider._token_expiry = time.time() - 10
        provider._logger = mock.Mock()
        thread = threading.Thread(target=provider._renewal_loop, daemon=True)
        thread.start()
        deadline = time.time() + 2
        while time.time() < deadline and provider._token == "initial":
            time.sleep(0.01)
        provider._stop_renewal.set()
        thread.join(timeout=5)
        self.assertIsNone(provider._token)
        provider._logger.warning.assert_called()

    def test_renewal_loop_without_token_skips_renew(self):
        provider = OpenBaoSecretProvider(base_url="http://openbao:8200/v1")
        provider._logger = mock.Mock()
        thread = threading.Thread(target=provider._renewal_loop, daemon=True)
        thread.start()
        time.sleep(0.2)
        provider._stop_renewal.set()
        thread.join(timeout=5)
        self.assertIsNone(provider._token)
        provider._logger.warning.assert_not_called()


class TestUtilsSecSecretProviderFactory(unittest.TestCase):
    """`create_secret_provider` auto-detection branches."""

    def setUp(self):
        for key in ("EDGEX_SECRETSTORE_TOKEN_FILE", "VAULT_ADDR", "OPENBAO_ADDR"):
            os.environ.pop(key, None)

    def tearDown(self):
        for key in ("EDGEX_SECRETSTORE_TOKEN_FILE", "VAULT_ADDR", "OPENBAO_ADDR"):
            os.environ.pop(key, None)

    @staticmethod
    def _token_file():
        f = tempfile.NamedTemporaryFile(mode="w", delete=False)
        f.write("token\n")
        f.close()
        return f.name

    def test_auto_token_file_without_base_url_uses_default(self):
        token_file = self._token_file()
        try:
            provider = create_secret_provider("auto", token_file=token_file)
            self.assertIsInstance(provider, OpenBaoSecretProvider)
            self.assertEqual(provider.base_url, "http://openbao:8200/v1")
            provider.close()
        finally:
            os.unlink(token_file)

    def test_auto_token_file_with_env_addr(self):
        token_file = self._token_file()
        os.environ["VAULT_ADDR"] = "http://vault:8200"
        try:
            provider = create_secret_provider("auto", token_file=token_file)
            self.assertIsInstance(provider, OpenBaoSecretProvider)
            self.assertEqual(provider.base_url, "http://vault:8200")
            provider.close()
        finally:
            os.unlink(token_file)


class TestUtilsSecCommandRequest(unittest.TestCase):
    """CommandRequest model defaults, aliases and serialization edge cases."""

    def test_defaults(self):
        req = CommandRequest(resource_name="r1")
        self.assertEqual(req.resource_name, "r1")
        self.assertEqual(req.attributes, {})
        self.assertEqual(req.value_type, VALUETYPE_STRING)
        self.assertEqual(req.options, {})

    def test_type_property_get_and_set(self):
        req = CommandRequest(resource_name="r1", value_type=VALUETYPE_STRING)
        self.assertEqual(req.type, VALUETYPE_STRING)
        req.type = "Int32"
        self.assertEqual(req.value_type, "Int32")
        self.assertEqual(req.type, "Int32")

    def test_mutable_defaults_not_shared(self):
        a = CommandRequest(resource_name="a")
        b = CommandRequest(resource_name="b")
        a.attributes["k"] = "v"
        a.options["o"] = 1
        self.assertEqual(b.attributes, {})
        self.assertEqual(b.options, {})

    def test_explicit_fields(self):
        req = CommandRequest(resource_name="r", attributes={"a": 1},
                             value_type="Bool", options={"opt": True})
        self.assertEqual(req.attributes, {"a": 1})
        self.assertEqual(req.value_type, "Bool")
        self.assertEqual(req.options, {"opt": True})

    def test_dataclass_equality(self):
        self.assertEqual(
            CommandRequest(resource_name="r", value_type="Int32"),
            CommandRequest(resource_name="r", value_type="Int32"))
        self.assertNotEqual(
            CommandRequest(resource_name="r"),
            CommandRequest(resource_name="s"))


if __name__ == "__main__":
    unittest.main()
