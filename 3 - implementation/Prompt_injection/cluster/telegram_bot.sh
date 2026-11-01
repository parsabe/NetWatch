#!/usr/bin/env bash
# Telegram commands for the cluster runs (/status, /qstat, /progress, /log, /compare) — runs on the LOGIN node
# (compute nodes have no internet) as an ordinary background process of yours: no cron, no root.
#
#   ./cluster/telegram_bot.sh start     # start it (nothing happens if it already runs) + command menu in Telegram
#   ./cluster/telegram_bot.sh status    # running? last lines of its log
#   ./cluster/telegram_bot.sh stop
#   ./cluster/telegram_bot.sh test      # print the /status answer here (sends nothing)
#
# cluster/launch_quick2h.sh starts it for you. It stops by itself 3 h after your last PBS job ended, and it ends
# when the login node reboots: then run `start` again (on the same login node: `hostname` → mlogin01 …).
# Needs ~/.config/horizon/telegram.env (TELEGRAM_BOT_TOKEN=…) and the chat id (send the bot one message, then
# `.venv/bin/python -m horizon.notify --check`). It answers only your chat and only the fixed commands above;
# it is read-only (never starts, stops or deletes a job).
set -euo pipefail
cd "$(dirname "$0")/.."
CONF="$HOME/.config/horizon"
LOG="$CONF/bot.log"
PIDF="$CONF/bot.pid"
running() { [ -s "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }

case "${1:-status}" in
  start)
    mkdir -p "$CONF"
    if running; then echo "bot already running on $(hostname) (pid $(cat "$PIDF"))"; exit 0; fi
    .venv/bin/python -m horizon.notify --check 2>/dev/null | grep -q "chat id: [0-9-]" \
      || { echo "!! Telegram not configured — see the top of this file"; exit 1; }
    .venv/bin/python scripts/telegram_bot.py --set-commands || true
    PATH="/opt/pbs/bin:$PATH" nohup setsid .venv/bin/python scripts/telegram_bot.py >> "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    sleep 3
    if running; then echo "bot running on $(hostname) (pid $(cat "$PIDF")) — send /status in Telegram"
    else echo "!! bot did not start:"; tail -n 5 "$LOG"; exit 1; fi
    ;;
  stop)
    if running; then kill "$(cat "$PIDF")" && echo "stopped"; else echo "not running"; fi
    rm -f "$PIDF"
    ;;
  status)
    if running; then echo "running on $(hostname) (pid $(cat "$PIDF"))"; else echo "not running on $(hostname)"; fi
    tail -n 15 "$LOG" 2>/dev/null || true
    ;;
  test)
    .venv/bin/python scripts/telegram_bot.py --test /status
    ;;
  install) echo "no cron needed any more: use  ./cluster/telegram_bot.sh start"; exit 2 ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
