import audioop
import base64
import io
import re
import wave


def mulaw_to_wav(mulaw_bytes: bytes) -> bytes:
    """Convert μ-law @ 8kHz (Plivo) → WAV (Sarvam STT)."""
    pcm_bytes = audioop.ulaw2lin(mulaw_bytes, 2)

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(pcm_bytes)

    return wav_buffer.getvalue()


def wav_to_mulaw(wav_bytes: bytes) -> bytes:
    """Convert WAV (Sarvam TTS) → μ-law @ 8kHz (Plivo)."""
    if not wav_bytes:
        return b""

    try:
        wav_buffer = io.BytesIO(wav_bytes)
        with wave.open(wav_buffer, "rb") as wav_file:
            pcm_bytes = wav_file.readframes(wav_file.getnframes())
            channels = wav_file.getnchannels()
            sampwidth = wav_file.getsampwidth()
            framerate = wav_file.getframerate()

        if channels == 2:
            pcm_bytes = audioop.tomono(pcm_bytes, sampwidth, 1, 1)

        if sampwidth != 2:
            pcm_bytes = audioop.lin2lin(pcm_bytes, sampwidth, 2)

        if framerate != 8000:
            pcm_bytes, _ = audioop.ratecv(
                pcm_bytes, 2, 1, framerate, 8000, None
            )

        return audioop.lin2ulaw(pcm_bytes, 2)
    except Exception:
        return b""


def decode_plivo_audio(payload: str) -> bytes:
    """Decode base64 mulaw payload from Plivo. Returns b'' on bad input."""
    if not payload:
        return b""
    try:
        return base64.b64decode(payload, validate=False)
    except Exception:
        return b""


def encode_for_plivo(mulaw_bytes: bytes) -> str:
    """Encode mulaw bytes as base64 for Plivo."""
    return base64.b64encode(mulaw_bytes).decode()


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।])\s+")


def split_for_tts(text: str, max_chars: int = 450) -> list:
    """Split long text into ≤max_chars chunks at sentence boundaries.

    Handles English (.!?) and Hindi (।) terminators. Oversized lone
    sentences are hard-cut at max_chars.
    """
    if not text:
        return []
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    sentences = _SENTENCE_SPLIT_RE.split(text)
    chunks: list = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        # Sentence itself oversized — hard split it
        while len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        # Pack greedily
        candidate = (current + " " + sentence).strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def concat_wav(wav_chunks: list) -> bytes:
    """Concatenate multiple WAV blobs into one. Assumes matching format.

    Reads PCM frames from each via the wave module and writes a single
    WAV envelope using the format params of the first chunk.
    """
    valid = [w for w in wav_chunks if w]
    if not valid:
        return b""
    if len(valid) == 1:
        return valid[0]

    nchannels = sampwidth = framerate = None
    pcm_parts: list = []
    for blob in valid:
        with wave.open(io.BytesIO(blob), "rb") as w:
            if nchannels is None:
                nchannels = w.getnchannels()
                sampwidth = w.getsampwidth()
                framerate = w.getframerate()
            pcm_parts.append(w.readframes(w.getnframes()))

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        w.writeframes(b"".join(pcm_parts))
    return out.getvalue()


def is_silence_mulaw(mulaw_bytes: bytes, threshold: int = 200) -> bool:
    """Detect silence in μ-law audio via RMS energy across the full chunk."""
    if not mulaw_bytes:
        return True
    try:
        pcm = audioop.ulaw2lin(mulaw_bytes, 2)
        rms = audioop.rms(pcm, 2)
        return rms < threshold
    except Exception:
        return True
