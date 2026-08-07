# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Zero-dependency SecretProvider implementation using in-memory storage.

Mirrors the EdgeX SecretProvider interface with StoreSecret, GetSecret,
GetAllSecrets, DeleteSecret.
"""

from __future__ import annotations

from typing import Dict, Optional


class SecretProvider:
    """In-memory secret store compatible with EdgeX SecretProvider interface."""

    def __init__(self) -> None:
        # path -> {key -> value}
        self._secrets: Dict[str, Dict[str, str]] = {}

    def store_secret(self, path: str, key: str, value: str) -> None:
        """Store a secret at the given path and key."""
        if path not in self._secrets:
            self._secrets[path] = {}
        self._secrets[path][key] = value

    def get_secret(self, path: str, key: str) -> str:
        """Retrieve a secret value by path and key. Raises KeyError if not found."""
        if path not in self._secrets or key not in self._secrets[path]:
            raise KeyError(f"secret not found at path '{path}' key '{key}'")
        return self._secrets[path][key]

    def get_all_secrets(self, path: str) -> Dict[str, str]:
        """Retrieve all secrets at the given path. Returns empty dict if path not found."""
        return self._secrets.get(path, {}).copy()

    def delete_secret(self, path: str, key: str) -> None:
        """Delete a secret by path and key. Raises KeyError if not found."""
        if path not in self._secrets or key not in self._secrets[path]:
            raise KeyError(f"secret not found at path '{path}' key '{key}'")
        del self._secrets[path][key]
        if not self._secrets[path]:
            del self._secrets[path]

    # -- additional convenience methods ----------------------------------------

    def has_secret(self, path: str, key: str) -> bool:
        """Check if a secret exists without retrieving it."""
        return path in self._secrets and key in self._secrets[path]

    def list_paths(self) -> list[str]:
        """List all secret paths."""
        return list(self._secrets.keys())

    def delete_path(self, path: str) -> None:
        """Delete all secrets at a path."""
        if path in self._secrets:
            del self._secrets[path]