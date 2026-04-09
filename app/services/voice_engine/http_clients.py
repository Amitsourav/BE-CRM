"""Shared persistent httpx.AsyncClient instances for voice-engine HTTP calls.

Every STT/LLM/TTS request was previously creating a fresh AsyncClient, which
means DNS + TCP + TLS handshake on every single turn. Per turn that was
costing ~100-200ms × 3 providers = up to 600ms of pure connection overhead.

Reusing a module-level client lets httpx keep keepalive connections open,
so subsequent requests reuse the same TCP socket. Ballpark: first request
to each host pays the full handshake (same as before); every subsequent
request is 150-300ms faster.

Clients are created lazily on first use via get_*() helpers so importing
this module doesn't require network at import time. They are kept alive
for the lifetime of the process — Railway recycles workers on deploy so
there's no long-lived leak concern.
"""
from __future__ import annotations

import httpx

# One keepalive pool per upstream. Separate clients so a stall in one
# provider can't back-pressure the others.
_sarvam: httpx.AsyncClient | None = None
_openrouter: httpx.AsyncClient | None = None
_smallest: httpx.AsyncClient | None = None
_deepgram: httpx.AsyncClient | None = None
_openai: httpx.AsyncClient | None = None


_LIMITS = httpx.Limits(
    max_keepalive_connections=8,
    max_connections=16,
    keepalive_expiry=60.0,
)


def get_sarvam_client() -> httpx.AsyncClient:
    global _sarvam
    if _sarvam is None:
        _sarvam = httpx.AsyncClient(
            base_url="https://api.sarvam.ai",
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
            limits=_LIMITS,
            http2=False,
        )
    return _sarvam


def get_openrouter_client() -> httpx.AsyncClient:
    global _openrouter
    if _openrouter is None:
        _openrouter = httpx.AsyncClient(
            base_url="https://openrouter.ai",
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0),
            limits=_LIMITS,
            http2=False,
        )
    return _openrouter


def get_smallest_client() -> httpx.AsyncClient:
    global _smallest
    if _smallest is None:
        _smallest = httpx.AsyncClient(
            base_url="https://waves-api.smallest.ai",
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
            limits=_LIMITS,
            http2=False,
        )
    return _smallest


def get_deepgram_client() -> httpx.AsyncClient:
    global _deepgram
    if _deepgram is None:
        _deepgram = httpx.AsyncClient(
            base_url="https://api.deepgram.com",
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0),
            limits=_LIMITS,
            http2=False,
        )
    return _deepgram


def get_openai_client() -> httpx.AsyncClient:
    global _openai
    if _openai is None:
        _openai = httpx.AsyncClient(
            base_url="https://api.openai.com",
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
            limits=_LIMITS,
            http2=False,
        )
    return _openai


async def close_all() -> None:
    """Close every persistent client. Call from FastAPI shutdown handler
    if you need graceful cleanup (otherwise the process exit handles it)."""
    for c in (_sarvam, _openrouter, _smallest, _deepgram, _openai):
        if c is not None:
            try:
                await c.aclose()
            except Exception:
                pass
