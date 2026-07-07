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

# Telegram's getFile caps downloads at 20MB; reject bigger files up front.
MAX_DOC_BYTES = 20 * 1024 * 1024

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


# --- shared prompt path (voice transcript or typed text) -------------------
async def _handle_prompt(update: Update, text: str, echo: str) -> None:
    """Apply a command, or run Claude for `text` and speak the reply.

    Shared by voice notes (after transcription) and typed text messages so the
    two channels behave identically. `echo` is prepended to the working ack —
    voice echoes the transcript back so the driver hears what was understood.
    All Claude error paths are handled here so neither channel strands you.
    """
    try:
        # a command (e.g. "use opus", "set effort to high") is applied instead
        # of being sent to Claude.
        cmd = commands.parse_command(text)
        if cmd:
            reply = apply_command(cmd)
            await update.message.reply_text(f"⚙️ {text}\n{reply}")
            await _send_voice(update, reply)
            return

        # instant ack so you're not driving in silence
        await update.message.reply_text(f"{echo}🔧 working…")

        # run Claude with the current runtime effort + model, then speak it
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
    except Exception:  # never strand the driver in silence
        log.exception("task failed")
        await _safe_say(update, BROKE_MSG)


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

    # download + transcribe
    try:
        tg_file = await voice.get_file()
        fd, in_path = tempfile.mkstemp(suffix=".oga")
        os.close(fd)
        try:
            await tg_file.download_to_drive(in_path)
            text = await asyncio.to_thread(transcribe, in_path)
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
    except Exception:  # covers download + STT failures
        log.exception("voice download/transcribe failed")
        await _safe_say(update, BROKE_MSG)
        return

    if not text:
        await update.message.reply_text("🤔 Didn't catch that — try again.")
        return

    await _handle_prompt(update, text, echo=f"🎤 {text}\n")


# --- text (typed prompt or command) ----------------------------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        user = update.effective_user
        log.warning("blocked update from %s", user and user.id)
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    await _handle_prompt(update, text, echo="")


# --- files (documents + photos) --------------------------------------------
def _safe_name(name: str) -> str:
    """basename only (no path traversal) + a filesystem-friendly charset."""
    name = os.path.basename(name or "").replace("\x00", "").strip()
    kept = "".join(c for c in name if c.isalnum() or c in " ._-()[]").strip()
    return (kept or "file")[:128]


def _inbox_dest(name: str, unique: str):
    """A collision-free path under WORK_DIR/inbox for an incoming attachment."""
    inbox = WORK_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / _safe_name(name)
    if dest.exists():
        dest = inbox / f"{unique}_{dest.name}"
    return dest


async def _dispatch_file(update: Update, dest, caption: str, default: str, echo: str) -> None:
    """Hand a saved attachment to Claude: the caption is the instruction, or a
    sensible default if there's none. Claude opens the file with its own tools."""
    if caption:
        prompt = f'I received a file you sent and saved it to "{dest}". Your note about it: {caption}'
    else:
        prompt = f'I received a file you sent and saved it to "{dest}". {default}'
    await _handle_prompt(update, prompt, echo=echo)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        user = update.effective_user
        log.warning("blocked update from %s", user and user.id)
        return

    doc = update.message.document
    if doc.file_size and doc.file_size > MAX_DOC_BYTES:
        await _safe_say(update, "That file is too big — Telegram caps me at 20 megabytes.")
        return

    try:
        tg_file = await doc.get_file()
        dest = _inbox_dest(doc.file_name or doc.file_unique_id, doc.file_unique_id)
        await tg_file.download_to_drive(str(dest))
    except Exception:
        log.exception("document download failed")
        await _safe_say(update, BROKE_MSG)
        return

    await _dispatch_file(
        update, dest, (update.message.caption or "").strip(),
        default="Open and read it, then tell me what it is and what's in it.",
        echo=f"📎 {dest.name}\n",
    )


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        user = update.effective_user
        log.warning("blocked update from %s", user and user.id)
        return

    photos = update.message.photo
    if not photos:
        return
    photo = photos[-1]  # last entry is the highest-resolution size

    try:
        tg_file = await photo.get_file()
        dest = _inbox_dest(f"photo_{photo.file_unique_id}.jpg", photo.file_unique_id)
        await tg_file.download_to_drive(str(dest))
    except Exception:
        log.exception("photo download failed")
        await _safe_say(update, BROKE_MSG)
        return

    await _dispatch_file(
        update, dest, (update.message.caption or "").strip(),
        default="Look at this image and describe what's in it.",
        echo="🖼 photo\n",
    )


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
    # Plain typed text (but not slash commands, which the CommandHandlers above
    # already own) goes to Claude just like a transcribed voice note.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # Sent files (txt, markdown, PDF, code, …) and photos are saved into the
    # workspace so Claude can open them with its own tools.
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_error_handler(_on_error)
    log.info("Hey Claude running. Send a voice note.")
    # bootstrap_retries=-1: a network blip during startup (getMe/Initialize)
    # otherwise aborts the whole process with exit 1. Retry the bootstrap
    # forever so a flaky moment at launch can't leave you with a dead bot.
    # (Once polling is up, PTB already retries network drops indefinitely.)
    app.run_polling(bootstrap_retries=-1)


if __name__ == "__main__":
    main()
