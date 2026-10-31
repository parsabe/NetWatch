"""Telegram milestone notifications (campaign start, important stages, failures, finish).

Credentials never live in the project: TELEGRAM_BOT_TOKEN (and optionally TELEGRAM_CHAT_ID) come from the
environment or from ~/.config/horizon/telegram.env (KEY=value lines, chmod 600). Without a chat id the bot's
most recent private chat is looked up once (send the bot any message first) and cached next to the token.
Sending never raises: a failed notification must never stop an experiment.

    uv run python -m horizon.notify "text"        # send one message (used by the cluster job script)
    uv run python -m horizon.notify --check       # show whether token / chat id are found
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CONF_DIR = Path.home() / ".config" / "horizon"
ENV_FILE = CONF_DIR / "telegram.env"
CHAT_FILE = CONF_DIR / "telegram_chat_id"
API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT_S = 15
MAX_CHARS = 4000                     # Telegram's limit is 4096 characters per message
HEADER_ROOM = 120                    # room for the '🔭 Horizon · host · part i/n' header line


def _env_file() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or _env_file().get("TELEGRAM_BOT_TOKEN")


def _call(token: str, method: str, params: dict | None = None) -> dict:
    data = urllib.parse.urlencode(params).encode() if params else None
    with urllib.request.urlopen(API.format(token=token, method=method), data=data, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def _chat_id(token: str) -> str | None:
    cid = os.environ.get("TELEGRAM_CHAT_ID") or _env_file().get("TELEGRAM_CHAT_ID")
    if cid:
        return cid
    if CHAT_FILE.exists():
        return CHAT_FILE.read_text().strip() or None
    try:
        res = _call(token, "getUpdates").get("result", [])
    except Exception:
        return None
    chats = [(u.get("message") or u.get("my_chat_member") or {}).get("chat", {}) for u in res]
    chats = [c for c in chats if c.get("type") == "private"]
    if not chats:
        return None
    cid = str(chats[-1]["id"])
    try:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        CHAT_FILE.write_text(cid)
        CHAT_FILE.chmod(0o600)
    except OSError:
        pass
    return cid


def run_tag() -> str:
    """Which campaign a message comes from (two runs with different models notify the same chat): HORIZON_RUN if
    set, else the victim model of this copy's config, e.g. 'qwen3:8b' or 'gemma3:12b'."""
    if os.environ.get("HORIZON_RUN"):
        return os.environ["HORIZON_RUN"]
    try:
        from horizon.config import load_config
        return load_config().models["q_llm"].split("/", 1)[-1]
    except Exception:
        return ""


def send(text: str) -> bool:
    """Send one message; returns False (never raises) if Telegram is not configured or unreachable."""
    if os.environ.get("HORIZON_NO_NOTIFY"):           # tests and dry runs
        return False
    token = _token()
    if not token:
        return False
    cid = _chat_id(token)
    if not cid:
        return False
    tag = run_tag()
    head = f"🔭 Horizon{f' [{tag}]' if tag else ''} · {socket.gethostname().split('.')[0]}"
    ok = True
    for i, part in enumerate(chunks(text), 1):
        n = len(chunks(text))
        body = f"{head}{f' · part {i}/{n}' if n > 1 else ''}\n{part}"
        try:
            ok &= bool(_call(token, "sendMessage", {"chat_id": cid, "text": body,
                                                    "disable_web_page_preview": "true"}).get("ok"))
        except Exception:
            ok = False
    return ok


def chunks(text: str, limit: int = MAX_CHARS - HEADER_ROOM) -> list[str]:
    """Split at blank lines (section borders), then at line ends, so no part exceeds Telegram's limit."""
    out, cur = [], ""
    for block in text.split("\n\n"):
        pieces = [block] if len(block) <= limit else block.split("\n")
        for piece in pieces:
            while len(piece) > limit:                    # a single huge line: hard split
                out.append(piece[:limit])
                piece = piece[limit:]
            sep = "\n\n" if piece is block else "\n"
            if cur and len(cur) + len(sep) + len(piece) > limit:
                out.append(cur)
                cur = piece
            else:
                cur = f"{cur}{sep}{piece}" if cur else piece
    if cur:
        out.append(cur)
    return out or [""]


def main() -> int:
    if sys.argv[1:] == ["--check"]:
        token = _token()
        print("token:", "found" if token else "missing", f"({ENV_FILE})")
        print("chat id:", (_chat_id(token) if token else None) or "not found — send the bot any message first")
        return 0
    return 0 if send(" ".join(sys.argv[1:]) or "test message") else 1


if __name__ == "__main__":
    sys.exit(main())
