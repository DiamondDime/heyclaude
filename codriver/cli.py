"""Command-line interface for Co-Driver.

Subcommands:
  codriver init    interactive onboarding wizard
  codriver start   start the bot (or --check to validate config only)
  codriver stop    stop a running bot
  codriver status  report running / stopped

Uses only the standard library plus httpx (HTTP) and tomli_w (TOML writing).
The config file and PID file live under ~/.config/codriver (overridable with
CODRIVER_CONFIG_DIR), OUTSIDE the project repo. Secrets are never printed.
"""

import argparse
import getpass
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import httpx

# tomllib is stdlib on 3.11+; we read the existing config with it.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on <3.11
    tomllib = None

import tomli_w


# --- Paths -----------------------------------------------------------------
# Computed locally (not imported from config) so the CLI stays usable even if
# the config module fails to import. Mirrors config.py's resolution exactly:
# env CODRIVER_CONFIG_DIR > ~/.config/codriver.
def _config_dir() -> Path:
    override = os.environ.get("CODRIVER_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "codriver"


def _config_file() -> Path:
    return _config_dir() / "config.toml"


def _pid_file() -> Path:
    return _config_dir() / "codriver.pid"


def _sandbox_claude_md() -> Path:
    # codriver/codriver/cli.py -> repo root is two parents up.
    return Path(__file__).resolve().parent.parent / "sandbox" / "CLAUDE.md"


DEFAULT_WORKDIR = Path.home() / "codriver-workspace"
ELEVENLABS_MODEL = "eleven_turbo_v2_5"
SAY_DEFAULT_VOICE = "Samantha"
HTTP_TIMEOUT = 20.0


# --- HTTP validation helpers ----------------------------------------------
def _validate_telegram_token(token: str):
    """Return (ok, message). On success message is the bot @username."""
    try:
        resp = httpx.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=HTTP_TIMEOUT
        )
    except httpx.HTTPError as exc:
        return False, f"network error: {exc}"
    try:
        data = resp.json()
    except ValueError:
        return False, f"unexpected response (HTTP {resp.status_code})"
    if resp.status_code == 200 and data.get("ok"):
        username = (data.get("result") or {}).get("username") or "unknown"
        return True, username
    desc = data.get("description") or f"HTTP {resp.status_code}"
    return False, desc


def _fetch_elevenlabs_voices(api_key: str):
    """Return (ok, voices_or_message). voices is a list of (name, voice_id)."""
    try:
        resp = httpx.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key},
            timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return False, f"network error: {exc}"
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        return False, f"HTTP {resp.status_code}" + (f": {detail}" if detail else "")
    try:
        data = resp.json()
    except ValueError:
        return False, "unexpected response from ElevenLabs"
    voices = [
        (v.get("name") or "unnamed", v.get("voice_id") or "")
        for v in data.get("voices", [])
        if v.get("voice_id")
    ]
    return True, voices


# --- init ------------------------------------------------------------------
def _prompt_telegram_token() -> str:
    while True:
        token = getpass.getpass("Telegram bot token (hidden): ").strip()
        if not token:
            print("  Token cannot be empty. Create one with @BotFather.")
            continue
        ok, msg = _validate_telegram_token(token)
        if ok:
            print(f"  Connected to bot @{msg}")
            return token
        print(f"  That token didn't work ({msg}). Try again.")


def _prompt_user_id() -> int:
    while True:
        raw = input("Your numeric Telegram user id (get it from @userinfobot): ").strip()
        try:
            return int(raw)
        except ValueError:
            print("  That isn't a number. Enter the numeric id only.")


def _prompt_tts() -> dict:
    """Return the [tts] config section."""
    while True:
        print("\nText-to-speech backend:")
        print("  1) elevenlabs  (cloud, natural voice)")
        print("  2) say         (local macOS, free, robotic)")
        choice = input("Choose 1 or 2: ").strip()

        if choice == "2":
            return {"backend": "say", "say": {"voice": SAY_DEFAULT_VOICE}}

        if choice != "1":
            print("  Please enter 1 or 2.")
            continue

        # elevenlabs
        api_key = getpass.getpass("ElevenLabs API key (hidden; blank if none): ").strip()
        if not api_key:
            print("  Get a free ElevenLabs API key: https://try.elevenlabs.io/ihajsceo1jo8")
            print("  Then run this step again.")
            continue
        ok, result = _fetch_elevenlabs_voices(api_key)
        if not ok:
            print(f"  That key didn't work ({result}). Try again.")
            continue
        voices = result
        if not voices:
            print("  No voices found on that account. Try a different key.")
            continue
        print("\nAvailable voices:")
        for i, (name, vid) in enumerate(voices, 1):
            print(f"  {i}) {name}  [{vid}]")
        while True:
            pick = input(f"Pick a voice (1-{len(voices)}): ").strip()
            try:
                idx = int(pick)
            except ValueError:
                print("  Enter a number.")
                continue
            if 1 <= idx <= len(voices):
                voice_id = voices[idx - 1][1]
                break
            print(f"  Out of range. Choose 1-{len(voices)}.")
        return {
            "backend": "elevenlabs",
            "elevenlabs": {
                "api_key": api_key,
                "voice_id": voice_id,
                "model": ELEVENLABS_MODEL,
            },
        }


def _setup_workdir() -> Path:
    raw = input(f"\nWorkspace directory [{DEFAULT_WORKDIR}]: ").strip()
    workdir = Path(raw).expanduser() if raw else DEFAULT_WORKDIR
    workdir.mkdir(parents=True, exist_ok=True)

    src = _sandbox_claude_md()
    dest = workdir / "CLAUDE.md"
    if src.exists():
        try:
            shutil.copyfile(src, dest)
            print(f"  Copied CLAUDE.md into {workdir}")
        except OSError as exc:
            print(f"  Could not copy CLAUDE.md ({exc}).")
    else:
        print(f"  Note: template CLAUDE.md not found at {src}; skipping.")

    if shutil.which("git"):
        try:
            if not (workdir / ".git").exists():
                subprocess.run(
                    ["git", "init"], cwd=workdir, check=True,
                    capture_output=True, text=True,
                )
            subprocess.run(
                ["git", "checkout", "-b", "codriver-work"], cwd=workdir,
                check=True, capture_output=True, text=True,
            )
            print("  Initialized git repo on branch 'codriver-work'.")
        except subprocess.CalledProcessError:
            # Branch may already exist, or git declined — not fatal.
            pass
    else:
        print("  git not found; skipping repo init.")
    return workdir


def cmd_init(args) -> int:
    print("=" * 64)
    print("Co-Driver setup")
    print("=" * 64)
    print(
        "\nSAFETY NOTICE\n"
        "  The Telegram user-id whitelist is the ONLY thing guarding this bot.\n"
        "  Claude runs with --dangerously-skip-permissions inside the workspace,\n"
        "  so it can run any command there without asking.\n"
        "  Point it ONLY at a dedicated, throwaway workspace directory —\n"
        "  NEVER your home folder or a real code repository.\n"
    )

    token = _prompt_telegram_token()
    user_id = _prompt_user_id()
    tts = _prompt_tts()
    workdir = _setup_workdir()

    config = {
        "telegram": {"bot_token": token, "allowed_user_id": user_id},
        "tts": tts,
        "claude": {"workdir": str(workdir), "timeout": 600},
    }

    cfg_dir = _config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = _config_file()
    with open(cfg_file, "wb") as fh:
        tomli_w.dump(config, fh)
    os.chmod(cfg_file, 0o600)

    print(f"\nWrote config to {cfg_file} (permissions 0600).")
    print("Done. Run: codriver start")
    return 0


# --- start -----------------------------------------------------------------
def _read_config_toml() -> dict:
    cfg_file = _config_file()
    if not cfg_file.exists() or tomllib is None:
        return {}
    try:
        with open(cfg_file, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return {}


def _check_config() -> int:
    """Validate the configured token (and ElevenLabs key) without polling."""
    # Resolve through the config module so ENV > TOML precedence is respected.
    try:
        from . import config as cfg
        token = cfg.TOKEN
        backend = cfg.TTS_BACKEND
        el_key = cfg.ELEVENLABS_API_KEY
    except Exception:
        data = _read_config_toml()
        token = (data.get("telegram") or {}).get("bot_token", "")
        backend = (data.get("tts") or {}).get("backend", "say")
        el_key = ((data.get("tts") or {}).get("elevenlabs") or {}).get("api_key", "")

    if not token:
        print("No bot token configured. Run: codriver init", file=sys.stderr)
        return 1
    ok, msg = _validate_telegram_token(token)
    if not ok:
        print(f"Telegram token invalid: {msg}", file=sys.stderr)
        return 1
    print(f"Telegram OK (bot @{msg})")

    if backend == "elevenlabs":
        if not el_key:
            print("ElevenLabs backend selected but no API key configured.",
                  file=sys.stderr)
            return 1
        ok, result = _fetch_elevenlabs_voices(el_key)
        if not ok:
            print(f"ElevenLabs key invalid: {result}", file=sys.stderr)
            return 1
        print(f"ElevenLabs OK ({len(result)} voices available)")

    print("OK")
    return 0


def cmd_start(args) -> int:
    # Gate on token-resolvability (ENV > TOML > keychain), not file existence,
    # so env-only setups (no config.toml) still work for backward compat.
    from . import config as cfg
    if not cfg.TOKEN:
        print("No bot token configured. Run: codriver init", file=sys.stderr)
        return 1

    if args.check:
        return _check_config()

    # Refuse to start a second instance: two pollers on one token => Telegram 409,
    # and overwriting the PID file would orphan the first (stop could not find it).
    existing = _read_pid()
    if existing and _process_alive(existing):
        print(f"Already running (PID {existing}). Run 'codriver stop' first.",
              file=sys.stderr)
        return 1

    # Record our PID so `codriver stop` / `status` can find us.
    cfg_dir = _config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    pid_file = _pid_file()
    pid_file.write_text(str(os.getpid()))
    try:
        from . import bot
        bot.main()
    finally:
        try:
            if pid_file.exists():
                pid_file.unlink()
        except OSError:
            pass
    return 0


# --- stop / status ---------------------------------------------------------
def _read_pid():
    pid_file = _pid_file()
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_codriver_process(pid: int) -> bool:
    """Best-effort guard against PID reuse: confirm the process still looks like
    codriver before signalling it. Fails open (returns True) if we can't tell."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if out.returncode != 0:
        return True
    return "codriver" in out.stdout


def cmd_stop(args) -> int:
    pid_file = _pid_file()
    pid = _read_pid()
    if pid is None:
        print("Co-Driver is not running (no PID file).")
        return 0
    if not _process_alive(pid):
        print(f"No process {pid} running; clearing stale PID file.")
        pid_file.unlink(missing_ok=True)
        return 0
    if not _is_codriver_process(pid):
        print(f"PID {pid} is not a codriver process; clearing stale PID file.")
        pid_file.unlink(missing_ok=True)
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Stopped Co-Driver (PID {pid}).")
    except OSError as exc:
        print(f"Could not stop PID {pid}: {exc}", file=sys.stderr)
        return 1
    finally:
        pid_file.unlink(missing_ok=True)
    return 0


def cmd_status(args) -> int:
    pid = _read_pid()
    if pid is not None and _process_alive(pid):
        print(f"running (PID {pid})")
    else:
        print("stopped")
    return 0


# --- argparse wiring -------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codriver",
        description="Drive Claude Code from Telegram voice notes.",
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="interactive onboarding wizard")
    p_init.set_defaults(func=cmd_init)

    p_start = sub.add_parser("start", help="start the bot")
    p_start.add_argument(
        "--check", action="store_true",
        help="validate config (token/keys) and exit without polling",
    )
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="stop a running bot")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="show running / stopped")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
