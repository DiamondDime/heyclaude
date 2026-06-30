import asyncio
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from .config import TOKEN, ALLOWED_USER_ID, SESSION_FILE, is_allowed
from .stt import transcribe
from .brain import ask_claude
from .tts import to_voice_ogg

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("codriver")

# Serialize: one Claude task at a time. A second voice note waits its turn.
_lock = asyncio.Lock()


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        log.warning("blocked user %s", user.id)
        return

    # 1. download + transcribe
    tg_file = await update.message.voice.get_file()
    in_path = tempfile.mktemp(suffix=".oga")
    await tg_file.download_to_drive(in_path)
    try:
        text = await asyncio.to_thread(transcribe, in_path)
    finally:
        os.remove(in_path)

    if not text:
        await update.message.reply_text("🤔 Didn't catch that — try again.")
        return

    # 2. instant ack so you're not driving in silence
    await update.message.reply_text(f"🎤 {text}\n🔧 working…")

    # 3. run Claude (serialized), then speak the result
    try:
        async with _lock:
            result = await asyncio.to_thread(ask_claude, text, SESSION_FILE)
        ogg = await asyncio.to_thread(to_voice_ogg, result)
        with open(ogg, "rb") as f:
            await update.message.reply_voice(voice=f)
        os.remove(ogg)
    except Exception as e:  # never die silently mid-drive
        log.exception("task failed")
        await update.message.reply_text(f"⚠️ Error: {e}. Session is saved — retry.")


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    log.info("Co-Driver running. Send a voice note.")
    app.run_polling()


if __name__ == "__main__":
    main()
