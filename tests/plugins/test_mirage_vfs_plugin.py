"""Tests for the Mirage VFS bridge plugin.

Covers:
  - MirageVfsConfig: construction, env var overrides, defaults
  - VfsBridge: tool definitions, health check, read/write/list operations
  - MirageVfsPlugin: registration, hook callbacks
  - Integration: end-to-end tool execution with simulated FUSE mount

Run:
    cd sidecars/hermes-agent-nous
    python -m pytest tests/plugins/test_mirage_vfs_plugin.py -v
    # or:
    python -m unittest tests.plugins.test_mirage_vfs_plugin -v
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "general" / "mirage-vfs"


def _import_module(name: str, path: Path):
    """Import a single module from an absolute path with proper __module__."""
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__module__ = name
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Ensure parent package exists for relative imports in __init__.py
_parent_pkg = "mirage_vfs_test_pkg"
if _parent_pkg not in sys.modules:
    parent = types.ModuleType(_parent_pkg)
    parent.__path__ = [str(PLUGIN_DIR)]
    parent.__package__ = _parent_pkg
    sys.modules[_parent_pkg] = parent

# Import plugin modules (order matters: deps first)
mirage_config_mod = _import_module(
    f"{_parent_pkg}.mirage_config", PLUGIN_DIR / "mirage_config.py"
)
vfs_bridge_mod = _import_module(
    f"{_parent_pkg}.vfs_bridge", PLUGIN_DIR / "vfs_bridge.py"
)
plugin_init_mod = _import_module(
    f"{_parent_pkg}.init", PLUGIN_DIR / "__init__.py"
)

# Shortcut aliases
MirageVfsConfig = mirage_config_mod.MirageVfsConfig
VfsBridge = vfs_bridge_mod.VfsBridge
MirageVfsPlugin = plugin_init_mod.MirageVfsPlugin
PLUGIN_NAME = plugin_init_mod.PLUGIN_NAME
PLUGIN_VERSION = plugin_init_mod.PLUGIN_VERSION


# ===================================================================
# MirageVfsConfig Tests
# ===================================================================


class TestMirageVfsConfig(unittest.TestCase):
    """Test MirageVfsConfig construction and defaults."""

    def test_default_values(self):
        """Config uses sensible defaults when no overrides provided."""
        cfg = MirageVfsConfig()
        self.assertEqual(cfg.mount_point, "/mnt/mirage")
        self.assertEqual(cfg.redis_host, "100.96.253.114")
        self.assertEqual(cfg.redis_port, 6379)
        self.assertEqual(cfg.s3_endpoint, "http://100.96.253.114:9000")
        self.assertEqual(cfg.s3_bucket, "sovereign-mirage")
        self.assertTrue(cfg.health_check_on_start)

    def test_from_dict_with_overrides(self):
        """Config reads values from a dict."""
        cfg = MirageVfsConfig.from_dict({
            "mount_point": "/tmp/test-mirage",
            "redis_host": "10.0.0.1",
            "redis_port": 6380,
            "s3_bucket": "test-bucket",
            "health_check_on_start": False,
        })
        self.assertEqual(cfg.mount_point, "/tmp/test-mirage")
        self.assertEqual(cfg.redis_host, "10.0.0.1")
        self.assertEqual(cfg.redis_port, 6380)
        self.assertEqual(cfg.s3_bucket, "test-bucket")
        self.assertFalse(cfg.health_check_on_start)

    @patch.dict(os.environ, {"MIRAGE_MOUNT_POINT": "/opt/mirage"})
    def test_env_var_overrides_mount_point(self):
        """MIRAGE_MOUNT_POINT env var takes precedence over dict."""
        cfg = MirageVfsConfig.from_dict({"mount_point": "/mnt/default"})
        self.assertEqual(cfg.mount_point, "/opt/mirage")

    @patch.dict(os.environ, {"MIRAGE_REDIS_HOST": "192.168.1.1"})
    def test_env_var_overrides_redis_host(self):
        """MIRAGE_REDIS_HOST env var takes precedence over dict."""
        cfg = MirageVfsConfig.from_dict({"redis_host": "10.0.0.1"})
        self.assertEqual(cfg.redis_host, "192.168.1.1")

    @patch.dict(os.environ, {"MIRAGE_S3_BUCKET": "env-bucket"})
    def test_env_var_overrides_s3_bucket(self):
        """MIRAGE_S3_BUCKET env var takes precedence over dict."""
        cfg = MirageVfsConfig.from_dict({"s3_bucket": "dict-bucket"})
        self.assertEqual(cfg.s3_bucket, "env-bucket")

    @patch.dict(os.environ, {"MIRAGE_REDIS_PORT": "6380"})
    def test_env_var_overrides_redis_port(self):
        """MIRAGE_REDIS_PORT env var takes precedence over dict."""
        cfg = MirageVfsConfig.from_dict({"redis_port": 6379})
        self.assertEqual(cfg.redis_port, 6380)

    def test_from_dict_empty_uses_defaults(self):
        """Empty dict falls back to defaults."""
        cfg = MirageVfsConfig.from_dict({})
        self.assertEqual(cfg.mount_point, "/mnt/mirage")
        self.assertEqual(cfg.redis_host, "100.96.253.114")
        self.assertEqual(cfg.redis_port, 6379)

    def test_config_is_frozen(self):
        """Config is immutable (frozen dataclass)."""
        cfg = MirageVfsConfig()
        with self.assertRaises(AttributeError):
            cfg.mount_point = "/other"


# ===================================================================
# VfsBridge Tests
# ===================================================================


class TestVfsBridgeToolDefinitions(unittest.TestCase):
    """Test VfsBridge tool definitions."""

    def setUp(self):
        self.config = MirageVfsConfig(mount_point="/tmp/test-mirage-nonexistent")
        self.bridge = VfsBridge(self.config)

    def test_tool_definitions_count(self):
        """Bridge exposes 4 tools."""
        defs = self.bridge.get_tool_definitions()
        self.assertEqual(len(defs), 4)

    def test_tool_names(self):
        """Tool names match expected set."""
        defs = self.bridge.get_tool_definitions()
        names = {d["name"] for d in defs}
        self.assertEqual(
            names,
            {"mirage_health_check", "mirage_read_file", "mirage_list_dir", "mirage_write_file"},
        )

    def test_each_tool_has_required_fields(self):
        """Each tool definition has name, inputSchema, and handler."""
        for d in self.bridge.get_tool_definitions():
            self.assertIn("name", d)
            self.assertIn("inputSchema", d)
            self.assertIn("handler", d)
            self.assertTrue(callable(d["handler"]))


class TestVfsBridgeHealthCheck(unittest.TestCase):
    """Test VfsBridge health check with real temp directory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = MirageVfsConfig(mount_point=self.tmpdir)
        self.bridge = VfsBridge(self.config)

    def test_health_check_unmounted(self):
        """Health check returns mounted=False for regular directory."""
        result = self.bridge._handle_health_check()
        self.assertFalse(result["mounted"])
        self.assertEqual(result["mount_point"], self.tmpdir)
        self.assertEqual(result["redis_host"], "100.96.253.114")

    def test_health_check_nonexistent_mount(self):
        """Health check returns error for nonexistent mount point."""
        config = MirageVfsConfig(mount_point="/tmp/mirage-definitely-does-not-exist-xyz")
        bridge = VfsBridge(config)
        result = bridge._handle_health_check()
        self.assertIn("error", result)
        self.assertFalse(result["mounted"])

    def test_health_check_lists_entries(self):
        """Health check lists files when mount has content."""
        # Create test files in temp dir
        Path(self.tmpdir, "test-file.txt").write_text("hello")
        Path(self.tmpdir, "test-dir").mkdir()

        result = self.bridge._handle_health_check()
        # Even though not "mounted" via FUSE, entries should be listed
        entry_names = [e["name"] for e in result["entries"]]
        self.assertIn("test-file.txt", entry_names)
        self.assertIn("test-dir", entry_names)


class TestVfsBridgeReadFile(unittest.TestCase):
    """Test VfsBridge read_file with real temp directory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = MirageVfsConfig(mount_point=self.tmpdir)
        self.bridge = VfsBridge(self.config)

    def test_read_existing_file(self):
        """Read returns content for an existing file."""
        Path(self.tmpdir, "hello.txt").write_text("sovereign mesh", encoding="utf-8")
        result = self.bridge._handle_read_file(path="hello.txt")
        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "sovereign mesh")
        self.assertEqual(result["path"], "hello.txt")

    def test_read_nonexistent_file(self):
        """Read returns error for nonexistent file."""
        result = self.bridge._handle_read_file(path="does-not-exist.txt")
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])

    def test_read_nested_file(self):
        """Read handles nested paths."""
        subdir = Path(self.tmpdir, "redis", "cache")
        subdir.mkdir(parents=True)
        (subdir / "session.json").write_text('{"key": "value"}', encoding="utf-8")

        result = self.bridge._handle_read_file(path="redis/cache/session.json")
        self.assertTrue(result["success"])
        self.assertEqual(result["content"], '{"key": "value"}')


class TestVfsBridgeWriteFile(unittest.TestCase):
    """Test VfsBridge write_file with real temp directory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = MirageVfsConfig(mount_point=self.tmpdir)
        self.bridge = VfsBridge(self.config)

    def test_write_new_file(self):
        """Write creates a new file."""
        result = self.bridge._handle_write_file(
            path="output.txt", content="written data"
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["bytes_written"], 12)

        # Verify file exists
        content = Path(self.tmpdir, "output.txt").read_text()
        self.assertEqual(content, "written data")

    def test_write_creates_parent_dirs(self):
        """Write creates intermediate directories."""
        result = self.bridge._handle_write_file(
            path="deep/nested/dir/file.txt", content="deep write"
        )
        self.assertTrue(result["success"])

        content = Path(self.tmpdir, "deep", "nested", "dir", "file.txt").read_text()
        self.assertEqual(content, "deep write")

    def test_write_overwrites_existing(self):
        """Write overwrites an existing file."""
        Path(self.tmpdir, "existing.txt").write_text("old content")
        result = self.bridge._handle_write_file(
            path="existing.txt", content="new content"
        )
        self.assertTrue(result["success"])

        content = Path(self.tmpdir, "existing.txt").read_text()
        self.assertEqual(content, "new content")

    def test_write_empty_path_fails(self):
        """Write with empty path returns error."""
        result = self.bridge._handle_write_file(path="", content="data")
        self.assertFalse(result["success"])
        self.assertIn("required", result["error"])


class TestVfsBridgeListDir(unittest.TestCase):
    """Test VfsBridge list_dir with real temp directory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = MirageVfsConfig(mount_point=self.tmpdir)
        self.bridge = VfsBridge(self.config)

    def test_list_empty_dir(self):
        """List empty directory returns empty entries."""
        result = self.bridge._handle_list_dir(path="")
        self.assertTrue(result["success"])
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["count"], 0)

    def test_list_with_files_and_dirs(self):
        """List returns both files and directories."""
        Path(self.tmpdir, "file1.txt").write_text("a")
        Path(self.tmpdir, "file2.json").write_text("{}")
        Path(self.tmpdir, "subdir").mkdir()

        result = self.bridge._handle_list_dir(path="")
        self.assertTrue(result["success"])
        names = [e["name"] for e in result["entries"]]
        self.assertIn("file1.txt", names)
        self.assertIn("file2.json", names)
        self.assertIn("subdir", names)
        self.assertEqual(result["count"], 3)

    def test_list_subdirectory(self):
        """List returns contents of a subdirectory."""
        subdir = Path(self.tmpdir, "s3")
        subdir.mkdir()
        (subdir / "data.csv").write_text("a,b,c")

        result = self.bridge._handle_list_dir(path="s3")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["name"], "data.csv")

    def test_list_nonexistent_dir(self):
        """List returns error for nonexistent directory."""
        result = self.bridge._handle_list_dir(path="no-such-dir")
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])

    def test_list_file_as_dir(self):
        """List returns error when path is a file, not directory."""
        Path(self.tmpdir, "not-a-dir.txt").write_text("data")
        result = self.bridge._handle_list_dir(path="not-a-dir.txt")
        self.assertFalse(result["success"])
        self.assertIn("Not a directory", result["error"])

    def test_list_dir_entry_metadata(self):
        """List entries include is_dir and size_bytes for files."""
        Path(self.tmpdir, "sized.txt").write_text("hello world")
        Path(self.tmpdir, "a_dir").mkdir()

        result = self.bridge._handle_list_dir(path="")
        entries_by_name = {e["name"]: e for e in result["entries"]}

        self.assertTrue(entries_by_name["a_dir"]["is_dir"])
        self.assertFalse(entries_by_name["sized.txt"]["is_dir"])
        self.assertEqual(entries_by_name["sized.txt"]["size_bytes"], 11)


# ===================================================================
# MirageVfsPlugin Tests
# ===================================================================


class TestMirageVfsPlugin(unittest.TestCase):
    """Test MirageVfsPlugin lifecycle and registration."""

    def test_plugin_init(self):
        """Plugin initializes with config."""
        plugin = MirageVfsPlugin({"mount_point": "/tmp/test"})
        self.assertEqual(plugin.config.mount_point, "/tmp/test")
        self.assertFalse(plugin._initialized)

    def test_plugin_initialize(self):
        """Plugin initializes creates a VfsBridge."""
        plugin = MirageVfsPlugin({})
        mock_api = MagicMock()
        plugin.initialize(mock_api)
        self.assertTrue(plugin._initialized)
        self.assertIsNotNone(plugin.bridge)

    def test_plugin_on_session_start_health_check(self):
        """on_session_start runs health check when enabled."""
        tmpdir = tempfile.mkdtemp()
        plugin = MirageVfsPlugin({
            "mount_point": tmpdir,
            "health_check_on_start": True,
        })
        mock_api = MagicMock()
        plugin.initialize(mock_api)

        # Should not raise
        plugin.on_session_start(session_id="test-session")

    def test_plugin_on_session_start_skip(self):
        """on_session_start skips when health_check_on_start is False."""
        plugin = MirageVfsPlugin({"health_check_on_start": False})
        mock_api = MagicMock()
        plugin.initialize(mock_api)

        # Should not raise even with nonexistent mount
        plugin.on_session_start(session_id="test-session")

    def test_plugin_on_session_start_no_bridge(self):
        """on_session_start handles uninitialized bridge gracefully."""
        plugin = MirageVfsPlugin({})
        # Don't initialize — bridge is None
        plugin.on_session_start(session_id="test-session")

    def test_register(self):
        """register() creates plugin, registers tools and hooks."""
        ctx = MagicMock()
        ctx.config = {"mirage_vfs": {"mount_point": "/tmp/test-reg"}}

        plugin_init_mod.register(ctx)

        # Should have registered 4 tools
        self.assertEqual(ctx.register_tool.call_count, 4)

        # Should have registered on_session_start hook
        ctx.register_hook.assert_called_once()
        hook_call = ctx.register_hook.call_args
        self.assertEqual(hook_call[0][0], "on_session_start")

    def test_register_tool_names(self):
        """register() registers all 4 expected tools."""
        ctx = MagicMock()
        ctx.config = {"mirage_vfs": {}}

        plugin_init_mod.register(ctx)

        registered_names = {
            call.kwargs.get("name") or call.args[0]
            for call in ctx.register_tool.call_args_list
        }
        self.assertEqual(
            registered_names,
            {"mirage_health_check", "mirage_read_file", "mirage_list_dir", "mirage_write_file"},
        )

    def test_register_config_from_dict_attribute(self):
        """register() handles config as object with mirage_vfs attribute."""
        ctx = MagicMock()
        # Simulate ctx.config being an object with mirage_vfs attribute
        ctx.config = MagicMock()
        ctx.config.get = MagicMock(return_value={"mount_point": "/opt/mirage"})
        # Also handle isinstance check for dict — it will fail, so it falls to getattr
        ctx.config.__class__ = type("Config", (), {"get": ctx.config.get})

        plugin_init_mod.register(ctx)
        self.assertEqual(ctx.register_tool.call_count, 4)


# ===================================================================
# Integration Tests
# ===================================================================


class TestVfsBridgeIntegration(unittest.TestCase):
    """End-to-end tests simulating FUSE-backed file operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = MirageVfsConfig(mount_point=self.tmpdir)
        self.bridge = VfsBridge(self.config)

    def test_write_then_read_roundtrip(self):
        """Write a file, then read it back — content preserved."""
        self.bridge._handle_write_file(
            path="mesh/state.json", content='{"status": "sovereign"}'
        )
        result = self.bridge._handle_read_file(path="mesh/state.json")
        self.assertTrue(result["success"])
        self.assertEqual(result["content"], '{"status": "sovereign"}')

    def test_write_then_list_then_read(self):
        """Write multiple files, list directory, read each file."""
        self.bridge._handle_write_file(path="redis/key1.txt", content="value1")
        self.bridge._handle_write_file(path="redis/key2.txt", content="value2")
        self.bridge._handle_write_file(path="s3/config.yaml", content="key: val")

        # List root
        root = self.bridge._handle_list_dir(path="")
        self.assertTrue(root["success"])
        root_names = {e["name"] for e in root["entries"]}
        self.assertIn("redis", root_names)
        self.assertIn("s3", root_names)

        # List redis subdir
        redis = self.bridge._handle_list_dir(path="redis")
        self.assertTrue(redis["success"])
        self.assertEqual(redis["count"], 2)

        # Read individual files
        r1 = self.bridge._handle_read_file(path="redis/key1.txt")
        self.assertEqual(r1["content"], "value1")

        r2 = self.bridge._handle_read_file(path="redis/key2.txt")
        self.assertEqual(r2["content"], "value2")

    def test_full_plugin_lifecycle(self):
        """Simulate full plugin lifecycle: register, start, tools, end."""
        ctx = MagicMock()
        ctx.config = {"mirage_vfs": {"mount_point": self.tmpdir}}

        # Register
        plugin_init_mod.register(ctx)
        self.assertEqual(ctx.register_tool.call_count, 4)

        # Simulate session start (health check)
        # The registered hook callback is in ctx.register_hook calls
        hook_fn = ctx.register_hook.call_args[0][1]
        hook_fn(session_id="lifecycle-test")  # Should not raise

    def test_tool_handler_direct_invocation(self):
        """Invoke each tool handler directly to verify response schema."""
        # Health check
        health = self.bridge._handle_health_check()
        self.assertIn("mounted", health)
        self.assertIn("mount_point", health)
        self.assertIn("entries", health)
        self.assertIn("redis_host", health)
        self.assertIn("s3_bucket", health)

        # Write
        write = self.bridge._handle_write_file(path="test.txt", content="abc")
        self.assertIn("success", write)
        self.assertTrue(write["success"])

        # Read
        read = self.bridge._handle_read_file(path="test.txt")
        self.assertTrue(read["success"])
        self.assertEqual(read["content"], "abc")

        # List
        listing = self.bridge._handle_list_dir(path="")
        self.assertTrue(listing["success"])
        self.assertIn("entries", listing)
        self.assertIn("count", listing)


if __name__ == "__main__":
    unittest.main()
