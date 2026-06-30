"""Pure parsing of in-bot control commands (voice transcript or typed text).

A message that matches a command (e.g. "set effort to high", "use opus",
"/model sonnet") changes a setting instead of being sent to Claude.
`parse_command` is pure, so the grammar is easy to unit-test. Patterns are kept
tight (mostly whole-message matches) so normal coding requests are NOT
misread as commands.
"""

import re

EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}

# Friendly spoken/typed names -> CLI model ids.
MODEL_ALIASES = {
    "opus": "claude-opus-4-8",
    "opus 4.8": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}
MODEL_FRIENDLY = {
    "claude-opus-4-8": "Opus 4.8",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-haiku-4-5": "Haiku 4.5",
}


def friendly_model(model_id: str) -> str:
    return MODEL_FRIENDLY.get(model_id, model_id)


def _norm(text: str) -> str:
    # keep letters, digits, dot, hyphen, slash, spaces; drop other punctuation
    return re.sub(r"[^a-z0-9.\-/\s]", "", text.lower()).strip()


def parse_command(text: str):
    """Return a command dict, or None if the text is a normal prompt.

    {"action": "set_effort", "value": <level>}
    {"action": "set_model", "value": <model_id>, "label": <friendly>}
    {"action": "show_config"}
    {"action": "reset"}
    """
    t = _norm(text)
    if not t:
        return None

    if t in {"config", "/config", "status", "/status", "settings", "/settings",
             "my config", "my settings", "current config", "current settings"} or \
            re.fullmatch(r"(what'?s|what\s+is|show)\s+(my\s+)?(config|settings|setup|model|effort)", t):
        return {"action": "show_config"}

    if t in {"reset", "/reset", "new", "/new", "start over", "new chat", "fresh start"} or \
            re.fullmatch(r"(new|fresh|reset)\s+(session|chat|conversation)", t):
        return {"action": "reset"}

    # effort — whole-message ONLY, so a prompt that merely mentions "effort" is
    # not swallowed (e.g. "reduce the effort to medium so we can ship faster").
    m = (re.fullmatch(r"/?effort\s+(low|medium|high|xhigh|max)", t)
         or re.fullmatch(r"set(?:\s+the)?\s+effort(?:\s+to)?\s+(low|medium|high|xhigh|max)", t)
         or re.fullmatch(r"(low|medium|high|xhigh|max)\s+effort", t))
    if m:
        return {"action": "set_effort", "value": m.group(1)}

    # model — capture a candidate, then accept ONLY a known model. An unknown id
    # (e.g. "switch to claude-foo") falls through to a normal prompt rather than
    # being stored and bricking every subsequent turn.
    m = re.match(r"/?model\s+(.+)$", t)
    if not m:
        m = re.fullmatch(
            r"(?:use|switch(?:\s+to)?|set(?:\s+the)?\s+model(?:\s+to)?)\s+(?:the\s+)?(.+?)(?:\s+model)?",
            t,
        )
    if m:
        raw = m.group(1).strip()
        model_id = MODEL_ALIASES.get(raw) or (raw if raw in MODEL_FRIENDLY else None)
        if model_id:
            return {"action": "set_model", "value": model_id, "label": friendly_model(model_id)}

    return None
