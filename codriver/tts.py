import os
import re
import subprocess
import tempfile
from functools import lru_cache

from .config import (
    DEFAULT_TTS_VOICE,
    DEFAULT_TTS_FALLBACK_VOICE,
    FFMPEG_PATH,
    OPUS_BITRATE,
    OPUS_CHANNELS,
)


def strip_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ... code omitted ... ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", "", text)  # inline code
    text = re.sub(r"[*_#>|]", "", text)  # markdown symbols
    text = re.sub(r"\n{2,}", ". ", text)
    return text.strip()


@lru_cache(maxsize=1)
def _installed_voices() -> frozenset:
    """Parse `say -v '?'` into the set of installed voice names.

    Each line looks like:  `Samantha            en_US    # Hello! ...`
    The voice name is the first whitespace-delimited token. We must validate
    against this set because `say` returns exit code 0 for a missing voice and
    silently falls back to the OS default voice.
    """
    try:
        out = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return frozenset()
    voices = set()
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        voices.add(line.split()[0])
    return frozenset(voices)


def _resolve_voice(voice: str) -> str:
    installed = _installed_voices()
    if not installed:
        # Could not enumerate voices — trust the caller's choice.
        return voice
    if voice in installed:
        return voice
    return DEFAULT_TTS_FALLBACK_VOICE


def to_voice_ogg(text: str, voice: str = DEFAULT_TTS_VOICE) -> str:
    spoken = strip_for_speech(text) or "Done."
    voice = _resolve_voice(voice)
    fd_a, aiff = tempfile.mkstemp(suffix=".aiff")
    os.close(fd_a)
    fd_o, ogg = tempfile.mkstemp(suffix=".ogg")
    os.close(fd_o)
    success = False
    try:
        # `--` separates flags from text so a leading '-' isn't parsed as a flag.
        say = subprocess.run(
            ["say", "-v", voice, "-o", aiff, "--", spoken],
            capture_output=True,
            text=True,
        )
        if say.returncode != 0:
            raise RuntimeError(say.stderr.strip() or "say exited non-zero")
        # Telegram voice messages REQUIRE OGG/Opus, mono.
        ff = subprocess.run(
            [
                FFMPEG_PATH,
                "-y",
                "-i",
                aiff,
                "-ac",
                str(OPUS_CHANNELS),
                "-c:a",
                "libopus",
                "-b:a",
                OPUS_BITRATE,
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
        if os.path.exists(aiff):
            os.remove(aiff)
        # On failure, ffmpeg's `-y` may have left a partial/zero-byte ogg — drop it.
        if not success and os.path.exists(ogg):
            os.remove(ogg)
