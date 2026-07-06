from __future__ import annotations

import logging

from config.settings import settings

logger = logging.getLogger(__name__)

try:
    import cloudinary
except ImportError:  # pragma: no cover
    cloudinary = None  # type: ignore[assignment]


def init_cloudinary() -> bool:
    """
    Configure Cloudinary SDK from environment variables.

    Setup only for now (no upload logic yet).
    Returns True if configured successfully, else False.
    """
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
    logger.info("Cloudinary configured for cloud_name=%s", settings.cloudinary_cloud_name)
    return True
