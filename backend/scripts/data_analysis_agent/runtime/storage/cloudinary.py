from __future__ import annotations

import asyncio
import hashlib
import io
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping

from ..models.artifacts import BlobReference
from .base import (
    ArtifactBlobStore,
    BlobConflictError,
    BlobIntegrityError,
    BlobNotFoundError,
    BlobStat,
    BlobStoreError,
    BlobStoreUnavailableError,
    BlobUpload,
)

try:
    import cloudinary
    import cloudinary.api
    import cloudinary.exceptions
    import cloudinary.uploader
    import cloudinary.utils
except ImportError:  # pragma: no cover - exercised only in minimal deployments
    cloudinary = None  # type: ignore[assignment]


_OBJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")
_CONTEXT_KEY_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class CloudinaryBlobStoreConfig:
    cloud_name: str
    api_key: str
    api_secret: str
    request_timeout_seconds: float = 30.0
    max_download_bytes: int = 100 * 1024 * 1024

    def __post_init__(self) -> None:
        if not all(
            value.strip() for value in (self.cloud_name, self.api_key, self.api_secret)
        ):
            raise ValueError("Cloudinary cloud name, API key, and API secret are required")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.max_download_bytes <= 0:
            raise ValueError("max_download_bytes must be positive")


class CloudinaryArtifactBlobStore(ArtifactBlobStore):
    """Cloudinary `raw/authenticated` implementation of immutable blob storage."""

    def __init__(self, config: CloudinaryBlobStoreConfig) -> None:
        if cloudinary is None:
            raise RuntimeError("cloudinary package is not installed")
        self._config = config
        # The Cloudinary SDK uses process-global configuration. DocMind has one
        # account per backend process, so configuring once per adapter is safe.
        cloudinary.config(
            cloud_name=config.cloud_name,
            api_key=config.api_key,
            api_secret=config.api_secret,
            secure=True,
            timeout=config.request_timeout_seconds,
        )

    async def upload(self, upload: BlobUpload) -> BlobReference:
        _validate_object_key(upload.object_key)
        try:
            result = await self._sdk_call(
                self._upload_sync,
                upload,
                action="upload",
            )
        except Exception as exc:
            translated = _translate_provider_error(exc, action="upload")
            if not isinstance(translated, BlobConflictError):
                raise translated from exc
            # `overwrite=False` makes the deterministic object key immutable.
            # A conflict can therefore be a retry after a process crashed
            # between provider upload and Mongo finalization. Recover only when
            # provider metadata matches the exact validated upload.
            result = await self._recover_existing_upload(upload)

        public_id = str(result.get("public_id") or "")
        remote_bytes = _coerce_int(result.get("bytes"))
        if public_id != upload.object_key:
            await self._best_effort_delete_public_id(public_id)
            raise BlobIntegrityError("provider returned an unexpected object key")
        if remote_bytes != upload.byte_count:
            await self._best_effort_delete_public_id(public_id)
            raise BlobIntegrityError("provider returned an unexpected object size")

        delivery_type = str(result.get("type") or "authenticated")
        resource_type = str(result.get("resource_type") or "raw")
        if delivery_type != "authenticated" or resource_type != "raw":
            await self._best_effort_delete_public_id(public_id)
            raise BlobIntegrityError("provider did not preserve private raw delivery")

        return BlobReference(
            provider="cloudinary",
            object_key=public_id,
            provider_asset_id=_optional_string(result.get("asset_id")),
            provider_version=_optional_string(result.get("version")),
            resource_type="raw",
            delivery_type="authenticated",
            content_type=upload.content_type,
            filename=upload.filename,
            byte_count=upload.byte_count,
            sha256=upload.sha256,
            created_at=_provider_created_at(result.get("created_at")),
        )

    async def _recover_existing_upload(
        self,
        upload: BlobUpload,
    ) -> Mapping[str, Any]:
        try:
            result = await self._sdk_call(
                cloudinary.api.resource,
                upload.object_key,
                action="recover",
                resource_type="raw",
                type="authenticated",
                context=True,
                timeout=self._config.request_timeout_seconds,
            )
        except Exception as exc:
            raise _translate_provider_error(exc, action="recover") from exc
        if str(result.get("public_id") or "") != upload.object_key:
            raise BlobIntegrityError(
                "existing immutable object has an unexpected key"
            )
        if _coerce_int(result.get("bytes")) != upload.byte_count:
            raise BlobIntegrityError(
                "existing immutable object has an unexpected size"
            )
        if str(result.get("resource_type") or "raw") != "raw":
            raise BlobIntegrityError("existing immutable object is not raw")
        if str(result.get("type") or "authenticated") != "authenticated":
            raise BlobIntegrityError(
                "existing immutable object is not authenticated"
            )
        context = result.get("context") or {}
        custom = (
            context.get("custom", {}) if isinstance(context, Mapping) else {}
        )
        stored_sha256 = (
            _optional_string(custom.get("sha256"))
            if isinstance(custom, Mapping)
            else None
        )
        if stored_sha256 is not None and stored_sha256 != upload.sha256:
            raise BlobIntegrityError(
                "existing immutable object has a different checksum"
            )
        return result

    async def stat(self, reference: BlobReference) -> BlobStat:
        self._validate_reference(reference)
        try:
            result = await self._sdk_call(
                cloudinary.api.resource,
                reference.object_key,
                action="stat",
                resource_type="raw",
                type="authenticated",
                context=True,
                timeout=self._config.request_timeout_seconds,
            )
        except Exception as exc:
            raise _translate_provider_error(exc, action="stat") from exc

        if str(result.get("resource_type") or "raw") != "raw":
            raise BlobIntegrityError("stored object is not a raw resource")
        if str(result.get("type") or "authenticated") != "authenticated":
            raise BlobIntegrityError("stored object is not authenticated")
        context = result.get("context") or {}
        custom_context = (
            context.get("custom", {}) if isinstance(context, Mapping) else {}
        )
        stored_sha256 = (
            _optional_string(custom_context.get("sha256"))
            if isinstance(custom_context, Mapping)
            else None
        )
        return BlobStat(
            object_key=str(result.get("public_id") or reference.object_key),
            byte_count=_coerce_int(result.get("bytes")),
            provider_version=_optional_string(result.get("version")),
            provider_asset_id=_optional_string(result.get("asset_id")),
            etag=_optional_string(result.get("etag")),
            stored_sha256=stored_sha256,
        )

    async def download(
        self,
        reference: BlobReference,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        self._validate_reference(reference)
        effective_limit = min(
            max_bytes if max_bytes is not None else self._config.max_download_bytes,
            self._config.max_download_bytes,
        )
        if effective_limit <= 0:
            raise ValueError("max_bytes must be positive")
        url = await self.signed_download_url(reference)
        try:
            return await asyncio.to_thread(
                self._download_sync,
                url,
                effective_limit,
            )
        except BlobStoreError:
            raise
        except Exception as exc:
            raise _translate_download_error(exc) from exc

    async def signed_download_url(
        self,
        reference: BlobReference,
        *,
        expires_in_seconds: int = 900,
    ) -> str:
        self._validate_reference(reference)
        if not 1 <= expires_in_seconds <= 24 * 60 * 60:
            raise ValueError("expires_in_seconds must be between 1 and 86400")
        expires_at = int(time.time()) + expires_in_seconds
        try:
            return await asyncio.to_thread(
                cloudinary.utils.private_download_url,
                reference.object_key,
                # Raw Cloudinary public IDs already include their extension.
                # Passing it again as `format` can address a different object.
                None,
                resource_type="raw",
                type="authenticated",
                attachment=reference.filename,
                expires_at=expires_at,
            )
        except Exception as exc:
            raise _translate_provider_error(exc, action="sign") from exc

    async def generate_signed_download(
        self,
        reference: BlobReference,
        *,
        expires_in_seconds: int = 900,
    ) -> str:
        """Compatibility name used by the provider-neutral storage contract."""

        return await self.signed_download_url(
            reference,
            expires_in_seconds=expires_in_seconds,
        )

    async def verify_checksum(self, reference: BlobReference) -> None:
        """Verify metadata and stream remote bytes through SHA-256."""

        self._validate_reference(reference)
        if reference.byte_count > self._config.max_download_bytes:
            raise BlobIntegrityError("blob exceeds the verification download limit")
        metadata = await self.stat(reference)
        if metadata.object_key != reference.object_key:
            raise BlobIntegrityError("stored object key does not match its reference")
        if metadata.byte_count != reference.byte_count:
            raise BlobIntegrityError("stored object size does not match its reference")
        if (
            reference.provider_version
            and metadata.provider_version
            and metadata.provider_version != reference.provider_version
        ):
            raise BlobIntegrityError("stored object version does not match its reference")
        if metadata.stored_sha256 and metadata.stored_sha256 != reference.sha256:
            raise BlobIntegrityError("stored checksum metadata does not match its reference")

        url = await self.signed_download_url(reference)
        try:
            remote_size, remote_sha256 = await asyncio.to_thread(
                self._hash_download_sync,
                url,
                min(
                    max(reference.byte_count + 1, 1),
                    self._config.max_download_bytes,
                ),
            )
        except BlobStoreError:
            raise
        except Exception as exc:
            raise _translate_download_error(exc) from exc
        if remote_size != reference.byte_count:
            raise BlobIntegrityError("downloaded object size does not match its reference")
        if remote_sha256 != reference.sha256:
            raise BlobIntegrityError(
                "downloaded object checksum does not match its reference"
            )

    async def delete(self, reference: BlobReference) -> bool:
        self._validate_reference(reference)
        try:
            result = await self._sdk_call(
                cloudinary.uploader.destroy,
                reference.object_key,
                action="delete",
                resource_type="raw",
                type="authenticated",
                invalidate=True,
                timeout=self._config.request_timeout_seconds,
            )
        except Exception as exc:
            raise _translate_provider_error(exc, action="delete") from exc
        return str(result.get("result") or "").casefold() == "ok"

    def _upload_sync(self, upload: BlobUpload) -> Mapping[str, Any]:
        context = {
            "sha256": upload.sha256,
            "content_type": upload.content_type,
            "filename": upload.filename,
            **_sanitize_context(upload.metadata),
        }
        return cloudinary.uploader.upload(
            io.BytesIO(upload.content),
            resource_type="raw",
            type="authenticated",
            public_id=upload.object_key,
            overwrite=False,
            unique_filename=False,
            use_filename=False,
            invalidate=False,
            filename_override=upload.filename,
            context=context,
            timeout=self._config.request_timeout_seconds,
        )

    def _download_sync(self, url: str, max_bytes: int) -> bytes:
        output = bytearray()
        with self._open_download(url) as response:
            _validate_content_length(response, max_bytes)
            while chunk := response.read(_READ_CHUNK_BYTES):
                output.extend(chunk)
                if len(output) > max_bytes:
                    raise BlobIntegrityError("download exceeded its allowed size")
        return bytes(output)

    def _hash_download_sync(self, url: str, max_bytes: int) -> tuple[int, str]:
        byte_count = 0
        digest = hashlib.sha256()
        with self._open_download(url) as response:
            _validate_content_length(response, max_bytes)
            while chunk := response.read(_READ_CHUNK_BYTES):
                byte_count += len(chunk)
                if byte_count > max_bytes:
                    raise BlobIntegrityError("download exceeded its expected size")
                digest.update(chunk)
        return byte_count, digest.hexdigest()

    def _open_download(self, url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "DocMind-ArtifactStore/1"},
        )
        return urllib.request.urlopen(  # noqa: S310 - URL is SDK-signed by us
            request,
            timeout=self._config.request_timeout_seconds,
        )

    async def _best_effort_delete_public_id(self, public_id: str) -> None:
        if not public_id:
            return
        try:
            await self._sdk_call(
                cloudinary.uploader.destroy,
                public_id,
                action="cleanup",
                resource_type="raw",
                type="authenticated",
                invalidate=True,
                timeout=self._config.request_timeout_seconds,
            )
        except Exception:
            # The integrity error is more useful than cleanup failure. Orphan
            # reconciliation can retry deletion later.
            return

    async def _sdk_call(
        self,
        function: Any,
        /,
        *args: Any,
        action: str,
        **kwargs: Any,
    ) -> Any:
        """Bound blocking SDK calls; timed-out mutations remain ambiguous."""

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(function, *args, **kwargs),
                timeout=self._config.request_timeout_seconds,
            )
        except TimeoutError as exc:
            raise BlobStoreUnavailableError(
                f"artifact blob {action} timed out"
            ) from exc

    @staticmethod
    def _validate_reference(reference: BlobReference) -> None:
        if _enum_value(reference.provider) != "cloudinary":
            raise ValueError("blob reference belongs to a different provider")
        if _enum_value(reference.resource_type) != "raw":
            raise ValueError("artifact blob must use Cloudinary raw resources")
        if _enum_value(reference.delivery_type) != "authenticated":
            raise ValueError("artifact blob must use authenticated delivery")
        _validate_object_key(reference.object_key)


def _validate_object_key(object_key: str) -> None:
    if not _OBJECT_KEY_PATTERN.fullmatch(object_key):
        raise ValueError("object_key contains unsupported characters")
    path = PurePosixPath(object_key)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("object_key must be a normalized relative path")


def _sanitize_context(metadata: Mapping[str, str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for key, value in metadata.items():
        safe_key = _CONTEXT_KEY_PATTERN.sub("_", str(key)).strip("_.")[:64]
        if not safe_key or safe_key in {"sha256", "content_type", "filename"}:
            continue
        output[safe_key] = str(value)[:512]
    return output


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _optional_string(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _coerce_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise BlobIntegrityError("provider returned an invalid byte count") from exc


def _provider_created_at(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError:
            parsed = datetime.now(timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _translate_provider_error(exc: Exception, *, action: str) -> BlobStoreError:
    if cloudinary is not None:
        if isinstance(exc, cloudinary.exceptions.NotFound):
            return BlobNotFoundError("artifact blob was not found")
        if isinstance(exc, cloudinary.exceptions.AlreadyExists):
            return BlobConflictError("immutable artifact object already exists")
        if isinstance(exc, cloudinary.exceptions.BadRequest) and "already" in str(
            exc
        ).casefold():
            return BlobConflictError("immutable artifact object already exists")
    return BlobStoreUnavailableError(f"artifact blob {action} failed")


def _translate_download_error(exc: Exception) -> BlobStoreError:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        return BlobNotFoundError("artifact blob was not found")
    return BlobStoreUnavailableError("artifact blob download failed")


def _validate_content_length(response: Any, max_bytes: int) -> None:
    raw_length = response.headers.get("Content-Length")
    if raw_length is None:
        return
    try:
        content_length = int(raw_length)
    except (TypeError, ValueError):
        return
    if content_length > max_bytes:
        raise BlobIntegrityError("download exceeded its allowed size")
