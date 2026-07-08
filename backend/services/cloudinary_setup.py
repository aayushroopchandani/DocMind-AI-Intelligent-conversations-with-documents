from __future__ import annotations

import logging
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)

try:
    import cloudinary
    import cloudinary.uploader
    import cloudinary.utils
except ImportError:  # pragma: no cover
    cloudinary = None  # type: ignore[assignment]


_configured = False


def init_cloudinary() -> bool:
    """
    Configure Cloudinary SDK from environment variables.

    Returns True if configured successfully, else False.
    """
    global _configured

    if not settings.cloudinary_is_configured:
        logger.warning(
            "Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET."
        )
        return False

    if cloudinary is None:
        logger.warning("cloudinary package not installed. Run: pip install cloudinary")
        return False

    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )
    _configured = True
    logger.info("Cloudinary configured for cloud_name=%s", settings.cloudinary_cloud_name)
    return True


def _ensure_ready() -> None:
    if cloudinary is None:
        raise RuntimeError("cloudinary package not installed. Run: pip install cloudinary")
    if not _configured and not init_cloudinary():
        raise RuntimeError(
            "Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET in backend/.env."
        )


def _user_folder(user_id: str, chat_id: str) -> str:
    """
    Assets are namespaced by user then chat so they're trivial to list later,
    e.g. `docmind/<user_id>/<chat_id>/<file>`.
    """
    return f"docmind/{user_id}/{chat_id}"


def upload_pdf(
    file_bytes: bytes,
    *,
    user_id: str,
    chat_id: str,
    filename: str,
) -> dict[str, Any]:
    """
    Upload a PDF to Cloudinary under the user's folder and return the fields we
    persist in MongoDB.

    PDFs are uploaded with `resource_type="image"` so Cloudinary reports the
    page count (`pages`) and lets us derive per-page previews later. The raw
    file remains downloadable via `secure_url`.
    """
    _ensure_ready()

    result = cloudinary.uploader.upload(
        file_bytes,
        resource_type="raw",
        folder=_user_folder(user_id, chat_id),
        use_filename=True,
        unique_filename=True,
        overwrite=False,
        # Tags make bulk lookups by user/chat easy from the dashboard or Admin API.
        tags=[f"user:{user_id}", f"chat:{chat_id}"],
        context={"filename": filename, "user_id": user_id, "chat_id": chat_id},
    )

    return {
        "public_id": result["public_id"],
        # Cloudinary's globally-unique internal id — used here as the "private" id.
        "asset_id": result.get("asset_id", ""),
        "secure_url": result.get("secure_url", ""),
        "resource_type": result.get("resource_type", "image"),
        "format": result.get("format"),
        "bytes": result.get("bytes"),
        "pages": result.get("pages"),
        "filename": filename,
    }


def delete_pdf(public_id: str, *, resource_type: str = "image") -> bool:
    """Delete an asset from Cloudinary. Returns True on success."""
    _ensure_ready()
    result = cloudinary.uploader.destroy(public_id, resource_type=resource_type)
    ok = result.get("result") == "ok"
    if not ok:
        logger.warning("Cloudinary delete for %s returned: %s", public_id, result)
    return ok
