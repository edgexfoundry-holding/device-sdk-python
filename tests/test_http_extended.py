# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Extended unit tests for the HTTP controller layer.

Complements `test_bootstrap.py`, `test_secure_mode.py` and
`test_discovery_profile_scan.py` by targeting the branches they leave uncovered in
`internal/controller/http`: the URL / query helpers and response builders of
`_utils.py`, the JWT authenticator edge cases (JWKS fetching, issuer / unexpected
validation errors, the singleton factory and `setup_jwt_auth`), the device command
GET / PUT handlers, the discovery / profile-scan handlers (locked service, missing
hooks, driver / handler failures) and the router machinery (reserved routes, JWT
bootstrap, event publishing, common endpoints and config serialization).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import unittest
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Optional
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import cbor2  # noqa: E402
import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from device_sdk_py.internal.cache import (  # noqa: E402
    Device,
    DeviceProfile,
)
from device_sdk_py.internal.cache.devices import create_device_cache  # noqa: E402
from device_sdk_py.internal.cache.profiles import create_profile_cache  # noqa: E402
from device_sdk_py.internal.common.consts import (  # noqa: E402
    API_VERSION,
    CONTENT_TYPE_CBOR,
    CONTENT_TYPE_JSON,
    CORRELATION_HEADER,
)
from device_sdk_py.internal.common.utils import (  # noqa: E402
    EdgexError,
    KIND_CONTRACT_INVALID,
    KIND_ENTITY_DOES_NOT_EXIST,
    KIND_NOT_IMPLEMENTED,
    KIND_SERVER_ERROR,
    KIND_SERVICE_LOCKED,
    KIND_STATUS_CONFLICT,
    create_edgx_error,
)
from device_sdk_py.internal.controller.http import auth as auth_mod  # noqa: E402
from device_sdk_py.internal.controller.http._utils import (  # noqa: E402
    base_response,
    event_response,
    filter_query_params,
    parse_request_body,
    send_edgx_error,
    send_edgx_error_with_request_id,
    send_event_response,
    send_response,
)
from device_sdk_py.internal.controller.http.auth import (  # noqa: E402
    JWTAuthenticator,
    JWTAuthError,
    JWTAuthMiddleware,
    get_jwt_authenticator,
    is_public_endpoint,
    setup_jwt_auth,
)
from device_sdk_py.internal.controller.http.router import RestController  # noqa: E402
from device_sdk_py.internal.transformer.transform import (  # noqa: E402
    Event,
    Reading,
)
from device_sdk_py.models import VALUETYPE_BINARY  # noqa: E402

_JWT_SECRET = "test-secret-for-hs256-0123456789"
_JWT_ALGO = "HS256"

_UNSET = object()


def _wait_until(condition, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _make_request(path="/api/v3/ping", query="", headers=None, path_params=None):
    headers = headers or {}
    header_items = [(k.lower().encode("latin-1"), str(v).encode("latin-1"))
                    for k, v in headers.items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": query.encode("latin-1"),
        "headers": header_items,
        "path_params": path_params or {},
        "server": ("testserver", 80),
        "client": ("testclient", 5000),
        "state": {},
    }
    return Request(scope)


@dataclass
class _Point:
    x: int = 1
    y: Optional[int] = None


@dataclass
class _DataclassConfig:
    name: str = "svc"
    description: Optional[str] = None


class _WeirdObject:
    def __repr__(self):
        return "WEIRD-OBJECT"


class _FakeDiscovery:
    def __init__(self, enabled=False):
        self.enabled = enabled


class _FakeDeviceOptions:
    def __init__(self, secure_mode=False, discovery_enabled=False):
        self.secure_mode = secure_mode
        self.discovery = _FakeDiscovery(enabled=discovery_enabled)


class _FakeConfig:
    def __init__(self, device=None):
        self.device = device


class _FakeJwtDevice:
    def __init__(self, secure_mode=True, jwt_jwks_url=None, jwt_public_key=None,
                 jwt_issuer=None, jwt_audience=None):
        self.secure_mode = secure_mode
        self.jwt_jwks_url = jwt_jwks_url
        self.jwt_public_key = jwt_public_key
        self.jwt_issuer = jwt_issuer
        self.jwt_audience = jwt_audience


class _FakeDeviceService:
    def __init__(self, admin_state="UNLOCKED"):
        self.admin_state = admin_state
        self._discovery_stop_events = {}
        self._discovery_thread = None
        self.progress = []
        self.profile_progress = []
        self._publish_discovery_progress = self._record_progress
        self._publish_profile_scan_progress = self._record_profile_progress

    def _record_progress(self, *args):
        self.progress.append(args)

    def _record_profile_progress(self, *args):
        self.profile_progress.append(args)


class TestHttpUtils(unittest.TestCase):
    def test_filter_query_params_separates_reserved(self):
        query, reserved = filter_query_params(
            "ds-pushevent=true&ds-regexcommand=false&ds-returnevent=&raw=hello&x=1&x=2")
        self.assertEqual(reserved, {
            "ds-pushevent": "true",
            "ds-regexcommand": "false",
            "ds-returnevent": "",
        })
        self.assertIn("raw=hello", query)
        self.assertIn("x=1", query)
        self.assertIn("x=2", query)

    def test_filter_query_params_no_reserved(self):
        query, reserved = filter_query_params("a=1&b=2")
        self.assertEqual(reserved, {})
        self.assertEqual(query, "a=1&b=2")

    def test_parse_request_body_empty(self):
        self.assertEqual(parse_request_body(b""), {})

    def test_parse_request_body_invalid_json(self):
        with self.assertRaises(EdgexError):
            parse_request_body(b"{not json")

    def test_parse_request_body_non_dict(self):
        with self.assertRaises(EdgexError):
            parse_request_body(b"[1, 2]")

    def test_parse_request_body_valid(self):
        self.assertEqual(parse_request_body(b'{"a": 1}'), {"a": 1})

    def test_json_default_conversions(self):
        payload = {
            "d": _Point(),
            "b": b"\x01\xff",
            "t": datetime(2024, 1, 2, 3, 4, 5),
            "o": _WeirdObject(),
        }
        resp = send_response(_make_request(headers={CORRELATION_HEADER: "c1"}),
                             payload, "/api", HTTPStatus.OK)
        body = json.loads(resp.body)
        self.assertEqual(body["d"], {"x": 1})
        self.assertEqual(body["b"], base64.b64encode(b"\x01\xff").decode("ascii"))
        self.assertEqual(body["t"], "2024-01-02T03:04:05")
        self.assertEqual(body["o"], "WEIRD-OBJECT")

    def test_event_response_none_event(self):
        payload = event_response(None, HTTPStatus.OK)
        self.assertEqual(payload["apiVersion"], API_VERSION)
        self.assertNotIn("event", payload)

    def test_send_event_response_cbor(self):
        reading = Reading(reading_id="r1", value_type=VALUETYPE_BINARY,
                          binary_value=b"\x00\x01")
        event = Event(event_id="e1", device_name="d", profile_name="p",
                      source_name="s", origin=1, readings=[reading])
        resp = send_event_response(_make_request(), event, HTTPStatus.OK)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], CONTENT_TYPE_CBOR)
        payload = cbor2.loads(resp.body)
        self.assertEqual(payload["apiVersion"], API_VERSION)
        self.assertEqual(payload["event"]["deviceName"], "d")

    def test_send_event_response_json(self):
        event = Event(event_id="e1", device_name="d", profile_name="p",
                      source_name="s", origin=1, readings=[])
        resp = send_event_response(_make_request(), event, HTTPStatus.OK)
        self.assertEqual(resp.headers["content-type"], CONTENT_TYPE_JSON)
        body = json.loads(resp.body)
        self.assertEqual(body["apiVersion"], API_VERSION)
        self.assertEqual(body["event"]["id"], "e1")

    def test_send_event_response_echoes_correlation_id(self):
        event = Event(event_id="e1", device_name="d", profile_name="p",
                      source_name="s", origin=1, readings=[])
        resp = send_event_response(
            _make_request(headers={CORRELATION_HEADER: "corr-x"}), event,
            HTTPStatus.OK)
        self.assertEqual(resp.headers[CORRELATION_HEADER.lower()], "corr-x")

    def test_send_edgx_error(self):
        err = create_edgx_error(KIND_SERVICE_LOCKED, "service locked")
        resp = send_edgx_error(_make_request(), err, "/api")
        self.assertEqual(resp.status_code, 423)
        body = json.loads(resp.body)
        self.assertEqual(body["message"], "service locked")
        self.assertEqual(body["statusCode"], 423)

    def test_send_edgx_error_with_request_id(self):
        err = create_edgx_error(KIND_ENTITY_DOES_NOT_EXIST, "gone")
        resp = send_edgx_error_with_request_id(_make_request(), err, "/api", "rid-1")
        self.assertEqual(resp.status_code, 404)
        body = json.loads(resp.body)
        self.assertEqual(body["requestId"], "rid-1")


class TestHttpAuthExtended(unittest.TestCase):
    def setUp(self):
        self._old_authenticator = auth_mod._authenticator
        auth_mod._authenticator = None

    def tearDown(self):
        auth_mod._authenticator = self._old_authenticator

    def _make_token(self, claims, secret=_JWT_SECRET, algorithm=_JWT_ALGO):
        return jwt.encode(claims, secret, algorithm=algorithm)

    def test_get_public_key_via_jwks(self):
        auth = JWTAuthenticator(jwks_url="http://jwks.example.com/keys")
        fake = mock.Mock()
        fake.json.return_value = {"keys": []}
        with mock.patch("requests.get", return_value=fake) as m:
            key = auth._get_public_key()
        self.assertEqual(key, {"keys": []})
        m.assert_called_once_with("http://jwks.example.com/keys", timeout=10)

    def test_fetch_jwks_key_cache_hit(self):
        auth = JWTAuthenticator(jwks_url="http://jwks.example.com/keys")
        fake = mock.Mock()
        fake.json.return_value = {"keys": [{"kid": "1"}]}
        with mock.patch("requests.get", return_value=fake) as m:
            first = auth._get_public_key()
            second = auth._get_public_key()
        self.assertEqual(first, {"keys": [{"kid": "1"}]})
        self.assertEqual(second, {"keys": [{"kid": "1"}]})
        self.assertEqual(m.call_count, 1)

    def test_fetch_jwks_key_failure(self):
        auth = JWTAuthenticator(jwks_url="http://jwks.example.com/keys")
        with mock.patch("requests.get",
                        side_effect=RuntimeError("connection refused")):
            with self.assertRaises(RuntimeError):
                auth._get_public_key()

    def test_validate_token_invalid_issuer(self):
        auth = JWTAuthenticator(public_key=_JWT_SECRET, algorithm=_JWT_ALGO,
                                issuer="expected", leeway=0)
        token = self._make_token({
            "sub": "x", "iss": "other", "exp": int(time.time()) + 3600})
        with self.assertRaises(JWTAuthError) as ctx:
            auth.validate_token(token)
        self.assertEqual(ctx.exception.message, "Invalid issuer")

    def test_validate_token_unexpected_error(self):
        auth = JWTAuthenticator(public_key=_JWT_SECRET, algorithm=_JWT_ALGO)
        token = self._make_token({"sub": "x", "exp": int(time.time()) + 3600})
        with mock.patch("device_sdk_py.internal.controller.http.auth.jwt.decode",
                        side_effect=ValueError("boom")):
            with self.assertRaises(JWTAuthError) as ctx:
                auth.validate_token(token)
        self.assertIn("Token validation failed", ctx.exception.message)

    def test_is_public_path_prefix_match(self):
        auth = JWTAuthenticator(public_key=_JWT_SECRET)
        mw = JWTAuthMiddleware(mock.Mock(), auth, public_paths=[], public_prefixes=[])
        self.assertTrue(mw.is_public_path("/api/v3/ping"))
        self.assertTrue(mw.is_public_path("/docs/api"))
        self.assertFalse(mw.is_public_path("/api/v3/device/all"))

    def test_get_jwt_authenticator_creates_singleton(self):
        first = get_jwt_authenticator(public_key=_JWT_SECRET)
        second = get_jwt_authenticator(public_key=_JWT_SECRET)
        self.assertIsInstance(first, JWTAuthenticator)
        self.assertIs(first, second)

    def test_is_public_endpoint_prefix_match(self):
        self.assertTrue(is_public_endpoint("/api/v3/ping"))
        self.assertTrue(is_public_endpoint("/docs"))
        self.assertFalse(is_public_endpoint("/api/v3/device/all"))

    def test_setup_jwt_auth(self):
        app = FastAPI()
        middleware = setup_jwt_auth(app, public_key=_JWT_SECRET,
                                    public_paths=["/custom"])
        self.assertIsInstance(middleware, JWTAuthMiddleware)
        self.assertEqual(len(app.user_middleware), 1)


class TestHttpCommand(unittest.TestCase):
    def _make_client(self, **kwargs):
        self.controller = RestController(service_name="device-command",
                                         service_version="0.0.1", **kwargs)
        self.controller.init_rest_routes()
        return TestClient(self.controller.app())

    def _event(self):
        return Event(event_id="e-1", device_name="dev", profile_name="p",
                     source_name="temp", origin=123, readings=[])

    def test_get_command_defaults(self):
        client = self._make_client()
        with mock.patch("device_sdk_py.internal.controller.http.command.command_read",
                        return_value=self._event()) as m:
            resp = client.get("/api/v3/device/name/dev/temp?ds-pushevent=false&raw=1")
            self.assertEqual(resp.status_code, 200)
            kwargs = m.call_args.kwargs
            self.assertTrue(kwargs["regex_cmd"])
            self.assertEqual(kwargs["attributes"], "raw=1")
            self.assertEqual(resp.json()["event"]["id"], "e-1")

    def test_get_command_regex_disabled(self):
        client = self._make_client()
        with mock.patch("device_sdk_py.internal.controller.http.command.command_read",
                        return_value=self._event()) as m:
            resp = client.get("/api/v3/device/name/dev/temp?ds-regexcommand=false")
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(m.call_args.kwargs["regex_cmd"])

    def test_get_command_push_event_and_no_return(self):
        pushed = []
        client = self._make_client(
            send_event_handler=lambda e, c: pushed.append((e, c)))
        with mock.patch("device_sdk_py.internal.controller.http.command.command_read",
                        return_value=self._event()):
            resp = client.get(
                "/api/v3/device/name/dev/temp?ds-pushevent=true&ds-returnevent=false")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.content, b"")
            self.assertTrue(_wait_until(lambda: pushed))
        self.assertEqual(pushed[0][0].event_id, "e-1")

    def test_get_command_error(self):
        client = self._make_client()
        with mock.patch("device_sdk_py.internal.controller.http.command.command_read",
                        side_effect=create_edgx_error(KIND_SERVICE_LOCKED,
                                                      "service locked")):
            resp = client.get("/api/v3/device/name/dev/temp")
            self.assertEqual(resp.status_code, 423)

    def test_set_command_success(self):
        pushed = []
        client = self._make_client(
            send_event_handler=lambda e, c: pushed.append((e, c)))
        with mock.patch("device_sdk_py.internal.controller.http.command.command_write",
                        return_value=self._event()) as m:
            resp = client.put("/api/v3/device/name/dev/temp?ds-regexcommand=true&foo=bar",
                              json={"temp": "42"})
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["apiVersion"], API_VERSION)
            self.assertEqual(body["statusCode"], 200)
            kwargs = m.call_args.kwargs
            self.assertEqual(kwargs["attributes"], "foo=bar")
            self.assertEqual(kwargs["requests"], {"temp": "42"})
        self.assertTrue(_wait_until(lambda: pushed))

    def test_set_command_no_event(self):
        client = self._make_client()
        with mock.patch("device_sdk_py.internal.controller.http.command.command_write",
                        return_value=None):
            resp = client.put("/api/v3/device/name/dev/temp", json={"temp": "42"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["statusCode"], 200)

    def test_set_command_bad_body(self):
        client = self._make_client()
        resp = client.put("/api/v3/device/name/dev/temp", content="{oops")
        self.assertEqual(resp.status_code, 400)

    def test_set_command_write_error(self):
        client = self._make_client()
        with mock.patch("device_sdk_py.internal.controller.http.command.command_write",
                        side_effect=create_edgx_error(KIND_SERVER_ERROR,
                                                      "write failed")):
            resp = client.put("/api/v3/device/name/dev/temp", json={"temp": "42"})
            self.assertEqual(resp.status_code, 500)

    def test_set_command_empty_body(self):
        client = self._make_client()
        with mock.patch("device_sdk_py.internal.controller.http.command.command_write",
                        return_value=None) as m:
            resp = client.put("/api/v3/device/name/dev/temp", content=b"")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(m.call_args.kwargs["requests"], {})


class TestHttpDiscovery(unittest.TestCase):
    def setUp(self):
        create_device_cache([])
        create_profile_cache([])
        self.controller = None
        self.client = None

    def tearDown(self):
        create_device_cache([])
        create_profile_cache([])

    def _make_controller(self, device_service=None, driver=None, configuration=None,
                         discovery_stop_handler=None, profile_scan_handler=None,
                         profile_scan_stop_handler=None):
        self.controller = RestController(
            service_name="device-discovery",
            service_version="0.0.1",
            logger=mock.Mock(),
            configuration=configuration,
            driver=driver or mock.Mock(),
            device_service=device_service,
            device_discovery_stop_handler=discovery_stop_handler,
            profile_scan_handler=profile_scan_handler,
            profile_scan_stop_handler=profile_scan_stop_handler)
        self.controller.init_rest_routes()
        self.client = TestClient(self.controller.app())
        return self.client

    def _add_sensor(self):
        create_device_cache([Device(name="sensor-01", profile_name="p1")])
        create_profile_cache([])

    def test_discovery_service_locked(self):
        client = self._make_controller(
            device_service=_FakeDeviceService(admin_state="LOCKED"))
        resp = client.post("/api/v3/discovery")
        self.assertEqual(resp.status_code, 423)

    def test_discovery_disabled(self):
        client = self._make_controller(
            configuration=_FakeConfig(_FakeDeviceOptions(discovery_enabled=False)),
            device_service=_FakeDeviceService())
        resp = client.post("/api/v3/discovery")
        self.assertEqual(resp.status_code, 503)

    def test_discovery_enabled_triggers_driver(self):
        ds = _FakeDeviceService()
        driver = mock.Mock()
        client = self._make_controller(
            configuration=_FakeConfig(_FakeDeviceOptions(discovery_enabled=True)),
            device_service=ds, driver=driver)
        resp = client.post("/api/v3/discovery")
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["message"], "Device Discovery is triggered.")
        self.assertTrue(_wait_until(lambda: driver.discover.called))
        self.assertTrue(_wait_until(lambda: len(ds.progress) >= 2))
        self.assertEqual(ds.progress[0][0], 0)
        self.assertEqual(ds.progress[1][0], 100)

    def test_discovery_driver_error_publishes_failure(self):
        ds = _FakeDeviceService()
        driver = mock.Mock()
        driver.discover.side_effect = RuntimeError("boom")
        client = self._make_controller(
            configuration=_FakeConfig(_FakeDeviceOptions(discovery_enabled=True)),
            device_service=ds, driver=driver)
        resp = client.post("/api/v3/discovery")
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(_wait_until(lambda: any(p[0] == -1 for p in ds.progress)))
        self.assertEqual(ds._discovery_stop_events, {})
        self.assertIsNone(ds._discovery_thread)

    def test_discovery_without_device_service(self):
        driver = mock.Mock()
        client = self._make_controller(
            configuration=_FakeConfig(_FakeDeviceOptions(discovery_enabled=True)),
            driver=driver)
        resp = client.post("/api/v3/discovery")
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(_wait_until(lambda: driver.discover.called))

    def test_discovery_enabled_no_discovery_option(self):
        class _NoDiscoveryDevice:
            secure_mode = False

        class _Cfg:
            device = _NoDiscoveryDevice()

        ctrl = RestController(service_name="s", service_version="1",
                              configuration=_Cfg())
        self.assertFalse(ctrl._discovery_enabled())

    def test_stop_discovery_not_implemented(self):
        client = self._make_controller()
        resp = client.delete("/api/v3/discovery")
        self.assertEqual(resp.status_code, 501)

    def test_stop_discovery_with_request_id_and_options(self):
        captured = []

        def handler(request_id, options):
            captured.append((request_id, options))

        client = self._make_controller(discovery_stop_handler=handler)
        resp = client.delete("/api/v3/discovery/abc?stop=true&ids=1&ids=2")
        self.assertEqual(resp.status_code, 200)
        request_id, options = captured[0]
        self.assertEqual(request_id, "abc")
        self.assertEqual(options["stop"], ["true"])
        self.assertEqual(options["ids"], ["1", "2"])

    def test_stop_discovery_handler_error(self):
        def handler(request_id, options):
            raise create_edgx_error(KIND_STATUS_CONFLICT, "busy")

        client = self._make_controller(discovery_stop_handler=handler)
        resp = client.delete("/api/v3/discovery")
        self.assertEqual(resp.status_code, 409)

    def test_profile_scan_service_locked(self):
        client = self._make_controller(
            device_service=_FakeDeviceService(admin_state="LOCKED"))
        resp = client.post("/api/v3/profilescan", json={})
        self.assertEqual(resp.status_code, 423)

    def test_profile_scan_bad_body(self):
        client = self._make_controller()
        resp = client.post("/api/v3/profilescan", content="{not json")
        self.assertEqual(resp.status_code, 400)

    def test_profile_scan_empty_device_name(self):
        client = self._make_controller()
        resp = client.post("/api/v3/profilescan", json={})
        self.assertEqual(resp.status_code, 400)

    def test_profile_scan_device_not_found(self):
        client = self._make_controller()
        resp = client.post("/api/v3/profilescan", json={"deviceName": "ghost"})
        self.assertEqual(resp.status_code, 404)

    def test_profile_scan_profile_duplicated(self):
        create_device_cache([Device(name="sensor-01", profile_name="p1")])
        create_profile_cache([DeviceProfile(name="p1")])
        client = self._make_controller()
        resp = client.post("/api/v3/profilescan",
                           json={"deviceName": "sensor-01", "profileName": "p1"})
        self.assertEqual(resp.status_code, 409)

    def test_profile_scan_not_implemented(self):
        self._add_sensor()
        client = self._make_controller()
        resp = client.post("/api/v3/profilescan",
                           json={"deviceName": "sensor-01", "profileName": "newp"})
        self.assertEqual(resp.status_code, 501)

    def test_profile_scan_auto_profile_name(self):
        captured = []

        def handler(device_name, profile_name, request_id, options):
            captured.append((device_name, profile_name, request_id, options))

        self._add_sensor()
        client = self._make_controller(profile_scan_handler=handler)
        resp = client.post("/api/v3/profilescan",
                           json={"deviceName": "sensor-01", "requestId": "req-1"})
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(_wait_until(lambda: captured))
        device_name, profile_name, request_id, options = captured[0]
        self.assertEqual(device_name, "sensor-01")
        self.assertTrue(profile_name.startswith("sensor-01_profile_"))
        self.assertEqual(request_id, "req-1")
        self.assertEqual(options, {})

    def test_profile_scan_correlation_id_as_request_id(self):
        captured = []

        def handler(device_name, profile_name, request_id, options):
            captured.append(request_id)

        self._add_sensor()
        client = self._make_controller(profile_scan_handler=handler)
        resp = client.post("/api/v3/profilescan",
                           json={"deviceName": "sensor-01", "profileName": "np"},
                           headers={CORRELATION_HEADER: "corr-42"})
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(_wait_until(lambda: captured))
        self.assertEqual(captured[0], "corr-42")

    def test_profile_scan_with_options(self):
        captured = []

        def handler(device_name, profile_name, request_id, options):
            captured.append(options)

        self._add_sensor()
        client = self._make_controller(profile_scan_handler=handler)
        resp = client.post("/api/v3/profilescan",
                           json={"deviceName": "sensor-01", "profileName": "np",
                                 "options": {"timeout": 30}})
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(_wait_until(lambda: captured))
        self.assertEqual(captured[0], {"timeout": 30})

    def test_profile_scan_handler_error_publishes_failure(self):
        def handler(*args):
            raise RuntimeError("boom")

        self._add_sensor()
        ds = _FakeDeviceService()
        client = self._make_controller(device_service=ds,
                                       profile_scan_handler=handler)
        resp = client.post("/api/v3/profilescan",
                           json={"deviceName": "sensor-01", "profileName": "np"})
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(_wait_until(
            lambda: any(p[1] == -1 for p in ds.profile_progress)))

    def test_stop_profile_scan_not_implemented(self):
        client = self._make_controller()
        resp = client.delete("/api/v3/profilescan/device/sensor-01")
        self.assertEqual(resp.status_code, 501)

    def test_stop_profile_scan_with_options(self):
        captured = []

        def handler(device_name, options):
            captured.append((device_name, options))

        client = self._make_controller(profile_scan_stop_handler=handler)
        resp = client.delete("/api/v3/profilescan/device/sensor-01?force=true")
        self.assertEqual(resp.status_code, 200)
        device_name, options = captured[0]
        self.assertEqual(device_name, "sensor-01")
        self.assertEqual(options["force"], ["true"])

    def test_stop_profile_scan_handler_error(self):
        def handler(device_name, options):
            raise create_edgx_error(KIND_NOT_IMPLEMENTED, "nope")

        client = self._make_controller(profile_scan_stop_handler=handler)
        resp = client.delete("/api/v3/profilescan/device/sensor-01")
        self.assertEqual(resp.status_code, 501)


class TestHttpSecret(unittest.TestCase):
    def setUp(self):
        self.provider = mock.Mock()
        self.provider.store_secret.return_value = None
        self.ds = _FakeDeviceService()
        self.ds.secret_provider = mock.Mock(return_value=self.provider)

    def _make_controller(self, device_service=_UNSET):
        self.controller = RestController(
            service_name="device-secret",
            service_version="0.0.1",
            logger=mock.Mock(),
            configuration=_FakeConfig(_FakeDeviceOptions(secure_mode=False)),
            driver=mock.Mock(),
            device_service=self.ds if device_service is _UNSET else device_service)
        self.controller.init_rest_routes()
        return TestClient(self.controller.app())

    @staticmethod
    def _secret_body(**overrides):
        body = {
            "apiVersion": API_VERSION,
            "secretName": "db",
            "secretData": [
                {"key": "user", "value": "alice"},
                {"key": "pass", "value": "s3cret"},
            ],
        }
        body.update(overrides)
        return body

    def test_add_secret_success(self):
        client = self._make_controller()
        resp = client.post("/api/v3/secret",
                           json=self._secret_body(requestId="req-123"))
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["apiVersion"], API_VERSION)
        self.assertEqual(body["requestId"], "req-123")
        self.assertEqual(body["statusCode"], 201)
        self.assertNotIn("message", body)
        self.provider.store_secret.assert_has_calls([
            mock.call("db", "user", "alice"),
            mock.call("db", "pass", "s3cret"),
        ])

    def test_add_secret_trims_secret_name(self):
        client = self._make_controller()
        resp = client.post("/api/v3/secret",
                           json=self._secret_body(secretName="  db  "))
        self.assertEqual(resp.status_code, 201)
        self.provider.store_secret.assert_has_calls(
            [mock.call("db", "user", "alice")])

    def test_add_secret_omits_empty_request_id(self):
        client = self._make_controller()
        resp = client.post("/api/v3/secret", json=self._secret_body())
        self.assertEqual(resp.status_code, 201)
        self.assertNotIn("requestId", resp.json())

    def test_add_secret_missing_secret_name(self):
        client = self._make_controller()
        resp = client.post("/api/v3/secret",
                           json=self._secret_body(secretName=""))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["message"], "SecretRequest validation failed.")
        self.provider.store_secret.assert_not_called()

    def test_add_secret_missing_secret_data(self):
        client = self._make_controller()
        for body in (self._secret_body(),):
            del body["secretData"]
            resp = client.post("/api/v3/secret", json=body)
            self.assertEqual(resp.status_code, 400)
        self.provider.store_secret.assert_not_called()

    def test_add_secret_invalid_secret_data(self):
        client = self._make_controller()
        for secret_data in ([], "nope", [{"key": "user"}],
                            [{"value": "alice"}], [{"key": "", "value": "alice"}],
                            ["not-a-map"]):
            with self.subTest(secret_data=secret_data):
                resp = client.post("/api/v3/secret",
                                   json=self._secret_body(secretData=secret_data))
                self.assertEqual(resp.status_code, 400)
        self.provider.store_secret.assert_not_called()

    def test_add_secret_bad_json(self):
        client = self._make_controller()
        resp = client.post("/api/v3/secret", content="{not json")
        self.assertEqual(resp.status_code, 400)

    def test_add_secret_no_device_service(self):
        client = self._make_controller(device_service=None)
        resp = client.post("/api/v3/secret", json=self._secret_body())
        self.assertEqual(resp.status_code, 500)
        self.assertIn("secret provider is missing", resp.json()["message"])

    def test_add_secret_provider_error(self):
        self.provider.store_secret.side_effect = RuntimeError("boom")
        client = self._make_controller()
        resp = client.post("/api/v3/secret", json=self._secret_body())
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["message"], "adding secret failed")

    def test_secret_route_is_reserved(self):
        ctrl = RestController(service_name="device-secret",
                              service_version="0.0.1", logger=mock.Mock())
        ctrl.init_rest_routes()
        with self.assertRaises(EdgexError) as ctx:
            ctrl.add_route("/api/v3/secret", lambda request: None)
        self.assertEqual(ctx.exception.message, "route is reserved")


class TestHttpRouter(unittest.TestCase):
    def setUp(self):
        self._old_authenticator = auth_mod._authenticator
        auth_mod._authenticator = None

    def tearDown(self):
        auth_mod._authenticator = self._old_authenticator

    def _controller(self, **kwargs):
        kwargs.setdefault("logger", mock.Mock())
        return RestController(service_name="device-router", service_version="0.0.1",
                              **kwargs)

    def test_add_route_reserved_raises(self):
        ctrl = self._controller()
        ctrl.reserved_routes.add("/api/v3/ping")
        with self.assertRaises(EdgexError) as ctx:
            ctrl.add_route("/api/v3/ping", lambda request: None)
        self.assertEqual(ctx.exception.message, "route is reserved")

    def test_add_route_custom_registers(self):
        ctrl = self._controller()
        ctrl.add_route("/custom", lambda request: None, methods=["POST"])
        self.assertIn("/custom", [r.path for r in ctrl.router.routes])
        ctrl.logger.debug.assert_called()

    def test_set_custom_config_info(self):
        ctrl = self._controller()
        ctrl.set_custom_config_info({"a": 1})
        self.assertEqual(ctrl.custom_config, {"a": 1})

    def test_ping(self):
        ctrl = self._controller()
        resp = ctrl.ping(_make_request())
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["apiVersion"], API_VERSION)
        self.assertEqual(body["serviceName"], "device-router")

    def test_version(self):
        ctrl = self._controller()
        resp = ctrl.version(_make_request())
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["version"], "0.0.1")

    def test_metrics_without_provider(self):
        ctrl = self._controller()
        resp = ctrl.metrics(_make_request())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.body), {})

    def test_metrics_with_provider(self):
        ctrl = self._controller(metrics_provider=lambda: {"EventsSent": 5})
        resp = ctrl.metrics(_make_request())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.body)["EventsSent"], 5)

    def test_config(self):
        ctrl = self._controller(configuration=_DataclassConfig())
        resp = ctrl.config(_make_request())
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.body)
        self.assertEqual(body["config"], {"name": "svc"})

    def test_config_to_dict_none(self):
        ctrl = self._controller()
        self.assertEqual(ctrl._config_to_dict(), {})

    def test_config_to_dict_dict(self):
        ctrl = self._controller(configuration={"a": 1})
        self.assertEqual(ctrl._config_to_dict(), {"a": 1})

    def test_config_to_dict_str(self):
        ctrl = self._controller(configuration="raw")
        self.assertEqual(ctrl._config_to_dict(), {"value": "raw"})

    def test_config_to_dict_custom_dataclass(self):
        ctrl = self._controller(configuration={"a": 1})
        ctrl.set_custom_config_info(_DataclassConfig())
        result = ctrl._config_to_dict()
        self.assertEqual(result["Custom"], {"name": "svc"})

    def test_config_to_dict_custom_other(self):
        ctrl = self._controller(configuration={"a": 1})
        ctrl.set_custom_config_info({"b": 2})
        result = ctrl._config_to_dict()
        self.assertEqual(result["Custom"], {"b": 2})

    def test_send_event_with_handler(self):
        received = []
        ctrl = self._controller(
            send_event_handler=lambda e, c: received.append((e, c)))
        ctrl.send_event("evt", "corr")
        self.assertTrue(_wait_until(lambda: received))
        self.assertEqual(received[0], ("evt", "corr"))

    def test_send_event_without_handler(self):
        ctrl = self._controller()
        ctrl.send_event("evt", "corr")
        self.assertTrue(_wait_until(lambda: ctrl.logger.debug.called))

    def test_jwt_auth_device_none(self):
        ctrl = self._controller(configuration=_FakeConfig(device=None))
        self.assertIsNone(ctrl._jwt_authenticator)

    def test_jwt_auth_secure_disabled(self):
        ctrl = self._controller(configuration=_FakeConfig(
            _FakeDeviceOptions(secure_mode=False)))
        self.assertIsNone(ctrl._jwt_authenticator)

    def test_jwt_auth_no_jwt_config(self):
        ctrl = self._controller(configuration=_FakeConfig(
            _FakeJwtDevice(secure_mode=True)))
        self.assertIsNone(ctrl._jwt_authenticator)
        ctrl.logger.warning.assert_called()

    def test_jwt_auth_with_public_key(self):
        ctrl = self._controller(configuration=_FakeConfig(
            _FakeJwtDevice(secure_mode=True, jwt_public_key="pem-key",
                           jwt_issuer="edgex", jwt_audience="device-router")))
        self.assertIsNotNone(ctrl._jwt_authenticator)
        self.assertEqual(ctrl._jwt_authenticator._public_key, "pem-key")
        self.assertGreater(len(ctrl.router.user_middleware), 0)
        ctrl.logger.info.assert_called()

    def test_jwt_auth_from_env(self):
        os.environ["EDGEX_JWT_JWKS_URL"] = "http://jwks.example/keys"
        os.environ["EDGEX_JWT_ISSUER"] = "env-issuer"
        os.environ["EDGEX_JWT_AUDIENCE"] = "env-audience"
        try:
            ctrl = self._controller(configuration=_FakeConfig(
                _FakeJwtDevice(secure_mode=True)))
        finally:
            os.environ.pop("EDGEX_JWT_JWKS_URL", None)
            os.environ.pop("EDGEX_JWT_ISSUER", None)
            os.environ.pop("EDGEX_JWT_AUDIENCE", None)
        self.assertIsNotNone(ctrl._jwt_authenticator)
        self.assertEqual(ctrl._jwt_authenticator._jwks_url, "http://jwks.example/keys")
        self.assertEqual(ctrl._jwt_authenticator._issuer, "env-issuer")
        self.assertEqual(ctrl._jwt_authenticator._audience, "env-audience")


if __name__ == "__main__":
    unittest.main()
