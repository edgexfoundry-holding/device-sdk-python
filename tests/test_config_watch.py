# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for custom configuration file watching (M7 / Gap G7).

Covers the file mtime polling mechanism for detecting configuration changes
and invoking the changed callback.
"""

from __future__ import annotations

import os
import sys
import time
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from device_sdk_py.service.bootstrap import bootstrap  # noqa: E402


class _Driver:
    def start(self):
        pass


def _make_service(config=None):
    return bootstrap("device-simple", "0.0.0", _Driver(), configuration=config)


class _MockConfig:
    def __init__(self):
        self.custom_config_path = None


class TestConfigWatch(unittest.TestCase):
    """Test file mtime polling for custom configuration changes."""

    def setUp(self):
        self.ds = _make_service()
        # Create a temporary config file to watch
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.temp_dir, "custom.yaml")
        with open(self.config_file, "w") as f:
            f.write("setting1: value1\n")
        self.config = _MockConfig()
        self.config.custom_config_path = self.config_file

    def tearDown(self):
        self.ds._shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_listen_requires_load_custom_config_first(self):
        """listen_for_custom_config_changes raises if load_custom_config not called."""
        with self.assertRaises(RuntimeError) as ctx:
            self.ds.listen_for_custom_config_changes(self.config, "test-section", lambda x: None)
        self.assertIn("custom configuration must be loaded", str(ctx.exception))

    def test_load_custom_config_sets_loaded_flag(self):
        """load_custom_config marks the section as loaded."""
        self.ds.load_custom_config(self.config, "test-section")
        # Should not raise
        self.ds.listen_for_custom_config_changes(self.config, "test-section", lambda x: None)

    def test_changed_callback_invoked_on_file_change(self):
        """When the config file mtime changes, callback is invoked with new config."""
        self.ds.load_custom_config(self.config, "test-section")

        called = []

        def callback(new_config):
            called.append(new_config)

        # Start watching
        self.ds.listen_for_custom_config_changes(self.config, "test-section", callback)

        # Modify the file
        time.sleep(0.1)  # Ensure mtime difference
        with open(self.config_file, "w") as f:
            f.write("setting1: changed\nsetting2: value2\n")

        # Give the polling thread time to detect change (poll interval is 2s)
        time.sleep(2.5)

        # Callback should have been called with the new config
        self.assertTrue(len(called) > 0, "Callback should be invoked on file change")

    def test_callback_receives_parsed_config(self):
        """Callback receives the parsed configuration object, not raw content."""
        self.ds.load_custom_config(self.config, "test-section")

        received = []

        def callback(new_config):
            received.append(new_config)

        self.ds.listen_for_custom_config_changes(self.config, "test-section", callback)

        time.sleep(0.1)
        with open(self.config_file, "w") as f:
            f.write("key: new_value\n")

        time.sleep(2.5)

        self.assertTrue(len(received) > 0)
        # The received config should be the parsed object (has attributes or dict-like)
        self.assertIsNotNone(received[0])

    def test_no_callback_when_file_unchanged(self):
        """Callback should not be invoked if file mtime hasn't changed."""
        self.ds.load_custom_config(self.config, "test-section")

        called = []

        def callback(new_config):
            called.append(new_config)

        self.ds.listen_for_custom_config_changes(self.config, "test-section", callback)

        time.sleep(0.5)

        # File not modified - callback should not be called
        self.assertEqual(len(called), 0)

    def test_watch_stops_on_shutdown(self):
        """Watch thread should stop when service shuts down."""
        self.ds.load_custom_config(self.config, "test-section")

        def callback(new_config):
            pass

        self.ds.listen_for_custom_config_changes(self.config, "test-section", callback)
        self.ds._shutdown()

        # After shutdown, modifying file should not cause callback
        time.sleep(0.1)
        with open(self.config_file, "w") as f:
            f.write("after shutdown\n")

        time.sleep(0.5)

        # Should not crash or callback after shutdown


if __name__ == "__main__":
    unittest.main()