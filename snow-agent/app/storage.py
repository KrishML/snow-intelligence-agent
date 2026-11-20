"""Webcam snapshot storage: local files by default, S3 when configured.

A stored reference is either a local filesystem path or ``s3://bucket/key``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.settings import settings

logger = logging.getLogger(__name__)

_S3_SCHEME = "s3://"


def _default_s3_client() -> Any:
    import boto3

    return boto3.client("s3")


class SnapshotStorage:
    def __init__(self, s3_client_factory: Callable[[], Any] | None = None) -> None:
        self._s3_factory = s3_client_factory or _default_s3_client

    async def persist(self, local_path: Path, key: str) -> str:
        """Store a captured screenshot and return its reference."""
        if settings.webcam_storage_backend != "s3":
            return str(local_path)
        if not settings.webcam_s3_bucket:
            raise RuntimeError("WEBCAM_S3_BUCKET is not configured.")
        object_key = f"{settings.webcam_s3_prefix.strip('/')}/{key}".lstrip("/")
        data = local_path.read_bytes()

        def _upload() -> None:
            self._s3_factory().put_object(
                Bucket=settings.webcam_s3_bucket,
                Key=object_key,
                Body=data,
                ContentType="image/png",
            )

        await asyncio.to_thread(_upload)
        local_path.unlink(missing_ok=True)
        return f"{_S3_SCHEME}{settings.webcam_s3_bucket}/{object_key}"

    async def read(self, reference: str) -> bytes | None:
        """Return image bytes, or None if the reference is missing or not allowed."""
        if reference.startswith(_S3_SCHEME):
            bucket, _, key = reference[len(_S3_SCHEME):].partition("/")
            if bucket != settings.webcam_s3_bucket or not key:
                return None

            def _download() -> bytes:
                return self._s3_factory().get_object(Bucket=bucket, Key=key)["Body"].read()

            try:
                return await asyncio.to_thread(_download)
            except Exception:
                logger.warning("Could not read webcam object %s", reference, exc_info=True)
                return None
        root = Path(settings.webcam_snapshot_dir).resolve()
        path = Path(reference).resolve()
        # Never read outside the snapshot directory.
        if not path.is_relative_to(root) or not path.is_file():
            return None
        return path.read_bytes()

    async def delete(self, reference: str) -> None:
        if reference.startswith(_S3_SCHEME):
            bucket, _, key = reference[len(_S3_SCHEME):].partition("/")
            if bucket == settings.webcam_s3_bucket and key:
                await asyncio.to_thread(
                    lambda: self._s3_factory().delete_object(Bucket=bucket, Key=key)
                )
            return
        root = Path(settings.webcam_snapshot_dir).resolve()
        path = Path(reference).resolve()
        if path.is_relative_to(root):
            path.unlink(missing_ok=True)
