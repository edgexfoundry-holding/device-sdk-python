# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Zero-dependency Logger implementation wrapping stdlib logging.

Mirrors the EdgeX Logger interface (app-functions-sdk-python Logger client)
with Debug, Info, Warn, Error, WithField, WithFields, SetLevel.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional


class Logger:
    """stdlib-backed Logger compatible with EdgeX Logger interface."""

    def __init__(self, name: str, level: int = logging.INFO) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        # Ensure handler exists
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"))
            self._logger.addHandler(handler)
        self._fields: Dict[str, Any] = {}

    # -- core log methods ------------------------------------------------------

    def debug(self, msg: str, *args: Any) -> None:
        self._log(logging.DEBUG, msg, args)

    def info(self, msg: str, *args: Any) -> None:
        self._log(logging.INFO, msg, args)

    def warn(self, msg: str, *args: Any) -> None:
        self._log(logging.WARNING, msg, args)

    def error(self, msg: str, *args: Any) -> None:
        self._log(logging.ERROR, msg, args)

    def _log(self, level: int, msg: str, args: tuple) -> None:
        if args:
            msg = msg % args
        if self._fields:
            extra = " ".join(f"{k}={v}" for k, v in self._fields.items())
            msg = f"{msg} [{extra}]"
        self._logger.log(level, msg)

    # -- field enrichment ------------------------------------------------------

    def with_field(self, key: str, value: Any) -> "Logger":
        """Return a child logger with an additional field."""
        child = Logger(self._logger.name, self._logger.level)
        child._fields = {**self._fields, key: value}
        child._logger = self._logger  # share underlying logger
        return child

    def with_fields(self, fields: Dict[str, Any]) -> "Logger":
        """Return a child logger with additional fields."""
        child = Logger(self._logger.name, self._logger.level)
        child._fields = {**self._fields, **fields}
        child._logger = self._logger
        return child

    # -- level control ---------------------------------------------------------

    def set_level(self, level: str) -> None:
        """Set log level by name (DEBUG, INFO, WARN, ERROR)."""
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARN": logging.WARNING,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
        }
        self._logger.setLevel(level_map.get(level.upper(), logging.INFO))

    # -- compatibility ---------------------------------------------------------

    # Some EdgeX code may call these methods
    def is_debug_enabled(self) -> bool:
        return self._logger.isEnabledFor(logging.DEBUG)

    def is_info_enabled(self) -> bool:
        return self._logger.isEnabledFor(logging.INFO)

    def is_warn_enabled(self) -> bool:
        return self._logger.isEnabledFor(logging.WARNING)

    def is_error_enabled(self) -> bool:
        return self._logger.isEnabledFor(logging.ERROR)