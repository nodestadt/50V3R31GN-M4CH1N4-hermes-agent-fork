"""n8n REST API client for the n8n-MCP bridge plugin.

Provides a synchronous HTTP client wrapping the n8n REST API v1 with:
  - Health check (``/healthz``)
  - List workflows (``GET /api/v1/workflows``)
  - Get workflow by ID (``GET /api/v1/workflows/{id}``)
  - Execute workflow (``POST /api/v1/workflows/{id}/run``)
  - List executions (``GET /api/v1/executions``)
  - Get execution by ID (``GET /api/v1/executions/{id}``)

Uses only the Python standard library (``urllib``) to avoid pulling in
additional dependencies beyond what Hermes already provides.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .n8n_config import N8nConfig

logger = logging.getLogger(__name__)


class N8nError(Exception):
    """Base exception for n8n API errors."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class N8nConnectionError(N8nError):
    """Raised when the n8n instance cannot be reached."""


class N8nApiError(N8nError):
    """Raised when the n8n API returns an error response."""


class N8nClient:
    """Synchronous REST client for n8n API v1.

    All methods perform blocking HTTP I/O and raise ``N8nConnectionError``
    or ``N8nApiError`` on failure.  Transient failures (5xx, network resets)
    are retried up to ``config.max_retries`` times with exponential backoff.
    """

    def __init__(self, config: N8nConfig):
        self._config = config
        self._ssl_ctx = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """Check n8n instance health via ``/healthz``.

        Returns:
            ``True`` if the instance responds with HTTP 200, ``False``
            otherwise.
        """
        url = f"{self._config.base_url}/healthz"
        try:
            self._request("GET", url, retries=0)
            return True
        except (N8nConnectionError, N8nApiError):
            return False

    def list_workflows(self, *, limit: int = 50, cursor: Optional[str] = None) -> Dict[str, Any]:
        """List n8n workflows.

        Args:
            limit: Maximum number of workflows to return (1–100).
            cursor: Pagination cursor from a previous response.

        Returns:
            Dict with ``data`` (list of workflow objects), ``nextCursor``.
        """
        params: Dict[str, str] = {"limit": str(max(1, min(limit, 100)))}
        if cursor:
            params["cursor"] = cursor
        return self._api_get("/workflows", params=params)

    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Retrieve a single workflow by its ID.

        Args:
            workflow_id: The n8n workflow ID.

        Returns:
            Workflow object dict.
        """
        return self._api_get(f"/workflows/{workflow_id}")

    def activate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Activate a workflow so it responds to triggers.

        Args:
            workflow_id: The n8n workflow ID.

        Returns:
            Updated workflow object.
        """
        return self._api_post(f"/workflows/{workflow_id}/activate")

    def deactivate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """Deactivate a workflow.

        Args:
            workflow_id: The n8n workflow ID.

        Returns:
            Updated workflow object.
        """
        return self._api_post(f"/workflows/{workflow_id}/deactivate")

    def execute_workflow(
        self,
        workflow_id: str,
        *,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute an n8n workflow and return the execution result.

        Uses the ``POST /api/v1/workflows/{id}/run`` endpoint which is
        available in n8n ≥ 1.0.  For workflows that require input data,
        pass a ``data`` dict which is forwarded as the ``pinData`` payload.

        Args:
            workflow_id: The n8n workflow ID.
            data: Optional input data forwarded as ``pinData``.

        Returns:
            Execution result dict containing at least ``id`` and ``status``.
        """
        body: Dict[str, Any] = {}
        if data:
            body["pinData"] = data
        return self._api_post(f"/workflows/{workflow_id}/run", body=body or None)

    def list_executions(
        self,
        *,
        limit: int = 20,
        cursor: Optional[str] = None,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List workflow executions.

        Args:
            limit: Maximum executions to return (1–100).
            cursor: Pagination cursor.
            workflow_id: Filter by workflow.
            status: Filter by status (``success``, ``error``, ``waiting``).

        Returns:
            Dict with ``data`` (list of execution objects), ``nextCursor``.
        """
        params: Dict[str, str] = {"limit": str(max(1, min(limit, 100)))}
        if cursor:
            params["cursor"] = cursor
        if workflow_id:
            params["workflowId"] = workflow_id
        if status:
            params["status"] = status
        return self._api_get("/executions", params=params)

    def get_execution(self, execution_id: str) -> Dict[str, Any]:
        """Retrieve a single execution by its ID.

        Args:
            execution_id: The n8n execution ID.

        Returns:
            Execution object dict.
        """
        return self._api_get(f"/executions/{execution_id}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _api_url(self, path: str) -> str:
        """Build a full API URL from a relative path."""
        return f"{self._config.api_url}{path}"

    def _api_get(self, path: str, *, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Perform an authenticated GET to the n8n API."""
        url = self._api_url(path)
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        return self._request("GET", url)

    def _api_post(
        self,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Perform an authenticated POST to the n8n API."""
        url = self._api_url(path)
        data = json.dumps(body).encode("utf-8") if body else None
        return self._request("POST", url, data=data)

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: Optional[bytes] = None,
        retries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute an HTTP request with retry logic.

        Args:
            method: HTTP method (``GET``, ``POST``, etc.).
            url: Full URL.
            data: Request body bytes (for POST/PUT).
            retries: Override ``config.max_retries`` for this call.

        Returns:
            Parsed JSON response body.

        Raises:
            N8nConnectionError: Network-level failure after all retries.
            N8nApiError: HTTP 4xx/5xx response.
        """
        max_attempts = (
            (retries + 1) if retries is not None else (self._config.max_retries + 1)
        )
        last_exc: Optional[Exception] = None

        for attempt in range(max_attempts):
            try:
                req = urllib.request.Request(
                    url,
                    data=data,
                    method=method,
                    headers=self._config.headers,
                )
                timeout = self._config.timeout_seconds

                with urllib.request.urlopen(
                    req,
                    timeout=timeout,
                    context=self._ssl_context(),
                ) as resp:
                    body = resp.read().decode("utf-8")
                    if not body:
                        return {}
                    return json.loads(body)

            except urllib.error.HTTPError as exc:
                body_text = ""
                try:
                    body_text = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    pass

                # Retry on 5xx (server errors)
                if exc.code >= 500 and attempt < max_attempts - 1:
                    last_exc = exc
                    self._backoff(attempt)
                    continue

                raise N8nApiError(
                    f"n8n API error {exc.code}: {body_text[:500]}",
                    status_code=exc.code,
                ) from exc

            except urllib.error.URLError as exc:
                if attempt < max_attempts - 1:
                    last_exc = exc
                    self._backoff(attempt)
                    continue
                raise N8nConnectionError(
                    f"Cannot reach n8n at {url}: {exc.reason}",
                ) from exc

            except Exception as exc:
                if attempt < max_attempts - 1:
                    last_exc = exc
                    self._backoff(attempt)
                    continue
                raise N8nConnectionError(
                    f"n8n request failed: {exc}",
                ) from exc

        # Should not reach here, but just in case
        raise N8nConnectionError(
            f"n8n request failed after {max_attempts} attempts: {last_exc}",
        )

    def _backoff(self, attempt: int) -> None:
        """Sleep with exponential backoff after a failed attempt.

        Caps delay at 30 seconds to avoid impractical waits.
        """
        max_delay = 30.0
        delay = min(self._config.retry_delay_seconds * (2 ** attempt), max_delay)
        logger.debug("Retry %d: waiting %.1fs", attempt + 1, delay)
        time.sleep(delay)

    def _ssl_context(self):
        """Build an SSL context based on configuration (cached)."""
        if self._ssl_ctx is not None:
            return self._ssl_ctx

        import ssl

        ctx = ssl.create_default_context()
        if not self._config.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._ssl_ctx = ctx
        return ctx
