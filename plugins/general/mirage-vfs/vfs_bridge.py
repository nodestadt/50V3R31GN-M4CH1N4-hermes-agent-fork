"""VFS Bridge — Hermes tool definitions for Mirage VFS access.

Provides Hermes agent tools that transparently read and write files
backed by Redis (Node A) and S3-compatible storage through the
Mirage FUSE-mounted virtualized filesystem.

Tools exposed:
  - ``mirage_health_check``: Verify VFS mount is active and responsive.
  - ``mirage_read_file``: Read a virtual file (transparently from Redis/S3).
  - ``mirage_list_dir``: List virtual directory contents.
  - ``mirage_write_file``: Write to a virtual file (persists to backend).
"""

from __future__ import annotations

import base64
import logging
import subprocess
from pathlib import Path
from typing import Any

from .mirage_config import MirageVfsConfig

logger = logging.getLogger(__name__)


class VfsBridge:
    """Bridge between Hermes agent tools and the Mirage VFS mount.

    All file operations go through the FUSE mount point, providing
    transparent access to Redis and S3 backends.
    """

    def __init__(self, config: MirageVfsConfig):
        self._config = config
        self._mount_point = Path(config.mount_point)

    # ------------------------------------------------------------------
    # Tool definitions for Hermes registration
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict]:
        """Return tool definitions for Hermes plugin registration.

        Each definition includes:
          - ``name``: Tool name used by the agent.
          - ``inputSchema``: JSON Schema for tool parameters.
          - ``handler``: Callable that executes the tool logic.
        """
        return [
            {
                "name": "mirage_health_check",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "description": (
                        "Check Mirage VFS mount status. Returns mount point, "
                        "whether the filesystem is active, and lists top-level "
                        "entries if mounted."
                    ),
                },
                "handler": self._handle_health_check,
            },
            {
                "name": "mirage_read_file",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "File path relative to the Mirage mount point. "
                                "Example: 'redis/session-context.json' or "
                                "'s3/mesh-config.yaml'."
                            ),
                        },
                    },
                    "required": ["path"],
                    "description": (
                        "Read a virtual file from the Mirage VFS. The file is "
                        "transparently fetched from the backend (Redis or S3)."
                    ),
                },
                "handler": self._handle_read_file,
            },
            {
                "name": "mirage_list_dir",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory path relative to the Mirage mount "
                                "point. Use '' or '/' for the root. "
                                "Default: root."
                            ),
                            "default": "",
                        },
                    },
                    "description": (
                        "List contents of a virtual directory in the Mirage VFS. "
                        "Returns filenames and subdirectories."
                    ),
                },
                "handler": self._handle_list_dir,
            },
            {
                "name": "mirage_write_file",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "File path relative to the Mirage mount point."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to write to the virtual file.",
                        },
                    },
                    "required": ["path", "content"],
                    "description": (
                        "Write content to a virtual file in the Mirage VFS. "
                        "Data persists to the backend (Redis or S3)."
                    ),
                },
                "handler": self._handle_write_file,
            },
        ]

    # ------------------------------------------------------------------
    # Path validation
    # ------------------------------------------------------------------

    def _validate_path(self, rel_path: str) -> Path:
        """Validate that a relative path does not escape the mount point.

        Resolves the path and verifies it stays within the FUSE mount.
        Raises ValueError if path traversal is detected.
        """
        mount_resolved = self._mount_point.resolve()
        full_path = (self._mount_point / rel_path).resolve()

        if not str(full_path).startswith(str(mount_resolved)):
            raise ValueError(
                f"Path traversal blocked: '{rel_path}' escapes mount point "
                f"'{self._mount_point}'"
            )

        return full_path

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def _handle_health_check(self, **kwargs) -> dict[str, Any]:
        """Handler for ``mirage_health_check`` tool.

        Returns:
            Dict with mount status, backend info, and directory listing.
        """
        result: dict[str, Any] = {
            "mount_point": str(self._mount_point),
            "mounted": False,
            "entries": [],
            "redis_host": self._config.redis_host,
            "redis_port": self._config.redis_port,
            "s3_endpoint": self._config.s3_endpoint,
            "s3_bucket": self._config.s3_bucket,
        }

        try:
            if not self._mount_point.exists():
                result["error"] = f"Mount point {self._mount_point} does not exist"
                return result

            # Check if mount point is a FUSE mount
            result["mounted"] = self._is_mounted()

            # List entries regardless of FUSE mount status — useful for
            # both live FUSE mounts and simulated/testing environments.
            entries = []
            for entry in self._mount_point.iterdir():
                entries.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else None,
                })
            result["entries"] = entries
        except PermissionError:
            result["error"] = "Permission denied accessing mount point"
        except OSError as exc:
            result["error"] = f"OS error: {exc}"

        return result

    def _handle_read_file(self, **kwargs) -> dict[str, Any]:
        """Handler for ``mirage_read_file`` tool.

        Args:
            **kwargs: Must include ``path`` (str).

        Returns:
            Dict with file content and metadata.
        """
        rel_path = kwargs.get("path", "")

        try:
            full_path = self._validate_path(rel_path)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "path": rel_path}

        try:
            data = full_path.read_bytes()
            stat = full_path.stat()

            try:
                content = data.decode("utf-8")
                return {
                    "success": True,
                    "path": rel_path,
                    "content": content,
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                }
            except UnicodeDecodeError:
                return {
                    "success": True,
                    "path": rel_path,
                    "content_b64": base64.b64encode(data).decode("ascii"),
                    "size_bytes": len(data),
                    "binary": True,
                }
        except FileNotFoundError:
            return {
                "success": False,
                "error": f"File not found: {rel_path}",
                "path": rel_path,
            }
        except PermissionError:
            return {
                "success": False,
                "error": f"Permission denied: {rel_path}",
                "path": rel_path,
            }
        except OSError as exc:
            return {
                "success": False,
                "error": f"OS error reading {rel_path}: {exc}",
                "path": rel_path,
            }

    def _handle_list_dir(self, **kwargs) -> dict[str, Any]:
        """Handler for ``mirage_list_dir`` tool.

        Args:
            **kwargs: May include ``path`` (str, default root).

        Returns:
            Dict with directory listing.
        """
        rel_path = kwargs.get("path", "") or ""

        try:
            full_path = self._validate_path(rel_path) if rel_path else self._mount_point
        except ValueError as exc:
            return {"success": False, "error": str(exc), "path": rel_path or "/", "entries": []}

        try:
            if not full_path.exists():
                return {
                    "success": False,
                    "error": f"Directory not found: {rel_path or '/'}",
                    "path": rel_path or "/",
                    "entries": [],
                }

            if not full_path.is_dir():
                return {
                    "success": False,
                    "error": f"Not a directory: {rel_path or '/'}",
                    "path": rel_path or "/",
                    "entries": [],
                }

            entries = []
            for entry in sorted(full_path.iterdir()):
                entry_info = {
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                }
                if entry.is_file():
                    try:
                        entry_info["size_bytes"] = entry.stat().st_size
                    except OSError:
                        entry_info["size_bytes"] = None
                entries.append(entry_info)

            return {
                "success": True,
                "path": rel_path or "/",
                "entries": entries,
                "count": len(entries),
            }
        except PermissionError:
            return {
                "success": False,
                "error": f"Permission denied: {rel_path or '/'}",
                "path": rel_path or "/",
                "entries": [],
            }
        except OSError as exc:
            return {
                "success": False,
                "error": f"OS error listing {rel_path or '/'}: {exc}",
                "path": rel_path or "/",
                "entries": [],
            }

    def _handle_write_file(self, **kwargs) -> dict[str, Any]:
        """Handler for ``mirage_write_file`` tool.

        Args:
            **kwargs: Must include ``path`` (str) and ``content`` (str).

        Returns:
            Dict with write result.
        """
        rel_path = kwargs.get("path", "")
        content = kwargs.get("content", "")

        if not rel_path:
            return {
                "success": False,
                "error": "Path is required",
            }

        try:
            full_path = self._validate_path(rel_path)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "path": rel_path}

        try:
            # Ensure parent directories exist
            full_path.parent.mkdir(parents=True, exist_ok=True)

            full_path.write_text(content, encoding="utf-8")

            return {
                "success": True,
                "path": rel_path,
                "bytes_written": len(content.encode("utf-8")),
            }
        except PermissionError:
            return {
                "success": False,
                "error": f"Permission denied writing: {rel_path}",
                "path": rel_path,
            }
        except OSError as exc:
            return {
                "success": False,
                "error": f"OS error writing {rel_path}: {exc}",
                "path": rel_path,
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_mounted(self) -> bool:
        """Check if the Mirage mount point is an active FUSE mount."""
        try:
            # Check via /proc/mounts
            mount_point_str = str(self._mount_point)
            with open("/proc/mounts", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == mount_point_str:
                        return True
        except OSError:
            pass

        # Fallback: check if mountpoint command succeeds
        try:
            result = subprocess.run(
                ["mountpoint", "-q", str(self._mount_point)],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return False
