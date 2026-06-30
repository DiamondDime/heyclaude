import os
import re
import subprocess
import tempfile
from functools import lru_cache

import httpx

from .config import (
    DEFAULT_TTS_VOICE,
    DEFAULT_TTS_FALLBACK_VOICE,
    FFMPEG_PATH,
    OPUS_BITRATE,
    OPUS_CHANNELS,
    TTS_BACKEND,
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
    ELEVENLABS_MODEL,
)


def strip_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ... code omitted ... ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", "", text)  # inline code
    text = re.sub(r"[*_#>|]", "", text)  # markdown symbols
    text = re.sub(r"\n{2,}", ". ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Backend: macOS `say` (local, free, robotic)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _installed_voices() -> frozenset:
    """Parse `say -v '?'` into the set of installed voice names. `say` returns
    exit code 0 for a missing voice and silently uses the OS default, so we must
    validate against this set."""
    try:
        out = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return frozenset()
    return frozenset(line.split()[0] for line in out.splitlines() if line.strip())


def _resolve_voice(voice: str) -> str:
    installed = _installed_voices()
    if not installed or voice in installed:
        return voice
    return DEFAULT_TTS_FALLBACK_VOICE


def _synthesize_say(spoken: str, voice: str) -> str:
    """Return path to an AIFF file synthesized by macOS `say`."""
    voice = _resolve_voice(voice)
    fd, aiff = tempfile.mkstemp(suffix=".aiff")
    os.close(fd)
    # `--` separates flags from text so a leading '-' isn't parsed as a flag.
    say = subprocess.run(
        ["say", "-v", voice, "-o", aiff, "--", spoken], capture_output=True, text=True
    )
    if say.returncode != 0:
        if os.path.exists(aiff):
            os.remove(aiff)
        raise RuntimeError(say.stderr.strip() or "say exited non-zero")
    return aiff


# ---------------------------------------------------------------------------
# Backend: ElevenLabs (cloud, neural, human intonation)
# ---------------------------------------------------------------------------
def _synthesize_elevenlabs(spoken: str) -> str:
    """Return path to an MP3 file synthesized by ElevenLabs."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    payload = {
        "text": spoken,
        "model_id": ELEVENLABS_MODEL,
        # Lower stability => more expressive intonation; speaker_boost adds presence.
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.8,
            "style": 0.25,
            "use_speaker_boost": True,
        },
    }
    with httpx.Client(timeout=60) as client:
        r = client.post(url, headers=headers, json=payload)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {r.text[:300]}")
    fd, mp3 = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    with open(mp3, "wb") as f:
        f.write(r.content)
    return mp3


def _synthesize(spoken: str, voice: str) -> str:
    if TTS_BACKEND == "elevenlabs":
        return _synthesize_elevenlabs(spoken)
    return _synthesize_say(spoken, voice)


# ---------------------------------------------------------------------------
# Shared: transcode raw audio (AIFF/MP3) -> Telegram OGG/Opus, mono
# ---------------------------------------------------------------------------
def to_voice_ogg(text: str, voice: str = DEFAULT_TTS_VOICE) -> str:
    spoken = strip_for_speech(text) or "Done."
    raw = _synthesize(spoken, voice)  # AIFF (say) or MP3 (elevenlabs)
    fd, ogg = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    success = False
    try:
        ff = subprocess.run(
            [
                FFMPEG_PATH, "-y", "-i", raw,
                "-ac", str(OPUS_CHANNELS),
                "-c:a", "libopus", "-b:a", OPUS_BITRATE,
                ogg,
            ],
            capture_output=True,
            text=True,
        )
        if ff.returncode != 0:
            raise RuntimeError(ff.stderr.strip() or "ffmpeg exited non-zero")
        success = True
        return ogg
    finally:
        if os.path.exists(raw):
            os.remove(raw)
        # On failure, ffmpeg's `-y` may have left a partial/zero-byte ogg — drop it.
        if not success and os.path.exists(ogg):
            os.remove(ogg)
