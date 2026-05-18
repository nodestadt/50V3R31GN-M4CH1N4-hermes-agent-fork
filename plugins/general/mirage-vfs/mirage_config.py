"""Configuration module for the Mirage VFS bridge plugin.

Loads configuration from:
  1. cli-config.yaml (plugins.mirage_vfs section)
  2. Environment variable overrides (MIRAGE_* prefix)
  3. Sensible defaults for Node D deployment in Sovereign Mesh.

Node topology:
  - Node A (100.96.253.114): Redis, S3-compatible storage (MinIO)
  - Node D (100.120.225.12): Mirage mount point (/mnt/mirage)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_MOUNT_POINT = "/mnt/mirage"
DEFAULT_REDIS_HOST = "100.96.253.114"
DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_KEY_PREFIX = "mirage:vfs:"
DEFAULT_S3_ENDPOINT = "http://100.96.253.114:9000"
DEFAULT_S3_BUCKET = "sovereign-mirage"
DEFAULT_HEALTH_CHECK_ON_START = True


@dataclass(frozen=True)
class MirageVfsConfig:
    """Immutable configuration for the Mirage VFS bridge.

    Attributes:
        mount_point: FUSE mount point path on Node D.
        redis_host: Redis server hostname (Node A Tailnet IP).
        redis_port: Redis server port.
        redis_key_prefix: Key prefix for Mirage entries in Redis.
        s3_endpoint: S3-compatible endpoint URL (Node A MinIO).
        s3_bucket: S3 bucket name for VFS storage.
        health_check_on_start: Whether to run a health check at session start.
    """

    mount_point: str = DEFAULT_MOUNT_POINT
    redis_host: str = DEFAULT_REDIS_HOST
    redis_port: int = DEFAULT_REDIS_PORT
    redis_key_prefix: str = DEFAULT_REDIS_KEY_PREFIX
    s3_endpoint: str = DEFAULT_S3_ENDPOINT
    s3_bucket: str = DEFAULT_S3_BUCKET
    health_check_on_start: bool = DEFAULT_HEALTH_CHECK_ON_START

    @classmethod
    def from_dict(cls, data: dict) -> "MirageVfsConfig":
        """Build a ``MirageVfsConfig`` from a YAML-derived dictionary.

        Environment variable overrides are applied after the dict is read:
          - ``MIRAGE_MOUNT_POINT`` overrides ``mount_point``
          - ``MIRAGE_REDIS_HOST`` overrides ``redis_host``
          - ``MIRAGE_REDIS_PORT`` overrides ``redis_port``
          - ``MIRAGE_S3_ENDPOINT`` overrides ``s3_endpoint``
          - ``MIRAGE_S3_BUCKET`` overrides ``s3_bucket``

        Args:
            data: Dictionary from the ``plugins.mirage_vfs`` section of
                ``cli-config.yaml``.

        Returns:
            A fully-resolved ``MirageVfsConfig`` instance.
        """
        return cls(
            mount_point=os.environ.get(
                "MIRAGE_MOUNT_POINT",
                data.get("mount_point", DEFAULT_MOUNT_POINT),
            ),
            redis_host=os.environ.get(
                "MIRAGE_REDIS_HOST",
                data.get("redis_host", DEFAULT_REDIS_HOST),
            ),
            redis_port=int(
                os.environ.get(
                    "MIRAGE_REDIS_PORT",
                    data.get("redis_port", DEFAULT_REDIS_PORT),
                )
            ),
            redis_key_prefix=data.get("redis_key_prefix", DEFAULT_REDIS_KEY_PREFIX),
            s3_endpoint=os.environ.get(
                "MIRAGE_S3_ENDPOINT",
                data.get("s3_endpoint", DEFAULT_S3_ENDPOINT),
            ),
            s3_bucket=os.environ.get(
                "MIRAGE_S3_BUCKET",
                data.get("s3_bucket", DEFAULT_S3_BUCKET),
            ),
            health_check_on_start=bool(
                data.get("health_check_on_start", DEFAULT_HEALTH_CHECK_ON_START)
            ),
        )
