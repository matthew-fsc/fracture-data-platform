"""The evidence trail.

Object storage holds every raw extraction artifact, separate from the database
on purpose (spec section 2): if a tenant disputes a number, you produce the file
you received and its hash. Order is not negotiable -- artifact first, hash
recorded, then load. A row in `raw` whose artifact is missing is a row you
cannot defend.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from fracture.core.config import Settings, settings as default_settings
from fracture.core.hashing import canonical_json, sha256_bytes
from fracture.core.logging import get_logger

log = get_logger("ingest.artifacts")

ARTIFACT_FORMAT_VERSION = 1


@dataclass(frozen=True)
class StoredArtifact:
    uri: str
    sha256: bytes
    byte_size: int
    record_count: int

    @property
    def sha256_hex(self) -> str:
        return self.sha256.hex()


class ArtifactStore(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def get(self, uri: str) -> bytes: ...
    def exists(self, uri: str) -> bool: ...


class LocalArtifactStore:
    """Filesystem store that mirrors the S3 key layout exactly.

    Local and prod therefore produce the same `_artifact_uri` shape, so a
    drill-through link built in development resolves in production.
    """

    def __init__(self, root: Path | str | None = None, bucket: str | None = None) -> None:
        s = default_settings
        self.root = Path(root or s.artifact_root)
        self.bucket = bucket or s.artifact_bucket
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / self.bucket / key

    def put(self, key: str, data: bytes) -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # Artifacts are immutable. A collision means two extractions claimed
            # the same key, which would silently overwrite evidence.
            existing = target.read_bytes()
            if existing != data:
                raise FileExistsError(
                    f"artifact key {key!r} already exists with different content; "
                    "extraction keys must be unique per run"
                )
            return f"s3://{self.bucket}/{key}"
        target.write_bytes(data)
        target.chmod(0o440)
        return f"s3://{self.bucket}/{key}"

    def _key_from_uri(self, uri: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError(f"uri {uri!r} does not belong to bucket {self.bucket!r}")
        return uri[len(prefix) :]

    def get(self, uri: str) -> bytes:
        return self._path(self._key_from_uri(uri)).read_bytes()

    def exists(self, uri: str) -> bool:
        try:
            return self._path(self._key_from_uri(uri)).exists()
        except ValueError:
            return False


class S3ArtifactStore:  # pragma: no cover - requires AWS
    """Production store. Object lock and per-tenant KMS key are set by Terraform."""

    def __init__(self, bucket: str | None = None, kms_key_arn: str | None = None) -> None:
        import boto3

        self.bucket = bucket or default_settings.artifact_bucket
        self.kms_key_arn = kms_key_arn
        self._client = boto3.client("s3")

    def put(self, key: str, data: bytes) -> str:
        extra: dict[str, Any] = {}
        if self.kms_key_arn:
            extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_arn}
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        return f"s3://{self.bucket}/{key}"

    def get(self, uri: str) -> bytes:
        key = uri.split(f"s3://{self.bucket}/", 1)[1]
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, uri: str) -> bool:
        from botocore.exceptions import ClientError

        key = uri.split(f"s3://{self.bucket}/", 1)[1]
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False


def artifact_key(
    s3_prefix: str,
    firm_id: str,
    source_id: str,
    stream: str,
    extracted_at: dt.datetime,
    load_id: str,
) -> str:
    """Deterministic, sortable, and partitioned by extraction date."""
    day = extracted_at.date().isoformat()
    stamp = extracted_at.strftime("%Y%m%dT%H%M%S%f")
    return (
        f"{s3_prefix}/raw/firm={firm_id}/source={source_id}/stream={stream}/"
        f"dt={day}/{stamp}-{load_id}.json.gz"
    )


def build_envelope(
    firm_id: str,
    source_id: str,
    stream: str,
    extracted_at: dt.datetime,
    records: Sequence[Any],
    adapter_version: str | None = None,
    schema_hash: bytes | None = None,
    cursor_start: str | None = None,
    cursor_end: str | None = None,
) -> bytes:
    """Serialise an extraction into the self-describing artifact envelope.

    Self-describing matters: rebuilding the database from S3 alone (spec 6.1)
    means the object must carry enough context to know which firm, source and
    stream it belongs to without consulting anything else.
    """
    envelope = {
        "format_version": ARTIFACT_FORMAT_VERSION,
        "firm_id": firm_id,
        "source_id": source_id,
        "stream": stream,
        "extracted_at": extracted_at.isoformat(),
        "adapter_version": adapter_version,
        "schema_hash": schema_hash.hex() if schema_hash else None,
        "cursor_start": cursor_start,
        "cursor_end": cursor_end,
        "record_count": len(records),
        "records": list(records),
    }
    return gzip.compress(canonical_json(envelope), mtime=0)


def read_envelope(data: bytes) -> dict[str, Any]:
    return json.loads(gzip.decompress(data).decode("utf-8"))


def store_extraction(
    store: ArtifactStore,
    s3_prefix: str,
    firm_id: str,
    source_id: str,
    stream: str,
    extracted_at: dt.datetime,
    load_id: str,
    records: Sequence[Any],
    **envelope_kwargs: Any,
) -> StoredArtifact:
    """Write the artifact and return its URI and hash. Call before loading."""
    data = build_envelope(firm_id, source_id, stream, extracted_at, records, **envelope_kwargs)
    key = artifact_key(s3_prefix, firm_id, source_id, stream, extracted_at, load_id)
    uri = store.put(key, data)
    artifact = StoredArtifact(
        uri=uri, sha256=sha256_bytes(data), byte_size=len(data), record_count=len(records)
    )
    log.info(
        "stored artifact %s (%d records, %d bytes, sha256=%s)",
        uri, artifact.record_count, artifact.byte_size, artifact.sha256_hex[:16],
    )
    return artifact


def default_store(kms_key_arn: str | None = None) -> ArtifactStore:
    if os.environ.get("FRACTURE_ENV") == "prod":  # pragma: no cover
        return S3ArtifactStore(kms_key_arn=kms_key_arn)
    return LocalArtifactStore()
