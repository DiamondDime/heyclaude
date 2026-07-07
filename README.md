# heyclaude

Talk to **Claude Code** by voice while you drive.

Send a Telegram voice note from your phone; the bot on your Mac transcribes it,
runs it through the `claude` CLI, and replies with a spoken voice note. Claude
works on **your Claude Code subscription** — no paid Anthropic API key required.

```
voice note → Whisper (local) → claude -p --resume → TTS → voice reply
```

Each drive is one continuous conversation: the session id is persisted, so
follow-up requests keep their context.

---

## Safety — read this first

heyclaude is a **remote-code-execution channel into your Mac.** Treat it that way.

- It runs `claude --dangerously-skip-permissions`, so Claude executes commands,
  edits files, and hits the network **without asking for approval.**
- The **only** thing standing between a Telegram message and your machine is the
  single allowed Telegram user ID. There is no second guard. Anyone who can post
  to your bot as that user gets a shell on your Mac.
- A working directory is **not** a sandbox. Claude can read and write outside it.
  Point heyclaude at a **dedicated, throwaway workspace** — never your home folder
  and never a real repo with secrets, SSH keys, or production code.
- Use a fresh Telegram bot token, keep it out of any directory Claude can reach,
  and rotate it if it is ever exposed.

If that trade-off is not acceptable to you, do not run this.

---

## Driving safety — also read this

heyclaude is a **hands-free coding aid, not an autopilot.** It is designed so you
never have to look at or touch a screen — you talk, you listen. But:

- **The road comes first.** Do not let a coding problem pull your attention off
  driving. If a reply needs real thought, pull over.
- **Obey your local laws.** Rules on phone/device use while driving vary by
  country and state. Complying with them is your responsibility.
- Treat it like a podcast you can talk back to — background, not foreground.

You use this tool at your own risk. It is provided "as is" (see the MIT license),
with no warranty, and the author is not liable for how you use it.

---

## Privacy — where your words go

| Step | Where it runs |
|------|---------------|
| Your spoken note | through **Telegram's servers** (like any Telegram message) |
| Transcription (speech → text) | **locally** on your Mac (Whisper) |
| Claude's work | **locally** via the `claude` CLI on your subscription |
| Reply spoken back | **`say`**: fully local · **ElevenLabs**: reply *text* is sent to ElevenLabs to synthesize |

Nothing is sent to the author of heyclaude. If you want zero third-party speech
services, choose the `say` backend — then only Telegram is in the loop.

---

## Using your Claude subscription

heyclaude drives the `claude` CLI you're already signed into, so it runs on your
**Claude Code subscription** rather than the paid API. Automating your own
account this way is for personal use — review the
[Anthropic Usage Policies](https://www.anthropic.com/legal/aup) and your plan's
terms, and don't share one login across people. heyclaude is an independent
project and is not affiliated with or endorsed by Anthropic.

---

## Requirements

- **macOS** (uses the `say` voice and Homebrew `ffmpeg`)
- **ffmpeg** — `brew install ffmpeg`
- A working **Claude Code login** (`claude` on your PATH, already signed in)
- A **Telegram bot** token from [@BotFather](https://t.me/BotFather) and your
  numeric user id from [@userinfobot](https://t.me/userinfobot)
- Python **3.11+**
- Optional: an **ElevenLabs** API key for higher-quality speech

---

## Install

```bash
pipx install .
```

This installs the `heyclaude` command.

---

## Set up: `heyclaude init`

```bash
heyclaude init
```

The wizard walks you through everything:

1. **Safety notice** — the warning above, up front.
2. **Telegram bot token** — pasted and verified against Telegram (it shows your
   bot's `@username` on success).
3. **Telegram user id** — the numeric id allowed to talk to the bot.
4. **TTS backend** — pick `elevenlabs` or `say` (see below).
5. **Workspace** — defaults to `~/heyclaude-workspace`. It is created, seeded with
   a `CLAUDE.md` (an existing one is left untouched), and initialized as a git
   repo on a `heyclaude-work` branch. The wizard **refuses your home folder, real
   repos, and data dirs** like `~/Documents` or `~/.ssh` — Claude runs destructive
   commands here, so it must be a dedicated, throwaway directory.
6. Writes `~/.config/heyclaude/config.toml` (permissions `0600`).

Config lives at `~/.config/heyclaude/config.toml`, outside the repo. You never
have to edit it by hand.

---

## Run: `start` / `stop` / `status`

```bash
heyclaude doctor           # full preflight: ffmpeg, Telegram, TTS, claude, speech model
heyclaude start            # start the bot (begins polling Telegram)
heyclaude start --check    # quick check (platform + token + TTS), then exit
heyclaude stop             # stop the running bot
heyclaude status           # show running (with PID) or stopped
```

Run **`heyclaude doctor`** once before your first drive. Unlike `--check`, it also
runs one real `claude` turn to confirm you're signed in and that your build
accepts the model/effort flags, and it pre-downloads the speech model — so you
don't discover a problem on the road. The bot also **auto-restarts itself** if it
crashes, so a transient hiccup won't leave you stranded mid-drive.

Then put on your seatbelt, open the chat with your bot, and start sending voice
notes.

### Keep it running (optional)

`heyclaude start` runs in the foreground and stops if you close the terminal. To
run it as a background service that **starts at login and restarts on crash**:

```bash
heyclaude install-service     # install + start the launchd agent
heyclaude uninstall-service   # stop + remove it
```

The service runs under `caffeinate -i`, which prevents *idle* system sleep **on
battery** (so the bot keeps polling while you drive). Caveats: closing the laptop
lid still pauses it, and once installed, `heyclaude stop` won't keep it down —
launchd relaunches it, so use **`heyclaude uninstall-service`** to stop it for
good. Logs land in `~/.config/heyclaude/bot.log`. Save your token via `heyclaude
init` (not just an env var) before installing — the service runs with a clean
environment.

---

## Text-to-speech backends

heyclaude can speak its replies three ways (set `[tts].backend` in your config):

- **`kokoro`** *(recommended)* — a local neural voice (Kokoro-82M via mlx-audio)
  that runs on Apple Silicon. **Natural intonation, free, unlimited, offline —
  no quota.** Install the extra once:

  ```bash
  pip install 'heyclaude[kokoro]'
  brew install espeak-ng        # for out-of-dictionary words (or the bundled loader covers it)
  ```

  Pick a voice with `[tts.kokoro].voice` (e.g. `af_heart`, `am_adam`, `bf_emma`,
  `bm_george`). First reply loads the model (~330 MB, one-time download).
- **`elevenlabs`** — cloud, very human, but **metered** (the free tier is ~10k
  credits/month ≈ a handful of replies). Needs an API key; if the quota runs out
  mid-reply it automatically falls back to `say` so you still hear the answer.
- **`say`** — macOS built-in speech. Zero setup, works offline, but robotic. This
  is also the universal fallback if `kokoro`/`elevenlabs` ever fail.

**Get a free ElevenLabs API key: https://try.elevenlabs.io/ihajsceo1jo8**

> That's a referral link — signing up through it supports heyclaude at no extra
> cost to you. For most people the free local **`kokoro`** voice is the best
> default; ElevenLabs is there when you want a specific cloud voice.

---

## Controlling Claude from the bot

Claude defaults to **model `claude-opus-4-8` at `xhigh` effort**. Switch either on
the fly — **type a slash command, or just say it** in a voice note. Changes persist
across restarts.

| Slash command | Or say | Effect |
|---|---|---|
| `/effort low\|medium\|high\|xhigh\|max` | "set effort to high" | reasoning depth per turn |
| `/model opus\|sonnet\|haiku` | "use opus" / "switch to sonnet" | which Claude model runs |
| `/config` | "what's my config" | report current model + effort |
| `/reset` | "new session" | drop session context, start fresh |

Higher effort = deeper reasoning, slower replies. `opus` is the most capable;
`haiku` is fastest and cheapest.

---

## How it works

| File | Responsibility |
|------|----------------|
| `heyclaude/config.py` | config loading (env > `config.toml` > defaults), whitelist, paths |
| `heyclaude/stt.py`    | local Whisper transcription |
| `heyclaude/brain.py`  | runs `claude -p --resume` with the chosen model + effort, keeps session continuity |
| `heyclaude/tts.py`    | text → spoken OGG/Opus (ElevenLabs or `say`) |
| `heyclaude/bot.py`    | Telegram handlers (voice + slash commands) and wiring |
| `heyclaude/commands.py` | parses in-bot commands (effort / model / config / reset) |
| `heyclaude/runtime.py`  | live-switchable effort + model, persisted to `runtime.json` |
| `heyclaude/cli.py`    | `init` / `start` / `stop` / `status` |

Transcription runs locally with Whisper. Replies are encoded as mono Opus, the
format Telegram voice messages require.

---

## Tests

```bash
pytest tests/ -v
```

The included tests are pure functions — no network, token, or model download.

---

## Author & support

Built by **diamonddime**.

- ⭐ **Star the repo** if heyclaude saved you a commute — that's what helps others find it.
- 💬 Questions, ideas, or bugs: open an issue.

---

## License

[MIT](LICENSE) — Copyright (c) 2026 diamonddime.
