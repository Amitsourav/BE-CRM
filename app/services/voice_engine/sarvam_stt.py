import asyncio
import base64
import json
import logging

import httpx
import websockets

from app.config import get_settings

logger = logging.getLogger(__name__)


class SarvamSTT:

    BASE_URL = "https://api.sarvam.ai"

    async def transcribe(
        self,
        audio_bytes: bytes,
        language_code: str = "unknown",
        model: str = "saaras:v3",
        keywords: str = "",
    ) -> dict:
        """Transcribe audio bytes to text."""
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.BASE_URL}/speech-to-text",
                    headers={
                        "api-subscription-key": settings.sarvam_api_key,
                    },
                    files={
                        "file": ("audio.wav", audio_bytes, "audio/wav"),
                    },
                    data={
                        "model": model,
                        "language_code": language_code,
                        "with_timestamps": "false",
                        "with_diarization": "false",
                        # Sarvam accepts a comma-separated hotword list as
                        # "vocab" on some models; unknown fields are ignored.
                        **({"vocab": keywords} if keywords else {}),
                    },
                )

                if response.status_code != 200:
                    return {
                        "transcript": "",
                        "language_code": "en-IN",
                        "detected_language": "en",
                        "error": response.text,
                    }

                try:
                    data = response.json()
                except ValueError as e:
                    return {
                        "transcript": "",
                        "language_code": "en-IN",
                        "detected_language": "en",
                        "error": f"invalid JSON: {e}",
                    }

                transcript = data.get("transcript", "") if isinstance(data, dict) else ""
                lang = data.get("language_code", "en-IN") if isinstance(data, dict) else "en-IN"

                from app.services.language_detector import detect_language
                detected = detect_language(transcript)

                return {
                    "transcript": transcript,
                    "language_code": lang,
                    "detected_language": detected,
                }

        except (httpx.RequestError, httpx.TimeoutException) as e:
            return {
                "transcript": "",
                "language_code": "en-IN",
                "detected_language": "en",
                "error": str(e),
            }

    STREAMING_URI = "wss://api.sarvam.ai/speech-to-text-streaming"

    async def transcribe_stream(
        self,
        audio_bytes: bytes,
        timeout_seconds: float = 8.0,
        model: str = "saaras:v3",
        keywords: str = "",
    ) -> dict:
        """Transcribe via Sarvam streaming WebSocket. Falls back to batch on
        ANY error so callers always get a usable result.

        NOTE: The exact Sarvam streaming protocol may differ from what's
        implemented here. If streaming fails repeatedly in production logs,
        check Sarvam's WS API reference and adjust the message shapes below.
        """
        if not audio_bytes:
            return {"transcript": "", "language_code": "en-IN", "detected_language": "en"}

        settings = get_settings()
        transcript_parts = []

        try:
            # Convert WAV to raw PCM 16kHz LE if needed.
            # Audio coming in here is already 8kHz mono PCM (from mulaw_to_wav).
            # Strip 44-byte WAV header for raw LINEAR16.
            pcm_payload = audio_bytes[44:] if audio_bytes.startswith(b"RIFF") else audio_bytes

            async with asyncio.wait_for(
                websockets.connect(
                    self.STREAMING_URI,
                    extra_headers={"api-subscription-key": settings.sarvam_api_key},
                    ping_interval=None,
                    open_timeout=3.0,
                ),
                timeout=timeout_seconds,
            ) as ws:
                # Initial config frame
                await ws.send(
                    json.dumps(
                        {
                            "type": "config",
                            "data": {
                                "language_code": "unknown",
                                "model": model,
                                "encoding": "LINEAR16",
                                "sample_rate_hertz": 8000,
                            },
                        }
                    )
                )

                # Stream audio in 200ms chunks (8000 Hz × 0.2 s × 2 bytes = 3200)
                chunk_size = 3200
                for i in range(0, len(pcm_payload), chunk_size):
                    chunk = pcm_payload[i : i + chunk_size]
                    await ws.send(
                        json.dumps(
                            {
                                "type": "audio",
                                "data": base64.b64encode(chunk).decode(),
                            }
                        )
                    )

                # Signal end of stream
                await ws.send(json.dumps({"type": "end"}))

                # Drain incoming messages until terminal event or timeout
                try:
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        msg_type = data.get("type", "")
                        if msg_type in ("transcript", "partial", "interim"):
                            text = data.get("text", "") or data.get("transcript", "")
                            if text:
                                transcript_parts.append(text)
                        elif msg_type in ("final", "end"):
                            text = data.get("text", "") or data.get("transcript", "")
                            if text:
                                transcript_parts.append(text)
                            break
                except asyncio.TimeoutError:
                    pass

            full = " ".join(transcript_parts).strip()
            if not full:
                # Streaming returned nothing — fall back to batch
                logger.info("streaming STT returned empty, falling back to batch")
                return await self.transcribe(audio_bytes, model=model, keywords=keywords)

            from app.services.language_detector import detect_language
            return {
                "transcript": full,
                "language_code": "auto",
                "detected_language": detect_language(full),
            }

        except (asyncio.TimeoutError, OSError, websockets.WebSocketException) as e:
            logger.info("streaming STT failed (%s), falling back to batch", e)
            return await self.transcribe(audio_bytes, model=model, keywords=keywords)
        except Exception as e:
            logger.warning("streaming STT unexpected error: %s — fallback to batch", e)
            return await self.transcribe(audio_bytes, model=model, keywords=keywords)


sarvam_stt = SarvamSTT()
