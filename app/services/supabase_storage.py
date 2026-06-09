from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.config import get_settings
from app.db.supabase_client import get_supabase_admin_client

logger = logging.getLogger(__name__)


class SupabaseStorageError(Exception):
    """Raised when an upload or signed-URL call fails. Caller decides
    whether to bubble (block the API response) or swallow (e.g. PDF
    upload failure after invoice already committed → log + leave
    pdf_url=NULL + let admin regenerate later).
    """
    pass


def _bucket() -> str:
    return get_settings().supabase_storage_bucket


def _client():
    return get_supabase_admin_client()


# ── Sync workers (wrapped with asyncio.to_thread by the async API) ────


def _upload_sync(bucket: str, path: str, content: bytes, content_type: str) -> str:
    """Single-shot upload with upsert. Returns the storage path on
    success, raises SupabaseStorageError on failure. Idempotent thanks
    to upsert=true — re-uploading the same path replaces.
    """
    try:
        client = _client()
        client.storage.from_(bucket).upload(
            path=path,
            file=content,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        return path
    except Exception as e:
        logger.exception("Supabase Storage upload failed: bucket=%s path=%s", bucket, path)
        raise SupabaseStorageError(f"upload failed: {e}") from e


def _signed_url_sync(bucket: str, path: str, ttl_seconds: int) -> str:
    """Mint a short-lived signed URL. Wrapping the dict response so the
    caller doesn't have to know the SDK shape ({"signedURL": "..."}).
    """
    try:
        client = _client()
        resp = client.storage.from_(bucket).create_signed_url(path, ttl_seconds)
        # supabase-py 2.x returns a dict with 'signedURL' key. Be
        # defensive in case the casing or shape changes between SDK
        # versions — fall back to the first stringified URL we find.
        if isinstance(resp, dict):
            for k in ("signedURL", "signed_url", "url"):
                if resp.get(k):
                    return resp[k]
        raise SupabaseStorageError(f"unexpected signed URL response shape: {resp!r}")
    except SupabaseStorageError:
        raise
    except Exception as e:
        logger.exception("Supabase Storage signed_url failed: bucket=%s path=%s", bucket, path)
        raise SupabaseStorageError(f"signed_url failed: {e}") from e


# ── Public async API ──────────────────────────────────────────────────


async def upload_pdf(path: str, content: bytes) -> str:
    """Upload a generated invoice PDF. `path` is the storage object key
    relative to the bucket (e.g. `invoices/<company_id>/2025-26/FMC-2025-26-001.pdf`).
    Returns the path on success.
    """
    bucket = _bucket()
    return await asyncio.to_thread(_upload_sync, bucket, path, content, "application/pdf")


async def upload_image(path: str, content: bytes, content_type: str = "image/png") -> str:
    """Upload a brand asset (logo / signature). content_type should be
    a valid image MIME (image/png, image/jpeg).
    """
    bucket = _bucket()
    return await asyncio.to_thread(_upload_sync, bucket, path, content, content_type)


async def signed_url(path: str, ttl_seconds: int = 300) -> str:
    """Generate a fresh short-lived signed URL for downloading an
    object. Default TTL 5 minutes — long enough for the FE to redirect,
    short enough that screenshots / leaked Slack messages expire fast.
    """
    bucket = _bucket()
    return await asyncio.to_thread(_signed_url_sync, bucket, path, ttl_seconds)


async def download_bytes(path: str) -> Optional[bytes]:
    """Fetch a stored object back as bytes. Used by the PDF renderer
    to embed previously-uploaded logos/signatures without doing a
    public HTTP fetch (which would require the asset bucket to be
    public). Returns None on failure rather than raising — render
    should be defensive.
    """
    def _sync():
        try:
            return _client().storage.from_(_bucket()).download(path)
        except Exception:
            logger.exception("Supabase Storage download failed: path=%s", path)
            return None
    return await asyncio.to_thread(_sync)
