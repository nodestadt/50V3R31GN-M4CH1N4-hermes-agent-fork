"""Configuration module for the n8n-MCP bridge plugin.

Loads configuration from:
  1. cli-config.yaml (plugins.n8n_mcp section)
  2. Environment variable overrides (N8N_API_KEY, N8N_BASE_URL)
  3. Sensible defaults for Node B (Director's Forge) deployment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_BASE_URL = "http://100.66.173.31:5678"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_VERIFY_SSL = True


@dataclass(frozen=True)
class N8nConfig:
    """Immutable configuration for the n8n-MCP bridge.

    Attributes:
        base_url: n8n instance base URL (e.g. ``http://100.66.173.31:5678``).
        api_key: n8n REST API key. Falls back to the ``N8N_API_KEY`` env var.
        timeout_seconds: HTTP request timeout in seconds.
        max_retries: Maximum number of retry attempts for transient failures.
        retry_delay_seconds: Base delay (in seconds) between retries (uses
            exponential backoff).
        verify_ssl: Whether to verify SSL certificates on HTTPS endpoints.
    """

    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    timeout_seconds: int = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY
    verify_ssl: bool = DEFAULT_VERIFY_SSL

    @classmethod
    def from_dict(cls, data: dict) -> "N8nConfig":
        """Build an ``N8nConfig`` from a YAML-derived dictionary.

        Environment variable overrides are applied after the dict is read:
          - ``N8N_BASE_URL`` overrides ``n8n_base_url``
          - ``N8N_API_KEY`` overrides ``n8n_api_key``

        Args:
            data: Dictionary from the ``plugins.n8n_mcp`` section of
                ``cli-config.yaml``.

        Returns:
            A fully-resolved ``N8nConfig`` instance.
        """
        return cls(
            base_url=os.environ.get("N8N_BASE_URL", data.get("n8n_base_url", DEFAULT_BASE_URL)).rstrip("/"),
            api_key=os.environ.get("N8N_API_KEY", data.get("n8n_api_key", "")),
            timeout_seconds=int(data.get("timeout_seconds", DEFAULT_TIMEOUT)),
            max_retries=int(data.get("max_retries", DEFAULT_MAX_RETRIES)),
            retry_delay_seconds=float(data.get("retry_delay_seconds", DEFAULT_RETRY_DELAY)),
            verify_ssl=bool(data.get("verify_ssl", DEFAULT_VERIFY_SSL)),
        )

    @property
    def api_url(self) -> str:
        """Return the full n8n REST API base URL.

        n8n exposes its REST API at ``<base_url>/api/v1``.
        """
        return f"{self.base_url}/api/v1"

    @property
    def headers(self) -> dict:
        """Return HTTP headers for n8n API requests.

        Includes the ``X-N8N-API-KEY`` header when an API key is configured.
        """
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        return headers
