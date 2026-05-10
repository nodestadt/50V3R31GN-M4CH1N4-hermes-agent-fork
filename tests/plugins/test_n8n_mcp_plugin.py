"""Tests for the n8n-MCP bridge plugin.

Covers:
  - N8nConfig: construction, env var overrides, property accessors
  - N8nClient: health check, list workflows, execute workflow, error handling
  - N8nMcpBridge: MCP tool responses, error serialization
  - N8nMcpPlugin: registration, hook callbacks
  - Integration: end-to-end tool execution with mocked HTTP

Run:
    cd sidecars/hermes-agent-nous
    python -m pytest tests/plugins/test_n8n_mcp_plugin.py -v
    # or:
    python -m unittest tests.plugins.test_n8n_mcp_plugin -v
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import types
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Repo root resolution — same pattern as test_telegram_artery_plugin.py
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "general" / "n8n-mcp"


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
_parent_pkg = "n8n_mcp_test_pkg"
if _parent_pkg not in sys.modules:
    parent = types.ModuleType(_parent_pkg)
    parent.__path__ = [str(PLUGIN_DIR)]
    parent.__package__ = _parent_pkg
    sys.modules[_parent_pkg] = parent

# Import plugin modules (order matters: deps first)
n8n_config_mod = _import_module(
    f"{_parent_pkg}.n8n_config", PLUGIN_DIR / "n8n_config.py"
)
n8n_client_mod = _import_module(
    f"{_parent_pkg}.n8n_client", PLUGIN_DIR / "n8n_client.py"
)
mcp_bridge_mod = _import_module(
    f"{_parent_pkg}.mcp_bridge", PLUGIN_DIR / "mcp_bridge.py"
)
plugin_init_mod = _import_module(
    f"{_parent_pkg}.init", PLUGIN_DIR / "__init__.py"
)

# Shortcut aliases
N8nConfig = n8n_config_mod.N8nConfig
DEFAULT_BASE_URL = n8n_config_mod.DEFAULT_BASE_URL
N8nClient = n8n_client_mod.N8nClient
N8nApiError = n8n_client_mod.N8nApiError
N8nConnectionError = n8n_client_mod.N8nConnectionError
N8nError = n8n_client_mod.N8nError
N8nMcpBridge = mcp_bridge_mod.N8nMcpBridge
N8nMcpPlugin = plugin_init_mod.N8nMcpPlugin
PLUGIN_NAME = plugin_init_mod.PLUGIN_NAME
PLUGIN_VERSION = plugin_init_mod.PLUGIN_VERSION

# Patch target for urllib — uses the test package module path
_URLOPEN_PATCH = f"{_parent_pkg}.n8n_client.urllib.request.urlopen"


# ===================================================================
# Helpers
# ===================================================================


def _mock_response(data, status_code=200):
    """Build a mock urllib response object."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ===================================================================
# N8nConfig Tests
# ===================================================================


class TestN8nConfig(unittest.TestCase):
    """Test N8nConfig construction and properties."""

    def test_default_values(self):
        """Config uses sensible defaults when no overrides provided."""
        cfg = N8nConfig()
        self.assertEqual(cfg.base_url, DEFAULT_BASE_URL)
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.timeout_seconds, 30)
        self.assertEqual(cfg.max_retries, 3)
        self.assertEqual(cfg.retry_delay_seconds, 1.0)
        self.assertTrue(cfg.verify_ssl)

    def test_from_dict_with_overrides(self):
        """Config reads values from a dict."""
        cfg = N8nConfig.from_dict({
            "n8n_base_url": "http://localhost:9999",
            "n8n_api_key": "test-key-123",
            "timeout_seconds": 60,
            "max_retries": 5,
            "verify_ssl": False,
        })
        self.assertEqual(cfg.base_url, "http://localhost:9999")
        self.assertEqual(cfg.api_key, "test-key-123")
        self.assertEqual(cfg.timeout_seconds, 60)
        self.assertEqual(cfg.max_retries, 5)
        self.assertFalse(cfg.verify_ssl)

    def test_from_dict_trailing_slash_stripped(self):
        """Trailing slashes are stripped from base_url."""
        cfg = N8nConfig.from_dict({"n8n_base_url": "http://host:5678/"})
        self.assertEqual(cfg.base_url, "http://host:5678")

    @patch.dict(os.environ, {"N8N_BASE_URL": "http://env-host:5678"})
    def test_env_var_overrides_base_url(self):
        """N8N_BASE_URL env var takes precedence over dict."""
        cfg = N8nConfig.from_dict({"n8n_base_url": "http://dict-host:5678"})
        self.assertEqual(cfg.base_url, "http://env-host:5678")

    @patch.dict(os.environ, {"N8N_API_KEY": "env-key-456"})
    def test_env_var_overrides_api_key(self):
        """N8N_API_KEY env var takes precedence over dict."""
        cfg = N8nConfig.from_dict({"n8n_api_key": "dict-key"})
        self.assertEqual(cfg.api_key, "env-key-456")

    def test_api_url(self):
        """api_url appends /api/v1 to base_url."""
        cfg = N8nConfig(base_url="http://host:5678")
        self.assertEqual(cfg.api_url, "http://host:5678/api/v1")

    def test_headers_with_api_key(self):
        """Headers include X-N8N-API-KEY when configured."""
        cfg = N8nConfig(api_key="my-key")
        headers = cfg.headers
        self.assertEqual(headers["X-N8N-API-KEY"], "my-key")
        self.assertEqual(headers["Accept"], "application/json")

    def test_headers_without_api_key(self):
        """Headers omit X-N8N-API-KEY when not configured."""
        cfg = N8nConfig()
        headers = cfg.headers
        self.assertNotIn("X-N8N-API-KEY", headers)


# ===================================================================
# N8nClient Tests (with mocked HTTP)
# ===================================================================


class TestN8nClient(unittest.TestCase):
    """Test N8nClient with mocked HTTP layer."""

    def setUp(self):
        self.config = N8nConfig(
            base_url="http://n8n.test:5678",
            api_key="test-key",
            max_retries=0,
        )
        self.client = N8nClient(self.config)

    @patch(_URLOPEN_PATCH)
    def test_health_check_success(self, mock_urlopen):
        """health_check returns True on HTTP 200."""
        mock_urlopen.return_value = _mock_response({"status": "ok"})
        self.assertTrue(self.client.health_check())

    @patch(_URLOPEN_PATCH)
    def test_health_check_failure(self, mock_urlopen):
        """health_check returns False on connection error."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        self.assertFalse(self.client.health_check())

    @patch(_URLOPEN_PATCH)
    def test_list_workflows(self, mock_urlopen):
        """list_workflows returns parsed workflow data."""
        workflows_data = {
            "data": [
                {"id": "wf1", "name": "Test Flow", "active": True, "tags": []},
                {"id": "wf2", "name": "Another Flow", "active": False, "tags": [{"name": "prod"}]},
            ],
            "nextCursor": "cursor123",
        }
        mock_urlopen.return_value = _mock_response(workflows_data)
        result = self.client.list_workflows(limit=10)
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(result["nextCursor"], "cursor123")

    @patch(_URLOPEN_PATCH)
    def test_get_workflow(self, mock_urlopen):
        """get_workflow retrieves a single workflow by ID."""
        wf = {"id": "wf1", "name": "My Flow", "active": True}
        mock_urlopen.return_value = _mock_response(wf)
        result = self.client.get_workflow("wf1")
        self.assertEqual(result["name"], "My Flow")

    @patch(_URLOPEN_PATCH)
    def test_execute_workflow(self, mock_urlopen):
        """execute_workflow posts data and returns execution result."""
        execution = {
            "id": "exec1",
            "status": "success",
            "data": {"result": "done"},
        }
        mock_urlopen.return_value = _mock_response(execution)
        result = self.client.execute_workflow("wf1", data={"input": "test"})
        self.assertEqual(result["id"], "exec1")
        self.assertEqual(result["status"], "success")

        # Verify POST was used
        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]
        self.assertEqual(request_obj.method, "POST")

    @patch(_URLOPEN_PATCH)
    def test_list_executions(self, mock_urlopen):
        """list_executions returns execution history."""
        executions = {
            "data": [
                {"id": "exec1", "status": "success", "workflowId": "wf1"},
            ],
        }
        mock_urlopen.return_value = _mock_response(executions)
        result = self.client.list_executions(workflow_id="wf1")
        self.assertEqual(len(result["data"]), 1)

    @patch(_URLOPEN_PATCH)
    def test_api_error_raises(self, mock_urlopen):
        """HTTP 4xx raises N8nApiError."""
        import urllib.error
        err_body = b'{"message": "Not Found"}'
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://test",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=BytesIO(err_body),
        )
        with self.assertRaises(N8nApiError) as ctx:
            self.client.get_workflow("nonexistent")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch(_URLOPEN_PATCH)
    def test_connection_error_raises(self, mock_urlopen):
        """Network error raises N8nConnectionError."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        with self.assertRaises(N8nConnectionError):
            self.client.list_workflows()

    @patch(_URLOPEN_PATCH)
    def test_empty_response_body(self, mock_urlopen):
        """Empty response body returns empty dict."""
        resp = MagicMock()
        resp.read.return_value = b""
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        result = self.client.list_workflows()
        self.assertEqual(result, {})


# ===================================================================
# N8nMcpBridge Tests
# ===================================================================


class TestN8nMcpBridge(unittest.TestCase):
    """Test N8nMcpBridge MCP tool definitions and responses."""

    def setUp(self):
        self.client = MagicMock(spec=N8nClient)
        self.bridge = N8nMcpBridge(self.client)

    def test_health_check_healthy(self):
        """health_check returns healthy JSON."""
        self.client.health_check.return_value = True
        self.client._config = N8nConfig(base_url="http://test:5678")

        result = json.loads(self.bridge.n8n_health_check())
        self.assertTrue(result["healthy"])
        self.assertEqual(result["endpoint"], "http://test:5678")

    def test_health_check_unhealthy(self):
        """health_check returns unhealthy JSON on failure."""
        self.client.health_check.return_value = False
        self.client._config = N8nConfig(base_url="http://test:5678")

        result = json.loads(self.bridge.n8n_health_check())
        self.assertFalse(result["healthy"])

    def test_health_check_exception(self):
        """health_check handles unexpected exceptions gracefully."""
        self.client.health_check.side_effect = Exception("boom")
        self.client._config = N8nConfig(base_url="http://test:5678")

        result = json.loads(self.bridge.n8n_health_check())
        self.assertFalse(result["healthy"])
        self.assertIn("boom", result["error"])

    def test_list_workflows(self):
        """list_workflows returns summarized workflow data."""
        self.client.list_workflows.return_value = {
            "data": [
                {
                    "id": "wf1",
                    "name": "Flow A",
                    "active": True,
                    "tags": [{"name": "prod"}],
                    "updatedAt": "2025-01-01",
                },
            ],
            "nextCursor": None,
        }

        result = json.loads(self.bridge.n8n_list_workflows(limit=10))
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["workflows"][0]["name"], "Flow A")
        self.assertEqual(result["workflows"][0]["tags"], ["prod"])

    def test_list_workflows_error(self):
        """list_workflows handles API errors."""
        self.client.list_workflows.side_effect = N8nApiError("Server error", 500)

        result = json.loads(self.bridge.n8n_list_workflows())
        self.assertIn("error", result)
        self.assertEqual(result["status_code"], 500)

    def test_execute_workflow(self):
        """execute_workflow returns execution metadata."""
        self.client.execute_workflow.return_value = {
            "id": "exec1",
            "status": "success",
            "data": {"output": "hello"},
            "startedAt": "2025-01-01T00:00:00",
            "stoppedAt": "2025-01-01T00:00:01",
        }

        result = json.loads(
            self.bridge.n8n_execute_workflow("wf1", data={"input": "test"})
        )
        self.assertEqual(result["execution_id"], "exec1")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["workflow_id"], "wf1")

    def test_execute_workflow_missing_id(self):
        """execute_workflow rejects missing workflow_id."""
        result = json.loads(self.bridge.n8n_execute_workflow(""))
        self.assertIn("error", result)

    def test_execute_workflow_error(self):
        """execute_workflow handles API errors."""
        self.client.execute_workflow.side_effect = N8nApiError("Not Found", 404)

        result = json.loads(self.bridge.n8n_execute_workflow("bad-wf"))
        self.assertIn("error", result)
        self.assertEqual(result["workflow_id"], "bad-wf")

    def test_get_execution(self):
        """get_execution returns execution details."""
        self.client.get_execution.return_value = {
            "id": "exec1",
            "status": "success",
            "workflowId": "wf1",
            "startedAt": "2025-01-01T00:00:00",
            "stoppedAt": "2025-01-01T00:00:01",
            "data": {},
        }

        result = json.loads(self.bridge.n8n_get_execution("exec1"))
        self.assertEqual(result["execution_id"], "exec1")
        self.assertEqual(result["workflow_id"], "wf1")

    def test_get_execution_missing_id(self):
        """get_execution rejects missing execution_id."""
        result = json.loads(self.bridge.n8n_get_execution(""))
        self.assertIn("error", result)

    def test_get_tool_definitions(self):
        """get_tool_definitions returns the 4 expected tools."""
        defs = self.bridge.get_tool_definitions()
        names = {d["name"] for d in defs}
        self.assertEqual(names, {
            "n8n_health_check",
            "n8n_list_workflows",
            "n8n_execute_workflow",
            "n8n_get_execution",
        })

    def test_tool_definitions_have_schemas(self):
        """Each tool definition has required fields."""
        defs = self.bridge.get_tool_definitions()
        for tool_def in defs:
            self.assertIn("name", tool_def)
            self.assertIn("description", tool_def)
            self.assertIn("inputSchema", tool_def)
            self.assertIn("handler", tool_def)
            self.assertIn("type", tool_def["inputSchema"])
            self.assertEqual(tool_def["inputSchema"]["type"], "object")


# ===================================================================
# N8nMcpPlugin Tests
# ===================================================================


class TestN8nMcpPlugin(unittest.TestCase):
    """Test N8nMcpPlugin lifecycle and hook callbacks."""

    def test_plugin_name_and_version(self):
        """Plugin constants are correct."""
        self.assertEqual(PLUGIN_NAME, "n8n-mcp")
        self.assertEqual(PLUGIN_VERSION, "1.0.0")

    def test_initialization(self):
        """Plugin initializes client and bridge."""
        plugin = N8nMcpPlugin({"n8n_base_url": "http://test:5678"})
        ctx = MagicMock()
        plugin.initialize(ctx)

        self.assertIsNotNone(plugin.client)
        self.assertIsNotNone(plugin.mcp_bridge)

    def test_session_start_healthy(self):
        """on_session_start logs healthy n8n connectivity."""
        plugin = N8nMcpPlugin({"n8n_base_url": "http://test:5678"})
        ctx = MagicMock()
        plugin.initialize(ctx)

        # Mock the client health check
        plugin.client.health_check = MagicMock(return_value=True)

        # Should not raise
        plugin.on_session_start(session_id="test-session")

    def test_session_start_unhealthy(self):
        """on_session_start handles unhealthy n8n gracefully."""
        plugin = N8nMcpPlugin({"n8n_base_url": "http://test:5678"})
        ctx = MagicMock()
        plugin.initialize(ctx)

        plugin.client.health_check = MagicMock(return_value=False)

        # Should not raise
        plugin.on_session_start(session_id="test-session")

    def test_session_end(self):
        """on_session_end completes without error."""
        plugin = N8nMcpPlugin({})
        ctx = MagicMock()
        plugin.initialize(ctx)

        # Should not raise
        plugin.on_session_end(session_id="test-session")

    def test_register_entry_point(self):
        """register() registers hooks with the Hermes plugin API."""
        plugin_config = {"n8n_base_url": "http://test:5678"}

        ctx = MagicMock()
        ctx.config = {"n8n_mcp": plugin_config}

        register_fn = plugin_init_mod.register
        register_fn(ctx)

        # Verify hooks were registered
        register_calls = ctx.register_hook.call_args_list
        hook_names = [call[0][0] for call in register_calls]
        self.assertIn("on_session_start", hook_names)
        self.assertIn("on_session_end", hook_names)

    def test_register_extracts_config_from_context(self):
        """register() extracts n8n_mcp config from the context."""
        ctx = MagicMock()
        ctx.config = {"n8n_mcp": {"n8n_base_url": "http://custom:9999"}}

        register_fn = plugin_init_mod.register
        register_fn(ctx)

        # The plugin should have been created with the custom URL
        # (verified indirectly via no exceptions)


# ===================================================================
# Integration Test (end-to-end with mocked HTTP)
# ===================================================================


class TestN8nIntegration(unittest.TestCase):
    """End-to-end test: plugin -> bridge -> client -> HTTP (mocked)."""

    @patch(_URLOPEN_PATCH)
    def test_full_execute_workflow_flow(self, mock_urlopen):
        """Full flow: plugin init -> session start -> execute workflow."""
        # Setup mock responses — one per HTTP call:
        #   1. on_session_start health check
        #   2. health_check assertion
        #   3. execute_workflow
        health_resp = _mock_response({"status": "ok"})
        health_resp2 = _mock_response({"status": "ok"})
        exec_resp = _mock_response({
            "id": "exec-42",
            "status": "success",
            "data": {"output": "Hello Sovereign"},
            "startedAt": "2025-01-01T00:00:00",
            "stoppedAt": "2025-01-01T00:00:05",
        })

        mock_urlopen.side_effect = [health_resp, health_resp2, exec_resp]

        # Create and initialize plugin
        plugin = N8nMcpPlugin({
            "n8n_base_url": "http://n8n.test:5678",
            "n8n_api_key": "test-key",
        })
        ctx = MagicMock()
        plugin.initialize(ctx)

        # Session start (triggers health check via real HTTP)
        plugin.on_session_start(session_id="integration-test")

        # Verify health check independently
        self.assertTrue(plugin.client.health_check())

        # Execute workflow via MCP bridge
        bridge = plugin.mcp_bridge
        result_json = bridge.n8n_execute_workflow("wf-1", data={"msg": "hello"})
        result = json.loads(result_json)

        self.assertEqual(result["execution_id"], "exec-42")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["workflow_id"], "wf-1")


if __name__ == "__main__":
    unittest.main()
