"""Immutable provider-response evidence and fail-closed text extraction.

Adapters may send private source material to an explicitly consented external
provider. When they do, they must archive the exact response bytes before
interpreting them. This module deliberately knows nothing about API keys,
request bodies, prompts, or source media bytes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1


class ProviderResponseError(RuntimeError):
    """A bounded response-evidence error safe to classify without raw text."""

    def __init__(self, code: str, stage: str) -> None:
        super().__init__(str(code))
        self.code = str(code)
        self.stage = str(stage)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def response_binding(
    *,
    provider: str,
    requested_model: str,
    source_message_id: int | None,
    attempt_message_id: int | None,
    photo_artifact_id: str | None,
    photo_sha256: str,
    request_sha256: str,
) -> dict[str, Any]:
    """Build the identity that prevents evidence reuse across attempts."""

    return {
        "provider": str(provider),
        "requested_model": str(requested_model),
        "source_message_id": (
            int(source_message_id)
            if source_message_id is not None
            else None
        ),
        "attempt_message_id": (
            int(attempt_message_id)
            if attempt_message_id is not None
            else None
        ),
        "photo_artifact_id": (
            str(photo_artifact_id) if photo_artifact_id else None
        ),
        "photo_sha256": str(photo_sha256),
        "request_sha256": str(request_sha256),
    }


def load_response_archive(
    path: str | Path,
    expected_binding: Mapping[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    """Verify and return exact response bytes from an immutable archive."""

    archive = Path(path)
    try:
        if archive.is_symlink() or not archive.is_file():
            raise ValueError("archive is not a regular file")
        wrapper = json.loads(archive.read_text(encoding="utf-8"))
        if not isinstance(wrapper, dict):
            raise TypeError("archive wrapper is not an object")
        if wrapper.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("archive schema mismatch")
        if wrapper.get("binding") != dict(expected_binding):
            raise ValueError("archive binding mismatch")
        encoded = wrapper["response_body_base64"]
        if not isinstance(encoded, str):
            raise TypeError("response body is not encoded text")
        raw_body = base64.b64decode(encoded, validate=True)
        if int(wrapper.get("response_body_bytes")) != len(raw_body):
            raise ValueError("response body size mismatch")
        if wrapper.get("response_body_sha256") != sha256_bytes(raw_body):
            raise ValueError("response body checksum mismatch")
        archive.chmod(0o400)
    except ProviderResponseError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ProviderResponseError(
            "provider_response_archive_invalid",
            "provider_archive",
        ) from exc
    return raw_body, wrapper


def _pending_archive_path(archive: Path) -> Path:
    return archive.with_name(f".{archive.name}.pending")


def _staging_archive_paths(archive: Path) -> list[Path]:
    return sorted(
        archive.parent.glob(f".{archive.name}.*.tmp"),
        key=lambda item: item.name,
    )


def _fsync_directory(directory: Path) -> None:
    try:
        directory_fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass


def _cleanup_matching_staging(
    archive: Path,
    binding: Mapping[str, Any],
    expected_body: bytes,
) -> None:
    for staging in _staging_archive_paths(archive):
        staging_body, _ = load_response_archive(staging, binding)
        if staging_body != expected_body:
            raise ProviderResponseError(
                "provider_response_archive_invalid",
                "provider_archive",
            )
        staging.unlink(missing_ok=True)
    _fsync_directory(archive.parent)


def _publish_pending_archive(
    pending: Path,
    archive: Path,
    binding: Mapping[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    pending_body, pending_wrapper = load_response_archive(pending, binding)
    try:
        os.link(pending, archive)
    except FileExistsError:
        archived_body, archived_wrapper = load_response_archive(
            archive,
            binding,
        )
        if archived_body != pending_body:
            raise ProviderResponseError(
                "provider_response_archive_invalid",
                "provider_archive",
            )
        pending.unlink(missing_ok=True)
        _fsync_directory(archive.parent)
        return archived_body, archived_wrapper
    except OSError as exc:
        raise ProviderResponseError(
            "provider_response_archive_failed",
            "provider_archive",
        ) from exc
    _fsync_directory(archive.parent)
    pending.unlink(missing_ok=True)
    _fsync_directory(archive.parent)
    archive.chmod(0o400)
    return pending_body, pending_wrapper


def recover_response_archive(
    path: str | Path,
    binding: Mapping[str, Any],
) -> tuple[bytes, dict[str, Any]] | None:
    """Recover a fully fsynced pre-publish file without another provider call."""

    archive = Path(path)
    pending = _pending_archive_path(archive)
    if archive.exists() or archive.is_symlink():
        archived_body, wrapper = load_response_archive(archive, binding)
        if pending.exists() or pending.is_symlink():
            pending_body, _ = load_response_archive(pending, binding)
            if pending_body != archived_body:
                raise ProviderResponseError(
                    "provider_response_archive_invalid",
                    "provider_archive",
                )
            pending.unlink(missing_ok=True)
            _fsync_directory(archive.parent)
        _cleanup_matching_staging(archive, binding, archived_body)
        return archived_body, wrapper
    if pending.exists() or pending.is_symlink():
        pending_body, _ = load_response_archive(pending, binding)
        _cleanup_matching_staging(archive, binding, pending_body)
        return _publish_pending_archive(pending, archive, binding)
    staging_paths = _staging_archive_paths(archive)
    if staging_paths:
        if len(staging_paths) != 1:
            raise ProviderResponseError(
                "provider_response_archive_incomplete",
                "provider_archive",
            )
        staging = staging_paths[0]
        try:
            load_response_archive(staging, binding)
            os.link(staging, pending)
            _fsync_directory(archive.parent)
            staging.unlink(missing_ok=True)
            _fsync_directory(archive.parent)
        except ProviderResponseError as exc:
            raise ProviderResponseError(
                "provider_response_archive_incomplete",
                "provider_archive",
            ) from exc
        except FileExistsError:
            pending_body, _ = load_response_archive(pending, binding)
            staging_body, _ = load_response_archive(staging, binding)
            if pending_body != staging_body:
                raise ProviderResponseError(
                    "provider_response_archive_invalid",
                    "provider_archive",
                )
            staging.unlink(missing_ok=True)
            _fsync_directory(archive.parent)
        except OSError as exc:
            raise ProviderResponseError(
                "provider_response_archive_failed",
                "provider_archive",
            ) from exc
        return _publish_pending_archive(pending, archive, binding)
    return None


def archive_response(
    path: str | Path,
    raw_body: bytes,
    *,
    binding: Mapping[str, Any],
    http_status: int,
    content_type: str,
    received_at: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Persist exact bytes once, fsync them, and refuse conflicting replay."""

    archive = Path(path)
    recovered = recover_response_archive(archive, binding)
    if recovered is not None:
        archived_body, wrapper = recovered
        if archived_body != raw_body:
            raise ProviderResponseError(
                "provider_response_archive_invalid",
                "provider_archive",
            )
        return archived_body, wrapper
    wrapper = {
        "schema_version": SCHEMA_VERSION,
        "received_at": received_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "binding": dict(binding),
        "http_status": int(http_status),
        "content_type": str(content_type or "")[:120],
        "response_body_bytes": len(raw_body),
        "response_body_sha256": sha256_bytes(raw_body),
        "response_body_base64": base64.b64encode(raw_body).decode("ascii"),
    }
    encoded = (
        json.dumps(wrapper, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    pending = _pending_archive_path(archive)
    staging = archive.with_name(
        f".{archive.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.parent.chmod(0o700)
        descriptor = os.open(
            str(staging),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o400,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(staging, 0o400)
        finally:
            os.close(descriptor)
        _fsync_directory(archive.parent)
        try:
            os.link(staging, pending)
        except FileExistsError:
            pending_body, _ = load_response_archive(pending, binding)
            staging_body, _ = load_response_archive(staging, binding)
            if pending_body != staging_body:
                raise ProviderResponseError(
                    "provider_response_archive_invalid",
                    "provider_archive",
                )
        _fsync_directory(archive.parent)
        staging.unlink(missing_ok=True)
        _fsync_directory(archive.parent)
    except FileExistsError as exc:
        raise ProviderResponseError(
            "provider_response_archive_failed",
            "provider_archive",
        ) from exc
    except ProviderResponseError:
        raise
    except OSError as exc:
        raise ProviderResponseError(
            "provider_response_archive_failed",
            "provider_archive",
        ) from exc
    published_body, published_wrapper = _publish_pending_archive(
        pending,
        archive,
        binding,
    )
    if published_body != raw_body:
        raise ProviderResponseError(
            "provider_response_archive_invalid",
            "provider_archive",
        )
    return published_body, published_wrapper


def extract_text(raw_body: bytes) -> tuple[dict[str, Any], str]:
    """Read all non-empty text blocks, ignoring leading non-text blocks."""

    try:
        envelope = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderResponseError(
            "provider_envelope_json_invalid",
            "provider_envelope",
        ) from exc
    if not isinstance(envelope, dict):
        raise ProviderResponseError(
            "provider_envelope_json_invalid",
            "provider_envelope",
        )
    content = envelope.get("content")
    if not isinstance(content, list):
        raise ProviderResponseError(
            "provider_text_block_missing",
            "provider_text",
        )
    text_blocks = [
        block["text"]
        for block in content
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
            and block["text"].strip()
        )
    ]
    if not text_blocks:
        raise ProviderResponseError(
            "provider_text_block_missing",
            "provider_text",
        )
    return envelope, "\n".join(text_blocks)


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract one JSON object from a text response or fail closed."""

    candidate = text.strip()
    if "{" in candidate and "}" in candidate:
        candidate = candidate[
            candidate.find("{") : candidate.rfind("}") + 1
        ]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError(
            "provider_payload_json_invalid",
            "provider_payload",
        ) from exc
    if not isinstance(value, dict):
        raise ProviderResponseError(
            "provider_payload_schema_invalid",
            "provider_payload",
        )
    return value
