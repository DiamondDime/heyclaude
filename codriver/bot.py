import asyncio
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from .config import TOKEN, WORK_DIR, SESSION_FILE, is_allowed
from .stt import transcribe
from .brain import ask_claude
from .tts import to_voice_ogg

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("codriver")

# Serialize: one Claude task at a time. A second voice note waits its turn.
_lock = asyncio.Lock()


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Fail closed: anonymous / channel / service updates have no user — never run.
    if user is None or not is_allowed(user.id):
        log.warning("blocked update from %s", user and user.id)
        return

    try:
        # 1. download + transcribe
        tg_file = await update.message.voice.get_file()
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

        # 2. instant ack so you're not driving in silence
        await update.message.reply_text(f"🎤 {text}\n🔧 working…")

        # 3. run Claude (serialized), then speak the result
        async with _lock:
            result = await asyncio.to_thread(ask_claude, text, SESSION_FILE)
        ogg = await asyncio.to_thread(to_voice_ogg, result)
        # Always unlink the temp OGG, even if reply_voice raises (flaky car signal,
        # Telegram 5xx, timeout). Otherwise every failed send leaks a file and the
        # user — told to "retry" — multiplies the leak over a long drive.
        try:
            with open(ogg, "rb") as f:
                await update.message.reply_voice(voice=f)
        finally:
            if os.path.exists(ogg):
                os.remove(ogg)
    except Exception:  # never strand the driver in silence — covers STT/download too
        log.exception("task failed")
        try:
            await update.message.reply_text(
                "⚠️ Something broke — your session is saved. Try again."
            )
        except Exception:
            log.exception("could not deliver error notice")


def main():
    # claude runs with cwd=WORK_DIR, so a missing workspace would crash on the
    # first voice note (e.g. env-only start, or the dir got removed). Ensure it.
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    log.info("Co-Driver running. Send a voice note.")
    app.run_polling()


if __name__ == "__main__":
    main()
