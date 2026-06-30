# codriver

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

codriver is a **remote-code-execution channel into your Mac.** Treat it that way.

- It runs `claude --dangerously-skip-permissions`, so Claude executes commands,
  edits files, and hits the network **without asking for approval.**
- The **only** thing standing between a Telegram message and your machine is the
  single allowed Telegram user ID. There is no second guard. Anyone who can post
  to your bot as that user gets a shell on your Mac.
- A working directory is **not** a sandbox. Claude can read and write outside it.
  Point codriver at a **dedicated, throwaway workspace** — never your home folder
  and never a real repo with secrets, SSH keys, or production code.
- Use a fresh Telegram bot token, keep it out of any directory Claude can reach,
  and rotate it if it is ever exposed.

If that trade-off is not acceptable to you, do not run this.

---

## Driving safety — also read this

codriver is a **hands-free coding aid, not an autopilot.** It is designed so you
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

Nothing is sent to the author of codriver. If you want zero third-party speech
services, choose the `say` backend — then only Telegram is in the loop.

---

## Using your Claude subscription

codriver drives the `claude` CLI you're already signed into, so it runs on your
**Claude Code subscription** rather than the paid API. Automating your own
account this way is for personal use — review the
[Anthropic Usage Policies](https://www.anthropic.com/legal/aup) and your plan's
terms, and don't share one login across people. codriver is an independent
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

This installs the `codriver` command.

---

## Set up: `codriver init`

```bash
codriver init
```

The wizard walks you through everything:

1. **Safety notice** — the warning above, up front.
2. **Telegram bot token** — pasted and verified against Telegram (it shows your
   bot's `@username` on success).
3. **Telegram user id** — the numeric id allowed to talk to the bot.
4. **TTS backend** — pick `elevenlabs` or `say` (see below).
5. **Workspace** — defaults to `~/codriver-workspace`. It is created, seeded with
   a `CLAUDE.md` (an existing one is left untouched), and initialized as a git
   repo on a `codriver-work` branch. The wizard **refuses your home folder, real
   repos, and data dirs** like `~/Documents` or `~/.ssh` — Claude runs destructive
   commands here, so it must be a dedicated, throwaway directory.
6. Writes `~/.config/codriver/config.toml` (permissions `0600`).

Config lives at `~/.config/codriver/config.toml`, outside the repo. You never
have to edit it by hand.

---

## Run: `start` / `stop` / `status`

```bash
codriver doctor           # full preflight: ffmpeg, Telegram, TTS, claude, speech model
codriver start            # start the bot (begins polling Telegram)
codriver start --check    # quick check (platform + token + TTS), then exit
codriver stop             # stop the running bot
codriver status           # show running (with PID) or stopped
```

Run **`codriver doctor`** once before your first drive. Unlike `--check`, it also
runs one real `claude` turn to confirm you're signed in and that your build
accepts the model/effort flags, and it pre-downloads the speech model — so you
don't discover a problem on the road. The bot also **auto-restarts itself** if it
crashes, so a transient hiccup won't leave you stranded mid-drive.

Then put on your seatbelt, open the chat with your bot, and start sending voice
notes.

### Keep it running (optional)

`codriver start` runs in the foreground and stops if you close the terminal. To
run it as a background service that **starts at login and restarts on crash**:

```bash
codriver install-service     # install + start the launchd agent
codriver uninstall-service   # stop + remove it
```

The service runs under `caffeinate -i`, which prevents *idle* system sleep **on
battery** (so the bot keeps polling while you drive). Caveats: closing the laptop
lid still pauses it, and once installed, `codriver stop` won't keep it down —
launchd relaunches it, so use **`codriver uninstall-service`** to stop it for
good. Logs land in `~/.config/codriver/bot.log`. Save your token via `codriver
init` (not just an env var) before installing — the service runs with a clean
environment.

---

## Text-to-speech backends

codriver can speak its replies two ways:

- **`say`** — macOS's built-in speech (voice `Samantha`). Zero setup, no account,
  works offline. Robotic but fine.
- **`elevenlabs`** — natural, human-sounding voices. Needs an API key. During
  `codriver init` the bot lists your available ElevenLabs voices so you can pick
  one.

**Get a free ElevenLabs API key: https://try.elevenlabs.io/ihajsceo1jo8**

> That's a referral link — signing up through it supports codriver at no extra
> cost to you. The tool works identically with any ElevenLabs key, or with the
> free local `say` voice, so use whatever you prefer.

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
| `codriver/config.py` | config loading (env > `config.toml` > defaults), whitelist, paths |
| `codriver/stt.py`    | local Whisper transcription |
| `codriver/brain.py`  | runs `claude -p --resume` with the chosen model + effort, keeps session continuity |
| `codriver/tts.py`    | text → spoken OGG/Opus (ElevenLabs or `say`) |
| `codriver/bot.py`    | Telegram handlers (voice + slash commands) and wiring |
| `codriver/commands.py` | parses in-bot commands (effort / model / config / reset) |
| `codriver/runtime.py`  | live-switchable effort + model, persisted to `runtime.json` |
| `codriver/cli.py`    | `init` / `start` / `stop` / `status` |

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

Built by **skywalqr**.

- ⭐ **Star the repo** if codriver saved you a commute — that's what helps others find it.
- 💬 Questions, ideas, or bugs: open an issue<!-- or add a contact: your email / @handle -->.
- ❤️ **Support development:** [Sponsor](https://github.com/sponsors/skywalqr) <!-- requires enabling GitHub Sponsors on this account -->

<!-- Repo URLs and the sponsor link assume the GitHub handle `skywalqr`.
     If yours differs, find-replace it here, in .github/FUNDING.yml, and in
     pyproject.toml [project.urls]. GitHub Sponsors must be enabled for the
     Sponsor link/button to resolve. -->

---

## License

[MIT](LICENSE) — Copyright (c) 2026 skywalqr.
