# Voice Co-Driver

Talk to your home Mac's `claude` CLI from the car via Telegram voice messages.
Dictate a request, Claude works on your subscription (no API key), and you get a
spoken voice note back. The session is continuous across the drive via a
persisted `session_id`.

## Architecture

A Python bot runs on the Mac. It receives a Telegram **voice note**, transcribes
it locally (Whisper), pipes the text into the `claude` CLI in headless print mode
(`claude -p --resume`), captures the text result, converts it to speech
(`say` → OGG/Opus), and replies as a Telegram **voice message**.

```
voice note → Whisper STT → claude -p --resume → say + ffmpeg → OGG/Opus → sendVoice
```

## Modules

| File | Responsibility |
|------|----------------|
| `codriver/config.py` | env vars, whitelist, paths, voice/opus/ffmpeg settings |
| `codriver/stt.py`    | `transcribe(path) -> str` (faster-whisper) |
| `codriver/brain.py`  | `ask_claude(prompt) -> result` via subprocess; session continuity |
| `codriver/tts.py`    | `to_voice_ogg(text, voice) -> ogg_path`; `strip_for_speech(text)` |
| `codriver/bot.py`    | Telegram handlers, wiring, request lock |

## Setup

```bash
cd ~/codriver
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg
```

Create a bot token from [@BotFather](https://t.me/BotFather) (`/newbot`) and get
your numeric user ID from [@userinfobot](https://t.me/userinfobot), then put them
in `.env` (never committed):

```
TELEGRAM_BOT_TOKEN=123456:ABC...
ALLOWED_USER_ID=YOUR_NUMERIC_ID
CODRIVER_VOICE=Samantha
```

## Run

```bash
cd ~/codriver
source .venv/bin/activate
set -a && source .env && set +a
python -m codriver.bot
```

For a drive, keep it alive under tmux so it survives terminal close:

```bash
tmux new -s codriver 'cd ~/codriver && source .venv/bin/activate && set -a && source .env && set +a && python -m codriver.bot'
```

## Tests

Pure-function tests (no network, no token, no model download):

```bash
.venv/bin/python -m pytest tests/ -v
```

## Notes on this Mac

- **TTS voice**: defaults to `Samantha` (installed). `Ava` is NOT installed —
  `say` returns exit code 0 for a missing voice and silently uses the OS default,
  so `tts.py` validates the configured voice against `say -v '?'` and falls back
  to `Samantha` if absent.
- **ffmpeg**: resolved from PATH, falling back to the Homebrew build at
  `/opt/homebrew/bin/ffmpeg`.
- **Opus**: mono, 32k bitrate, `libopus` — required format for Telegram voice
  messages.

## Security (read before first real use)

This bot is a **remote-code-execution channel into your Mac**.

1. **Whitelist your user ID** (`is_allowed`) — enforced; without it anyone who
   finds the bot can drive your machine.
2. **Sandbox cwd** — Claude runs only in `~/codriver/sandbox` on a throwaway
   branch. Never point `CODRIVER_WORKDIR` at a real repo while using
   `--dangerously-skip-permissions`.
3. **Secrets** — bot token + IDs live in `.env` (gitignored). A leaked token = a
   shell. Rotate via BotFather if exposed.
4. Before pointing this at real repos, implement hook-based voice approval and
   run a full security audit on the whole pipeline.

This is the **MVP (Stages 1–4)**. Interactive questions/permissions, edge-case
hardening, and voice/UX polish are designed for a follow-on plan.
