import os
import shutil
from pathlib import Path

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
WORK_DIR = Path(os.environ.get("CODRIVER_WORKDIR", str(Path.home() / "codriver" / "sandbox")))

# Default TTS voice: Samantha is actually installed on this Mac. Ava is NOT —
# `say` returns exit code 0 for a missing voice and silently uses the OS
# default, so we must pin to an installed voice and validate at runtime (tts.py).
DEFAULT_TTS_VOICE = os.environ.get("CODRIVER_VOICE", "Samantha")
DEFAULT_TTS_FALLBACK_VOICE = "Samantha"
# Backwards-compatible alias used elsewhere in the codebase.
VOICE = DEFAULT_TTS_VOICE

# Opus encode settings for Telegram voice notes (mono).
OPUS_BITRATE = "32k"
OPUS_CHANNELS = 1

# ffmpeg is the Homebrew build, not /usr/bin. Resolve via PATH, fall back to the
# known Homebrew location.
FFMPEG_PATH = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

WHISPER_MODEL = os.environ.get("CODRIVER_WHISPER", "base.en")
SESSION_FILE = WORK_DIR / ".codriver_session"
CLAUDE_TIMEOUT = int(os.environ.get("CODRIVER_TIMEOUT", "600"))


def is_allowed(user_id: int, allowed: int = ALLOWED_USER_ID) -> bool:
    return user_id == allowed and allowed != 0
