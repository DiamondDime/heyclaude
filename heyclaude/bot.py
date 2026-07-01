import asyncio
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import TOKEN, WORK_DIR, SESSION_FILE, is_allowed
from .stt import transcribe
from .brain import ask_claude, ClaudeUsageLimitError, ClaudeAuthError
from .tts import to_voice_ogg
from . import commands, runtime

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("heyclaude")

# Serialize: one Claude task at a time. A second voice note waits its turn.
_lock = asyncio.Lock()

# Reject oversized notes before download+transcribe. Telegram already caps
# getFile at 20MB; the duration cap stops one accidental long recording from
# hogging the single Claude lock for the whole drive.
MAX_VOICE_SECONDS = 300
MAX_VOICE_BYTES = 20 * 1024 * 1024

USAGE_LIMIT_MSG = (
    "You've hit your Claude usage limit. It resets later — you can stop sending "
    "for now, your session is saved."
)
AUTH_MSG = (
    "Claude isn't signed in on your Mac, so I can't run anything. You'll need to "
    "fix that when you're stopped."
)
BROKE_MSG = "Something broke — your session is saved. Try again."


def _allowed(update: Update) -> bool:
    user = update.effective_user
    return user is not None and is_allowed(user.id)


async def _send_voice(update: Update, text: str) -> None:
    """Speak `text` back as a Telegram voice note, always cleaning the temp file."""
    ogg = await asyncio.to_thread(to_voice_ogg, text)
    try:
        with open(ogg, "rb") as f:
            await update.message.reply_voice(voice=f)
    finally:
        if os.path.exists(ogg):
            os.remove(ogg)


async def _safe_say(update: Update, text: str) -> None:
    """Best-effort: deliver `text` as both message and voice, never raising.
    Used on error paths where TTS itself may also be failing."""
    try:
        await update.message.reply_text("⚠️ " + text)
    except Exception:
        log.exception("could not deliver text notice")
    try:
        await _send_voice(update, text)
    except Exception:
        log.exception("could not deliver voice notice")


def apply_command(cmd: dict) -> str:
    """Apply a parsed command to runtime state; return a spoken confirmation."""
    action = cmd["action"]
    if action == "set_effort":
        runtime.set("effort", cmd["value"])
        return f"Effort set to {cmd['value']}."
    if action == "set_model":
        runtime.set("model", cmd["value"])
        return f"Model set to {cmd.get('label') or cmd['value']}."
    if action == "reset":
        try:
            SESSION_FILE.unlink()
        except OSError:
            pass
        return "Started a fresh session."
    if action == "show_config":
        s = runtime.snapshot()
        return f"Model {commands.friendly_model(s['model'])}, effort {s['effort']}."
    return "Unknown command."


# --- voice (prompt or spoken command) --------------------------------------
async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        user = update.effective_user
        log.warning("blocked update from %s", user and user.id)
        return

    # Reject oversized notes up front (before paying for download + Whisper).
    voice = update.message.voice
    if voice.duration and voice.duration > MAX_VOICE_SECONDS:
        await _safe_say(update, "That voice note is too long — keep it under 5 minutes.")
        return
    if voice.file_size and voice.file_size > MAX_VOICE_BYTES:
        await _safe_say(update, "That voice note is too large to process.")
        return

    try:
        # 1. download + transcribe
        tg_file = await voice.get_file()
        fd, in_path = tempfile.mkstemp(suffix=".oga")
        os.close(fd)
        try:
            await tg_file.download_to_drive(in_path)
            text = await asyncio.to_thread(transcribe, in_path)
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)

        if not text:
            await update.message.reply_text("🤔 Didn't catch that — try again.")
            return

        # 2. a spoken command (e.g. "use opus", "set effort to high") is applied
        #    instead of being sent to Claude.
        cmd = commands.parse_command(text)
        if cmd:
            reply = apply_command(cmd)
            await update.message.reply_text(f"⚙️ {text}\n{reply}")
            await _send_voice(update, reply)
            return

        # 3. instant ack so you're not driving in silence
        await update.message.reply_text(f"🎤 {text}\n🔧 working…")

        # 4. run Claude with the current runtime effort + model, then speak it
        async with _lock:
            result = await asyncio.to_thread(
                ask_claude, text, SESSION_FILE, runtime.get("effort"), runtime.get("model")
            )
        await _send_voice(update, result)
    except ClaudeUsageLimitError:
        # Persistent — telling the driver to STOP retrying is the real safety win.
        log.warning("claude usage limit hit")
        await _safe_say(update, USAGE_LIMIT_MSG)
    except ClaudeAuthError:
        log.warning("claude not authenticated")
        await _safe_say(update, AUTH_MSG)
    except Exception:  # never strand the driver in silence — covers STT/download too
        log.exception("task failed")
        await _safe_say(update, BROKE_MSG)


# --- slash commands --------------------------------------------------------
async def cmd_effort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    arg = context.args[0].lower() if context.args else ""
    if arg not in commands.EFFORT_LEVELS:
        levels = " ".join(sorted(commands.EFFORT_LEVELS))
        await update.message.reply_text(f"Usage: /effort <{levels}>")
        return
    await update.message.reply_text("⚙️ " + apply_command({"action": "set_effort", "value": arg}))


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    raw = " ".join(context.args).strip() if context.args else ""
    cmd = commands.parse_command(f"/model {raw}") if raw else None
    if not cmd:
        await update.message.reply_text("Usage: /model <opus|sonnet|haiku>")
        return
    await update.message.reply_text("⚙️ " + apply_command(cmd))


async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await update.message.reply_text("⚙️ " + apply_command({"action": "show_config"}))


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await update.message.reply_text("⚙️ " + apply_command({"action": "reset"}))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await update.message.reply_text(
        "Send a voice note to talk to Claude.\n\n"
        "Switch settings (type, or just say it):\n"
        "• /effort low|medium|high|xhigh|max   — or say \"set effort to high\"\n"
        "• /model opus|sonnet|haiku            — or say \"use opus\"\n"
        "• /config                             — or say \"what's my config\"\n"
        "• /reset                              — or say \"new session\""
    )


async def _set_commands(app: Application) -> None:
    await app.bot.set_my_commands([
        ("effort", "reasoning effort: low|medium|high|xhigh|max"),
        ("model", "switch model: opus|sonnet|haiku"),
        ("config", "show current model + effort"),
        ("reset", "start a fresh session"),
        ("help", "how to use the bot"),
    ])


async def _on_error(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Slash handlers have no try/except of their own; this keeps them as graceful
    # as on_voice — the user gets a reply instead of silence on any failure.
    log.exception("handler error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Something broke — try again.")
        except Exception:
            pass


def main():
    # claude runs with cwd=WORK_DIR, so a missing workspace would crash on the
    # first voice note (e.g. env-only start, or the dir got removed). Ensure it.
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    app = Application.builder().token(TOKEN).post_init(_set_commands).build()
    app.add_handler(CommandHandler("effort", cmd_effort))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler(["help", "start"], cmd_help))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_error_handler(_on_error)
    log.info("Hey Claude running. Send a voice note.")
    # bootstrap_retries=-1: a network blip during startup (getMe/Initialize)
    # otherwise aborts the whole process with exit 1. Retry the bootstrap
    # forever so a flaky moment at launch can't leave you with a dead bot.
    # (Once polling is up, PTB already retries network drops indefinitely.)
    app.run_polling(bootstrap_retries=-1)


if __name__ == "__main__":
    main()
