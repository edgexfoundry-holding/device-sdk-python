# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for Secure Mode support (M11):

- JWT authentication middleware and authenticator
- OpenBao secret provider (KV v2, token file, renewal)
- In-memory secret provider
- Secret provider factory / auto-detection
- Readiness endpoint
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import jwt

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.internal.clients.secret import (  # noqa: E402
    InMemorySecretProvider,
    OpenBaoSecretProvider,
    SecretProvider,
    create_secret_provider,
)
from device_sdk_py.internal.controller.http.auth import (  # noqa: E402
    JWTAuthenticator,
    JWTAuthError,
    JWTAuthMiddleware,
    is_public_endpoint,
)
from device_sdk_py.internal.common.utils import EdgexErrorKind  # noqa: E402
from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402

# HS256 keeps the tests dependency-free (no cryptography module needed).
_JWT_SECRET = "test-secret-for-hs256"
_JWT_ALGO = "HS256"


def _make_token(claims, secret=_JWT_SECRET, algorithm=_JWT_ALGO):
    return jwt.encode(claims, secret, algorithm=algorithm)


class TestInMemorySecretProvider(unittest.TestCase):
    """Test the in-memory secret provider used in insecure mode."""

    def setUp(self):
        self.provider = InMemorySecretProvider()

    def test_roundtrip(self):
        self.provider.store_secret("device", "username", "admin")
        self.provider.store_secret("device", "password", "secret")
        self.assertEqual(self.provider.get_secret("device", "username"), "admin")
        self.assertEqual(self.provider.get_secret("device", "password"), "secret")

    def test_missing_key_raises(self):
        with self.assertRaises(KeyError):
            self.provider.get_secret("nonexistent", "key")

    def test_get_all_secrets(self):
        self.provider.store_secret("device", "a", "1")
        self.provider.store_secret("device", "b", "2")
        secrets = self.provider.get_all_secrets("device")
        self.assertEqual(secrets, {"a": "1", "b": "2"})

    def test_delete_secret(self):
        self.provider.store_secret("device", "a", "1")
        self.provider.delete_secret("device", "a")
        self.assertFalse(self.provider.has_secret("device", "a"))

    def test_has_secret_and_list_paths(self):
        self.provider.store_secret("device", "a", "1")
        self.assertTrue(self.provider.has_secret("device", "a"))
        self.assertIn("device", self.provider.list_paths())

    def test_is_secret_provider(self):
        self.assertIsInstance(self.provider, SecretProvider)


class TestOpenBaoSecretProvider(unittest.TestCase):
    """Test the OpenBao (Vault-compatible) secret provider."""

    def test_kv_path_mapping(self):
        provider = OpenBaoSecretProvider(base_url="http://openbao:8200/v1", token="root")
        self.assertEqual(provider._kv_path("device-simple"), "secret/data/device-simple")
        self.assertEqual(provider._kv_path("/postgres"), "secret/data/postgres")

    def test_get_token_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("s.token123\n")
            token_file = f.name
        try:
            provider = OpenBaoSecretProvider(
                base_url="http://openbao:8200/v1", token_file=token_file
            )
            self.assertEqual(provider._get_token(), "s.token123")
        finally:
            os.unlink(token_file)

    def test_get_token_static(self):
        provider = OpenBaoSecretProvider(base_url="http://openbao:8200/v1", token="static-token")
        self.assertEqual(provider._get_token(), "static-token")

    def test_get_token_missing_raises(self):
        provider = OpenBaoSecretProvider(
            base_url="http://openbao:8200/v1",
            token_file="/nonexistent/token/file",
        )
        with self.assertRaises(RuntimeError):
            provider._get_token()

    def test_headers_include_namespace(self):
        provider = OpenBaoSecretProvider(
            base_url="http://openbao:8200/v1", token="t", namespace="edgex"
        )
        headers = provider._headers()
        self.assertEqual(headers["X-Vault-Token"], "t")
        self.assertEqual(headers["X-Vault-Namespace"], "edgex")

    def test_request_error_raises(self):
        provider = OpenBaoSecretProvider(base_url="http://openbao:8200/v1", token="t")
        fake_resp = mock.Mock(status_code=404, text="not found")
        with mock.patch.object(provider._session, "request", return_value=fake_resp):
            with self.assertRaises(RuntimeError):
                provider._request("get", "secret/data/x")

    def test_store_and_get_secret(self):
        provider = OpenBaoSecretProvider(base_url="http://openbao:8200/v1", token="t")
        provider._session = mock.Mock()
        # store_secret POSTs {"data": {key: value}}
        resp = mock.Mock(status_code=200, text="")
        provider._session.request.return_value = resp
        provider.store_secret("device", "password", "hunter2")
        url = provider._session.request.call_args[0][1]
        self.assertIn("secret/data/device", url)

    def test_close_stops_renewal(self):
        provider = OpenBaoSecretProvider(base_url="http://openbao:8200/v1", token="t")
        provider._session = mock.Mock()
        resp = mock.Mock(status_code=200, text="")
        provider._session.request.return_value = resp
        provider._get_token()  # triggers no thread, but ensure token set
        provider.close()
        self.assertTrue(provider._stop_renewal.is_set())

    def test_context_manager(self):
        with OpenBaoSecretProvider(base_url="http://openbao:8200/v1", token="t") as provider:
            self.assertIsInstance(provider, OpenBaoSecretProvider)


class TestSecretProviderFactory(unittest.TestCase):
    """Test create_secret_provider factory."""

    def tearDown(self):
        for key in ("EDGEX_SECRETSTORE_TOKEN_FILE", "VAULT_ADDR", "OPENBAO_ADDR"):
            os.environ.pop(key, None)

    def test_insecure_mode_returns_in_memory(self):
        provider = create_secret_provider("insecure")
        self.assertIsInstance(provider, InMemorySecretProvider)

    def test_secure_mode_returns_openbao(self):
        provider = create_secret_provider("secure", base_url="http://openbao:8200/v1", token="t")
        self.assertIsInstance(provider, OpenBaoSecretProvider)

    def test_auto_detects_token_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("token")
            token_file = f.name
        try:
            os.environ["EDGEX_SECRETSTORE_TOKEN_FILE"] = token_file
            provider = create_secret_provider("auto", base_url="http://openbao:8200/v1")
            self.assertIsInstance(provider, OpenBaoSecretProvider)
        finally:
            os.unlink(token_file)

    def test_auto_detects_openbao_addr(self):
        os.environ["OPENBAO_ADDR"] = "http://openbao:8200"
        provider = create_secret_provider("auto", token="t")
        self.assertIsInstance(provider, OpenBaoSecretProvider)

    def test_auto_falls_back_to_in_memory(self):
        provider = create_secret_provider("auto")
        self.assertIsInstance(provider, InMemorySecretProvider)


class TestJWTAuthenticator(unittest.TestCase):
    """Test JWT token validation."""

    def setUp(self):
        self.auth = JWTAuthenticator(
            public_key=_JWT_SECRET,
            issuer="edgex",
            audience="device-simple",
            algorithm=_JWT_ALGO,
            leeway=0,
        )

    def _token(self, **overrides):
        claims = {
            "sub": "device-simple",
            "iss": "edgex",
            "aud": "device-simple",
            "exp": int(time.time()) + 3600,
        }
        claims.update(overrides)
        return _make_token(claims)

    def test_valid_token(self):
        claims = self.auth.validate_token(self._token())
        self.assertEqual(claims["sub"], "device-simple")

    def test_missing_token_raises(self):
        with self.assertRaises(JWTAuthError):
            self.auth.validate_token("")

    def test_expired_token_raises(self):
        with self.assertRaises(JWTAuthError) as ctx:
            self.auth.validate_token(self._token(exp=int(time.time()) - 60))
        self.assertEqual(ctx.exception.kind, EdgexErrorKind.CONTRACT_INVALID)
        self.assertEqual(ctx.exception.code, 400)

    def test_invalid_signature_raises(self):
        bad = _make_token({"sub": "x", "exp": int(time.time()) + 3600},
                          secret="wrong-secret")
        with self.assertRaises(JWTAuthError):
            self.auth.validate_token(bad)

    def test_wrong_audience_raises(self):
        with self.assertRaises(JWTAuthError):
            self.auth.validate_token(self._token(aud="other-service"))

    def test_extract_bearer_token(self):
        self.assertEqual(self.auth.extract_token_from_header("Bearer abc.def.ghi"), "abc.def.ghi")
        self.assertIsNone(self.auth.extract_token_from_header("Basic abc"))
        self.assertIsNone(self.auth.extract_token_from_header(None))

    def test_no_key_configured_raises(self):
        auth = JWTAuthenticator()
        with self.assertRaises(RuntimeError):
            auth.validate_token(self._token())


class TestJWTAuthMiddleware(unittest.TestCase):
    """Test the FastAPI JWT middleware end-to-end."""

    def _token(self):
        return _make_token(
            {"sub": "device-simple", "exp": int(time.time()) + 3600},
        )

    def _make_app(self, public_paths=None):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        app = FastAPI()

        @app.get("/api/v3/device/all")
        def protected():
            return {"ok": True}

        @app.get("/api/v3/ping")
        def ping():
            return {"ping": "pong"}

        authenticator = JWTAuthenticator(public_key=_JWT_SECRET, algorithm=_JWT_ALGO)
        middleware = JWTAuthMiddleware(
            app, authenticator, public_paths=public_paths or []
        )
        app.add_middleware(
            type(middleware),
            authenticator=authenticator,
            public_paths=public_paths or [],
        )
        return TestClient(app)

    def test_public_endpoint_ok(self):
        client = self._make_app()
        resp = client.get("/api/v3/ping")
        self.assertEqual(resp.status_code, 200)

    def test_protected_without_token_401(self):
        client = self._make_app()
        resp = client.get("/api/v3/device/all")
        self.assertEqual(resp.status_code, 401)

    def test_protected_with_valid_token(self):
        client = self._make_app()
        resp = client.get("/api/v3/device/all", headers={"Authorization": f"Bearer {self._token()}"})
        self.assertEqual(resp.status_code, 200)

    def test_protected_with_bad_token_401(self):
        client = self._make_app()
        resp = client.get(
            "/api/v3/device/all",
            headers={"Authorization": "Bearer not.a.token"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_custom_public_path(self):
        client = self._make_app(public_paths=["/api/v3/device/all"])
        resp = client.get("/api/v3/device/all")
        self.assertEqual(resp.status_code, 200)

    def test_is_public_endpoint_helper(self):
        self.assertTrue(is_public_endpoint("/api/v3/ping"))
        self.assertTrue(is_public_endpoint("/api/v3/version"))
        self.assertFalse(is_public_endpoint("/api/v3/device/all"))


class TestReadinessEndpoint(unittest.TestCase):
    """Test the /api/v3/readiness endpoint used by security-bootstrapper."""

    def setUp(self):
        self.ds = bootstrap("device-simple", "0.0.0", _Driver())
        self.ds._init_http_controller()
        from starlette.testclient import TestClient

        self.client = TestClient(self.ds.controller.app())

    def tearDown(self):
        self.ds._shutdown()

    def test_readiness_returns_200(self):
        resp = self.client.get("/api/v3/readiness")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ready", resp.json().get("status", ""))


class _Driver:
    def start(self):
        pass


if __name__ == "__main__":
    unittest.main()
