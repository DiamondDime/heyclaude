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
import asyncio
import getpass
import os
import platform
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from importlib import resources
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


def _workspace_template() -> str:
    """The CLAUDE.md seeded into a new workspace, read from packaged data so it
    ships with `pip install` (the old repo-relative path didn't exist in a wheel,
    so the seed silently no-op'd for every installed user)."""
    return resources.files("codriver").joinpath("data/workspace_CLAUDE.md").read_text()


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


def validate_workdir(path: Path, home: Path) -> str | None:
    """Return a refusal reason if `path` is an unsafe workspace, else None.

    Claude runs `--dangerously-skip-permissions` in this directory, so a
    fat-fingered `~` must NOT hand it the home folder or a tree full of real
    data. Pure (no I/O beyond resolve) so it's unit-testable. The wizard's
    printed warning is only as good as this enforcement.
    """
    try:
        p = path.expanduser().resolve()
        home = home.expanduser().resolve()
    except OSError:
        return None

    if p == Path(p.anchor):
        return "that's the filesystem root"
    if p == home:
        return "that's your home folder"
    # p is an ancestor of home (home lives inside it) — e.g. /Users
    if home.is_relative_to(p):
        return "your home folder is inside it"

    # APFS is case-insensitive by default, so compare case-folded: ~/documents
    # and ~/Documents are the SAME directory and must both be refused.
    sensitive_names = {
        n.casefold() for n in (
            "Desktop", "Documents", "Downloads", "Library", "Movies", "Music",
            "Pictures", "Applications", "Projects", "code", "src",
        )
    }
    # Case-fold the parent comparison too (realpath doesn't canonicalize case on
    # macOS), so a mis-cased home prefix can't slip a sensitive dir past.
    if str(p.parent).casefold() == str(home).casefold() and p.name.casefold() in sensitive_names:
        return f"{p.name} holds real files — use a dedicated subfolder instead"

    pcf = str(p).casefold()
    for name in (".ssh", ".config", ".aws", ".gnupg", ".kube"):
        scf = str(home / name).casefold()
        if pcf == scf or pcf.startswith(scf + "/"):
            return f"it's inside {name}"
    return None


def _require_macos() -> None:
    if platform.system() != "Darwin":
        print(
            "codriver is macOS-only — it uses the `say` voice and Apple audio.",
            file=sys.stderr,
        )
        sys.exit(2)


def _seed_claude_md(workdir: Path) -> None:
    """Write the workspace CLAUDE.md, never clobbering one the user already has."""
    dest = workdir / "CLAUDE.md"
    try:
        text = _workspace_template()
    except (OSError, ModuleNotFoundError, FileNotFoundError):
        print("  Note: bundled CLAUDE.md template missing; skipping.")
        return
    if dest.exists():
        if dest.read_text() == text:
            return
        print(f"  {dest} already exists — leaving your version untouched.")
        return
    dest.write_text(text)
    print(f"  Seeded CLAUDE.md into {workdir}")


def _git_init_workspace(workdir: Path) -> None:
    if not shutil.which("git"):
        print("  git not found; skipping repo init.")
        return
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


def _setup_workdir() -> Path:
    home = Path.home()
    while True:
        raw = input(f"\nWorkspace directory [{DEFAULT_WORKDIR}]: ").strip()
        workdir = Path(raw).expanduser() if raw else DEFAULT_WORKDIR

        reason = validate_workdir(workdir, home)
        if reason:
            print(
                f"  Refusing {workdir}: {reason}.\n"
                f"  Claude runs destructive commands here — pick a dedicated, "
                f"throwaway directory (e.g. {DEFAULT_WORKDIR})."
            )
            continue

        # An existing non-empty dir (especially a real repo) is dangerous: confirm.
        if workdir.exists():
            ignorable = {".git", "CLAUDE.md", ".codriver_session"}
            try:
                leftovers = [e for e in workdir.iterdir() if e.name not in ignorable]
            except OSError:
                leftovers = []
            if (workdir / ".git").exists() or leftovers:
                print(
                    f"  ⚠️  {workdir} already contains files. Claude will run "
                    f"destructive commands in here."
                )
                confirm = input(
                    f"  Type the folder name '{workdir.name}' to use it anyway, "
                    f"or press Enter to choose another: "
                ).strip()
                if confirm != workdir.name:
                    continue
        break

    workdir.mkdir(parents=True, exist_ok=True)
    _seed_claude_md(workdir)
    _git_init_workspace(workdir)
    return workdir


def cmd_init(args) -> int:
    _require_macos()
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
        "\nDRIVING & PRIVACY\n"
        "  This is a hands-free coding aid, NOT an autopilot. Keep your eyes on\n"
        "  the road and obey local laws on phone use while driving.\n"
        "  Your voice notes travel through Telegram's servers; if you pick the\n"
        "  ElevenLabs voice, reply TEXT is also sent to ElevenLabs. Transcription\n"
        "  and Claude run locally on your Mac.\n"
    )
    if input("Type 'I understand' to continue: ").strip().lower() != "i understand":
        print("Aborted.")
        return 1

    token = _prompt_telegram_token()
    user_id = _prompt_user_id()
    tts = _prompt_tts()
    workdir = _setup_workdir()

    config = {
        "telegram": {"bot_token": token, "allowed_user_id": user_id},
        "tts": tts,
        "claude": {
            "workdir": str(workdir),
            "timeout": 600,
            "effort": "xhigh",
            "model": "claude-opus-4-8",
        },
    }

    cfg_dir = _config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = _config_file()
    with open(cfg_file, "wb") as fh:
        tomli_w.dump(config, fh)
    os.chmod(cfg_file, 0o600)

    print(f"\nWrote config to {cfg_file} (permissions 0600).")
    print("Claude defaults to model claude-opus-4-8 at xhigh effort —")
    print("switch anytime from the bot with /model and /effort.")

    # Pre-download the speech model now (on WiFi) so the first voice note on the
    # road doesn't stall on a ~140MB fetch over cellular.
    _warm_whisper()

    print("\nDone. Run: codriver start --check   (then: codriver start)")
    return 0


def _warm_whisper() -> None:
    print("\nDownloading the local speech model (first run only, ~140MB)…")
    try:
        from . import stt
        stt._model()
        print("  Speech model ready.")
    except Exception as exc:  # never block setup on this
        print(f"  Could not pre-load the speech model ({exc}); it will download "
              f"on first use.")


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


def _preflight(warm_whisper: bool, check_claude: bool = True) -> int:
    """Validate everything needed to drive: platform, ffmpeg, Telegram token,
    TTS credentials, and (optionally) that `claude` is installed + signed in.
    Returns 0 if all required checks pass, non-zero otherwise."""
    ok = True

    if platform.system() != "Darwin":
        print("FAIL  macOS required (uses the `say` voice + Apple audio)", file=sys.stderr)
        return 2
    print("OK    macOS")

    if shutil.which("ffmpeg") or Path("/opt/homebrew/bin/ffmpeg").exists():
        print("OK    ffmpeg")
    else:
        print("FAIL  ffmpeg not found — run: brew install ffmpeg", file=sys.stderr)
        ok = False

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
        print("FAIL  no bot token configured — run: codriver init", file=sys.stderr)
        return 1
    tg_ok, msg = _validate_telegram_token(token)
    if tg_ok:
        print(f"OK    Telegram (bot @{msg})")
    else:
        print(f"FAIL  Telegram token invalid: {msg}", file=sys.stderr)
        ok = False

    if backend == "elevenlabs":
        if not el_key:
            print("FAIL  ElevenLabs selected but no API key configured", file=sys.stderr)
            ok = False
        else:
            el_ok, result = _fetch_elevenlabs_voices(el_key)
            if el_ok:
                print(f"OK    ElevenLabs ({len(result)} voices) — falls back to `say` if it fails")
            else:
                # Non-fatal: tts.py degrades to `say`, so a bad EL key still speaks.
                print(f"WARN  ElevenLabs key invalid ({result}); will use local `say`")

    if check_claude:
        print("...   checking claude (one quick turn)…")
        try:
            from . import brain
            c_ok, detail = brain.preflight()
        except Exception as exc:
            c_ok, detail = False, str(exc)
        print(("OK    " if c_ok else "FAIL  ") + f"claude — {detail}")
        ok = ok and c_ok

    if warm_whisper:
        _warm_whisper()

    print("\n" + ("OK — ready to drive." if ok else "Some checks failed (see FAIL above)."))
    return 0 if ok else 1


def cmd_doctor(args) -> int:
    """Full preflight: platform, ffmpeg, Telegram, TTS, claude, speech model."""
    return _preflight(warm_whisper=True, check_claude=True)


def cmd_start(args) -> int:
    _require_macos()
    # Gate on token-resolvability (ENV > TOML > keychain), not file existence,
    # so env-only setups (no config.toml) still work for backward compat.
    from . import config as cfg
    if not cfg.TOKEN:
        print("No bot token configured. Run: codriver init", file=sys.stderr)
        return 1

    if args.check:
        # Skip the live claude turn here for speed; `codriver doctor` does that.
        return _preflight(warm_whisper=False, check_claude=False)

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
        _supervise_bot()
    finally:
        try:
            if pid_file.exists():
                pid_file.unlink()
        except OSError:
            pass
    return 0


def _supervise_bot() -> None:
    """Run the bot, auto-restarting after an unexpected crash so a transient
    failure mid-drive doesn't leave the driver with a dead bot for the rest of
    the trip. A clean shutdown (SIGTERM via `codriver stop`, Ctrl-C) or a fatal
    config error (bad token) stops for good instead of looping."""
    from . import bot
    try:
        from telegram.error import InvalidToken
    except Exception:  # pragma: no cover
        InvalidToken = ()  # type: ignore[assignment]

    restarts: list[float] = []
    backoff = 3
    while True:
        # PTB's run_polling closes the event loop on exit (close_loop=True). A
        # second bot.main() would then call run_until_complete on that CLOSED
        # loop -> "Event loop is closed" -> instant re-crash, defeating the whole
        # restart feature. Hand PTB a FRESH loop each iteration so a restart
        # actually works. (Verified: without this, the bot dies permanently
        # ~15s after the first mid-drive crash.)
        asyncio.set_event_loop(asyncio.new_event_loop())
        try:
            bot.main()          # returns cleanly when PTB handles SIGINT/SIGTERM
            return
        except KeyboardInterrupt:
            return
        except InvalidToken:
            print("Bot token is invalid — not restarting. Run: codriver init",
                  file=sys.stderr)
            return
        except Exception as exc:
            now = time.monotonic()
            restarts = [t for t in restarts if now - t < 60]
            restarts.append(now)
            if len(restarts) > 5:
                print("Bot crashed repeatedly; giving up. Try: codriver doctor",
                      file=sys.stderr)
                return
            print(f"Bot crashed ({exc}); restarting in {backoff}s…", file=sys.stderr)
            time.sleep(backoff)


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


# --- launchd service (optional, macOS) -------------------------------------
SERVICE_LABEL = "com.codriver.bot"


def _service_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"


def _build_plist() -> bytes:
    # Run via `python -m codriver.cli start` so it works regardless of where the
    # `codriver` script landed on PATH. Built with plistlib so paths are escaped
    # correctly.
    logs = _config_dir()
    plist = {
        "Label": SERVICE_LABEL,
        "ProgramArguments": [
            # `-i` prevents idle system sleep and WORKS ON BATTERY. (`-s` is
            # AC-power only — useless in a car.) KeepAlive restarts a crash but
            # cannot stop sleep, which is the likeliest reason a bot goes quiet
            # mid-drive. Closing the laptop lid still pauses it.
            "/usr/bin/caffeinate", "-i",
            sys.executable, "-m", "codriver.cli", "start",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        # launchd agents start with a minimal PATH that EXCLUDES ~/.local/bin
        # (where `claude` lives) and Homebrew (`ffmpeg`). Without this, the
        # service starts but every voice note fails to find `claude`. Bake in the
        # install-time PATH so the service can actually run.
        # `or` (not a default arg) so a set-but-empty PATH also gets the fallback.
        "EnvironmentVariables": {"PATH": os.environ.get("PATH") or "/usr/bin:/bin"},
        "StandardOutPath": str(logs / "bot.log"),
        "StandardErrorPath": str(logs / "bot.err.log"),
    }
    return plistlib.dumps(plist)


def cmd_install_service(args) -> int:
    _require_macos()
    from . import config as cfg
    if not cfg.TOKEN:
        print("Configure the bot first. Run: codriver init", file=sys.stderr)
        return 1
    # The service runs with a CLEAN environment, so a token that only exists as an
    # exported env var is invisible to it. Require a persistent source.
    data = _read_config_toml()
    has_persistent = bool((data.get("telegram") or {}).get("bot_token")) or bool(cfg._keychain_token())
    if not has_persistent:
        print("Your bot token comes from an environment variable, which the "
              "background service can't see. Run `codriver init` to save it to "
              "config.toml first.", file=sys.stderr)
        return 1
    plist = _service_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    _config_dir().mkdir(parents=True, exist_ok=True)
    plist.write_bytes(_build_plist())
    # `load -w` is the widely-compatible enable; ignore "already loaded".
    subprocess.run(["launchctl", "unload", str(plist)],
                   capture_output=True, text=True)
    res = subprocess.run(["launchctl", "load", "-w", str(plist)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Wrote {plist} but `launchctl load` failed: {res.stderr.strip()}",
              file=sys.stderr)
        print("Load it manually with: launchctl load -w " + str(plist))
        return 1
    print(f"Installed and started the codriver service ({SERVICE_LABEL}).")
    print(f"  It auto-starts at login and restarts on crash. Logs: {_config_dir() / 'bot.log'}")
    print("  Remove it with: codriver uninstall-service")
    return 0


def cmd_uninstall_service(args) -> int:
    plist = _service_plist_path()
    if not plist.exists():
        print("No codriver service installed.")
        return 0
    subprocess.run(["launchctl", "unload", str(plist)],
                   capture_output=True, text=True)
    try:
        plist.unlink()
    except OSError as exc:
        print(f"Could not remove {plist}: {exc}", file=sys.stderr)
        return 1
    print("Uninstalled the codriver service.")
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

    p_doctor = sub.add_parser(
        "doctor", help="full preflight: ffmpeg, Telegram, TTS, claude, speech model"
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_install = sub.add_parser(
        "install-service", help="auto-start the bot at login (launchd, keeps Mac awake)"
    )
    p_install.set_defaults(func=cmd_install_service)

    p_uninstall = sub.add_parser(
        "uninstall-service", help="remove the launchd auto-start service"
    )
    p_uninstall.set_defaults(func=cmd_uninstall_service)

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
