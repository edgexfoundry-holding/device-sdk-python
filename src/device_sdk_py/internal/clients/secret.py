# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
SecretProvider implementations for EdgeX Device Service.

Provides two implementations:
- InMemorySecretProvider: In-memory storage for insecure mode / testing
- OpenBaoSecretProvider: OpenBao (Vault-compatible) HTTP client for secure mode

Both implement the EdgeX SecretProvider interface:
- store_secret(path, key, value)
- get_secret(path, key) -> str
- get_all_secrets(path) -> dict
- delete_secret(path, key)
"""

from __future__ import annotations

import os
import time
import logging
import threading
from typing import Dict, Optional, List
from urllib.parse import urljoin

import requests

__all__ = ["SecretProvider", "InMemorySecretProvider", "OpenBaoSecretProvider", "create_secret_provider"]

_LOGGER = logging.getLogger(__name__)


class SecretProvider:
    """Base interface for EdgeX SecretProvider."""

    def store_secret(self, path: str, key: str, value: str) -> None:
        raise NotImplementedError

    def get_secret(self, path: str, key: str) -> str:
        raise NotImplementedError

    def get_all_secrets(self, path: str) -> Dict[str, str]:
        raise NotImplementedError

    def delete_secret(self, path: str, key: str) -> None:
        raise NotImplementedError


class InMemorySecretProvider(SecretProvider):
    """In-memory secret store for insecure mode / testing."""

    def __init__(self) -> None:
        self._secrets: Dict[str, Dict[str, str]] = {}

    def store_secret(self, path: str, key: str, value: str) -> None:
        if path not in self._secrets:
            self._secrets[path] = {}
        self._secrets[path][key] = value

    def get_secret(self, path: str, key: str) -> str:
        if path not in self._secrets or key not in self._secrets[path]:
            raise KeyError(f"secret not found at path '{path}' key '{key}'")
        return self._secrets[path][key]

    def get_all_secrets(self, path: str) -> Dict[str, str]:
        return self._secrets.get(path, {}).copy()

    def delete_secret(self, path: str, key: str) -> None:
        if path not in self._secrets or key not in self._secrets[path]:
            raise KeyError(f"secret not found at path '{path}' key '{key}'")
        del self._secrets[path][key]
        if not self._secrets[path]:
            del self._secrets[path]

    def has_secret(self, path: str, key: str) -> bool:
        return path in self._secrets and key in self._secrets[path]

    def list_paths(self) -> List[str]:
        return list(self._secrets.keys())

    def delete_path(self, path: str) -> None:
        if path in self._secrets:
            del self._secrets[path]


class OpenBaoSecretProvider(SecretProvider):
    """OpenBao (Vault-compatible) secret provider for secure mode.

    Communicates with OpenBao via HTTP API (Vault-compatible).
    Supports token-based authentication with automatic token renewal.
    """

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        token_file: Optional[str] = None,
        namespace: Optional[str] = None,
        timeout: float = 10.0,
        logger: Optional[logging.Logger] = None,
        token_ttl: int = 3600,  # Token TTL in seconds (default 1 hour)
        token_renewal_threshold: float = 0.8,  # Renew when 80% of TTL elapsed
    ) -> None:
        """
        Args:
            base_url: OpenBao base URL (e.g., "http://openbao:8200/v1")
            token: Static token (or read from token_file)
            token_file: Path to token file (e.g., /tmp/edgex/secrets/<service>/token)
            namespace: OpenBao namespace (optional)
            timeout: Request timeout in seconds
            logger: Optional logger
            token_ttl: Expected token TTL in seconds (default 1 hour)
            token_renewal_threshold: Renew token when this fraction of TTL elapsed (default 0.8)
        """
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._token_file = token_file
        self.namespace = namespace
        self.timeout = timeout
        self._logger = logger or _LOGGER
        self._session = requests.Session()
        self._token_expiry = time.time() + token_ttl if token else 0.0
        self._token_ttl = token_ttl
        self._token_renewal_threshold = token_renewal_threshold
        self._token_lock = threading.Lock()
        self._renewal_thread: Optional[threading.Thread] = None
        self._stop_renewal = threading.Event()

    def _get_token(self) -> str:
        """Get token from file or static value, with automatic renewal."""
        with self._token_lock:
            now = time.time()
            if self._token and (self._token_expiry == 0.0 or now < self._token_expiry):
                return self._token

            # Token missing or expired: try to (re-)read from the token file.
            if self._token_file and os.path.exists(self._token_file):
                with open(self._token_file, "r") as f:
                    self._token = f.read().strip()
                if self._token:
                    self._token_expiry = now + self._token_ttl
                    self._logger.debug("Loaded token from file, expires in %ds", self._token_ttl)
                    return self._token

            raise RuntimeError("No token available: token_file not found or token not set")

    def _headers(self) -> Dict[str, str]:
        token = self._get_token()
        headers = {"X-Vault-Token": token, "Content-Type": "application/json"}
        if self.namespace:
            headers["X-Vault-Namespace"] = self.namespace
        return headers

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        # Start token renewal on first request
        if not self._renewal_thread or not self._renewal_thread.is_alive():
            self._start_token_renewal()
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        resp = self._session.request(
            method, url, headers=self._headers(), timeout=self.timeout, **kwargs
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenBao error {resp.status_code}: {resp.text}")
        return resp

    def _kv_path(self, path: str) -> str:
        """Convert secret path to OpenBao KV v2 API path."""
        # EdgeX secret path -> OpenBao KV v2: secret/data/<path>
        return f"secret/data/{path.lstrip('/')}"

    def _start_token_renewal(self) -> None:
        """Start background token renewal thread."""
        if self._renewal_thread and self._renewal_thread.is_alive():
            return

        self._stop_renewal.clear()
        self._renewal_thread = threading.Thread(
            target=self._renewal_loop,
            daemon=True,
            name="openbao-token-renewal",
        )
        self._renewal_thread.start()
        self._logger.debug("Started token renewal thread")

    def _stop_token_renewal(self) -> None:
        """Stop background token renewal thread."""
        self._stop_renewal.set()
        if self._renewal_thread and self._renewal_thread.is_alive():
            self._renewal_thread.join(timeout=5.0)
        self._logger.debug("Stopped token renewal thread")

    def _renewal_loop(self) -> None:
        """Background loop to renew token before expiry."""
        while not self._stop_renewal.is_set():
            # Check whether renewal is due without holding the lock across the
            # (re-entrant) token read below.
            with self._token_lock:
                if not self._token:
                    renew = False
                else:
                    now = time.time()
                    remaining = self._token_expiry - now
                    renew = remaining <= (1.0 - self._token_renewal_threshold) * self._token_ttl
            if renew:
                self._logger.debug("Renewing OpenBao token")
                try:
                    with self._token_lock:
                        self._token = None  # Force re-read from file
                    self._get_token()
                except Exception as exc:  # pylint: disable=broad-except
                    self._logger.warning("Token renewal failed: %s", exc)

            # Sleep for 1/10 of TTL or 60 seconds, whichever is smaller
            sleep_time = min(self._token_ttl / 10, 60)
            self._stop_renewal.wait(timeout=sleep_time)

    def store_secret(self, path: str, key: str, value: str) -> None:
        data = {"data": {key: value}}
        self._request("post", self._kv_path(path), json=data)
        self._logger.debug("Stored secret at %s/%s", path, key)

    def get_secret(self, path: str, key: str) -> str:
        resp = self._request("get", self._kv_path(path))
        data = resp.json().get("data", {}).get("data", {})
        if key not in data:
            raise KeyError(f"secret not found at path '{path}' key '{key}'")
        return data[key]

    def get_all_secrets(self, path: str) -> Dict[str, str]:
        resp = self._request("get", self._kv_path(path))
        return resp.json().get("data", {}).get("data", {})

    def delete_secret(self, path: str, key: str) -> None:
        # OpenBao KV v2 doesn't support single key deletion easily
        # Read all, remove key, write back
        all_secrets = self.get_all_secrets(path)
        if key not in all_secrets:
            raise KeyError(f"secret not found at path '{path}' key '{key}'")
        del all_secrets[key]
        self._request("post", self._kv_path(path), json={"data": all_secrets})
        self._logger.debug("Deleted secret %s/%s", path, key)

    # -- convenience methods ----------------------------------------------------

    def has_secret(self, path: str, key: str) -> bool:
        try:
            self.get_secret(path, key)
            return True
        except KeyError:
            return False

    def list_paths(self) -> List[str]:
        resp = self._request("list", "secret/metadata")
        return resp.json().get("data", {}).get("keys", [])

    def delete_path(self, path: str) -> None:
        self._request("delete", f"secret/metadata/{path.lstrip('/')}")

    def close(self) -> None:
        """Close the provider and stop background renewal thread."""
        self._stop_token_renewal()
        self._session.close()
        self._logger.debug("OpenBaoSecretProvider closed")

    def __enter__(self) -> "OpenBaoSecretProvider":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def create_secret_provider(
    mode: str = "auto",
    **kwargs,
) -> SecretProvider:
    """Factory to create appropriate SecretProvider based on mode.

    Args:
        mode: "auto" (detect from env), "insecure" (in-memory), "secure" (OpenBao)
        **kwargs: passed to provider constructor

    Returns:
        SecretProvider instance
    """
    if mode == "insecure":
        return InMemorySecretProvider()
    if mode == "secure":
        return OpenBaoSecretProvider(**kwargs)

    # Auto-detect: check for OpenBao token file or VAULT_ADDR
    token_file = kwargs.get("token_file") or os.environ.get("EDGEX_SECRETSTORE_TOKEN_FILE")
    vault_addr = kwargs.get("base_url") or os.environ.get("VAULT_ADDR") or os.environ.get("OPENBAO_ADDR")

    if token_file and os.path.exists(token_file):
        if not kwargs.get("base_url"):
            kwargs["base_url"] = vault_addr or "http://openbao:8200/v1"
        return OpenBaoSecretProvider(**kwargs)
    if vault_addr:
        # Ensure the detected base_url is injected when the caller did not pass one.
        return OpenBaoSecretProvider(base_url=vault_addr, **kwargs)

    _LOGGER.info("No OpenBao config detected, falling back to in-memory secret provider")
    return InMemorySecretProvider()