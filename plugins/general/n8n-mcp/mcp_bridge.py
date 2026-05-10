"""MCP tool definitions wrapping n8n operations.

Defines the MCP tools exposed by the n8n-MCP bridge plugin for consumption
by Hermes and external MCP clients:

  - ``n8n_health_check``: Verify n8n instance connectivity.
  - ``n8n_list_workflows``: List available n8n workflows.
  - ``n8n_execute_workflow``: Execute an n8n workflow with parameters.
  - ``n8n_get_execution``: Retrieve execution status and results.

These tools integrate with the Hermes MCP server surface so they appear
in ``hermes tools list`` as callable tools for the agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .n8n_client import N8nClient, N8nError

logger = logging.getLogger(__name__)


class N8nMcpBridge:
    """MCP tool bridge that wraps n8n REST API operations.

    Each public method corresponds to an MCP tool and returns a JSON
    string suitable for the MCP tool response surface.
    """

    def __init__(self, client: N8nClient):
        self._client = client

    # ------------------------------------------------------------------
    # MCP Tool Implementations
    # ------------------------------------------------------------------

    def n8n_health_check(self) -> str:
        """Check n8n instance health.

        Returns JSON with ``healthy: true/false`` and the configured
        endpoint URL.
        """
        try:
            healthy = self._client.health_check()
            return json.dumps({
                "healthy": healthy,
                "endpoint": self._client._config.base_url,
            })
        except Exception as exc:
            return json.dumps({
                "healthy": False,
                "endpoint": self._client._config.base_url,
                "error": str(exc),
            })

    def n8n_list_workflows(
        self,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> str:
        """List available n8n workflows.

        Returns a JSON array of workflow objects with id, name, active
        status, and tags.

        Args:
            limit: Maximum workflows to return (default 50, max 100).
            cursor: Pagination cursor from a previous response.
        """
        try:
            result = self._client.list_workflows(limit=limit, cursor=cursor)
            workflows = result.get("data", [])

            # Summarize each workflow for concise MCP output
            summary = [
                {
                    "id": wf.get("id"),
                    "name": wf.get("name", ""),
                    "active": wf.get("active", False),
                    "tags": [t.get("name", "") for t in wf.get("tags", [])],
                    "updated_at": wf.get("updatedAt", ""),
                }
                for wf in workflows
            ]

            return json.dumps({
                "count": len(summary),
                "next_cursor": result.get("nextCursor"),
                "workflows": summary,
            }, indent=2)
        except N8nError as exc:
            return json.dumps({"error": str(exc), "status_code": exc.status_code})
        except Exception as exc:
            return json.dumps({"error": f"Unexpected error: {exc}"})

    def n8n_execute_workflow(
        self,
        workflow_id: str,
        *,
        data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Execute an n8n workflow with optional input data.

        Triggers a workflow execution and returns the execution metadata.

        Args:
            workflow_id: The n8n workflow ID to execute.
            data: Optional input data forwarded to the workflow.
        """
        if not workflow_id:
            return json.dumps({"error": "workflow_id is required"})

        try:
            result = self._client.execute_workflow(workflow_id, data=data)
            return json.dumps({
                "execution_id": result.get("id"),
                "status": result.get("status", "unknown"),
                "workflow_id": workflow_id,
                "data": result.get("data"),
                "started_at": result.get("startedAt", ""),
                "stopped_at": result.get("stoppedAt", ""),
            }, indent=2)
        except N8nError as exc:
            return json.dumps({
                "error": str(exc),
                "status_code": exc.status_code,
                "workflow_id": workflow_id,
            })
        except Exception as exc:
            return json.dumps({
                "error": f"Unexpected error: {exc}",
                "workflow_id": workflow_id,
            })

    def n8n_get_execution(self, execution_id: str) -> str:
        """Retrieve execution status and results by execution ID.

        Args:
            execution_id: The n8n execution ID.
        """
        if not execution_id:
            return json.dumps({"error": "execution_id is required"})

        try:
            result = self._client.get_execution(execution_id)
            return json.dumps({
                "execution_id": result.get("id"),
                "status": result.get("status", "unknown"),
                "workflow_id": result.get("workflowId"),
                "started_at": result.get("startedAt", ""),
                "stopped_at": result.get("stoppedAt", ""),
                "data": result.get("data"),
            }, indent=2)
        except N8nError as exc:
            return json.dumps({
                "error": str(exc),
                "status_code": exc.status_code,
                "execution_id": execution_id,
            })
        except Exception as exc:
            return json.dumps({
                "error": f"Unexpected error: {exc}",
                "execution_id": execution_id,
            })

    # ------------------------------------------------------------------
    # Tool Registry
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return MCP tool definitions for registration with Hermes.

        Each definition follows the Hermes tool schema:
        ``{"name": ..., "description": ..., "inputSchema": ..., "handler": ...}``
        """
        return [
            {
                "name": "n8n_health_check",
                "description": (
                    "Check the health of the Sovereign n8n instance on Node B "
                    "(Director's Forge). Returns healthy status and endpoint URL."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                "handler": self.n8n_health_check,
            },
            {
                "name": "n8n_list_workflows",
                "description": (
                    "List available n8n workflows on the Sovereign instance. "
                    "Returns workflow IDs, names, active status, and tags."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum workflows to return (default 50)",
                            "default": 50,
                        },
                        "cursor": {
                            "type": "string",
                            "description": "Pagination cursor from previous response",
                        },
                    },
                    "required": [],
                },
                "handler": self.n8n_list_workflows,
            },
            {
                "name": "n8n_execute_workflow",
                "description": (
                    "Execute an n8n workflow by ID with optional input data. "
                    "Returns execution ID, status, and result data."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {
                            "type": "string",
                            "description": "The n8n workflow ID to execute",
                        },
                        "data": {
                            "type": "object",
                            "description": "Optional input data forwarded to the workflow",
                        },
                    },
                    "required": ["workflow_id"],
                },
                "handler": self.n8n_execute_workflow,
            },
            {
                "name": "n8n_get_execution",
                "description": (
                    "Retrieve execution status and results for a specific "
                    "n8n execution by its ID."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "execution_id": {
                            "type": "string",
                            "description": "The n8n execution ID",
                        },
                    },
                    "required": ["execution_id"],
                },
                "handler": self.n8n_get_execution,
            },
        ]
