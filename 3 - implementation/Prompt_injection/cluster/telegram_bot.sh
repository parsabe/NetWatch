#!/usr/bin/env bash
# Telegram commands for the cluster runs (/status, /qstat, /progress, /log, /compare) — on the LOGIN node.
#
#   ./cluster/telegram_bot.sh install     # cron job: polls Telegram every minute (answers within ~1 min) + command menu
#   ./cluster/telegram_bot.sh status      # is it installed? last answers / errors
#   ./cluster/telegram_bot.sh test        # print the /status answer here (sends nothing)
#   ./cluster/telegram_bot.sh uninstall
#
# Needs ~/.config/horizon/telegram.env (TELEGRAM_BOT_TOKEN=…) and the chat id (send the bot one message, then
# `.venv/bin/python -m horizon.notify --check`). The bot answers only that chat and only the fixed commands above;
# it is read-only (never starts, stops or deletes a job). Cron runs on THIS login node only: install it again if
# you move to another login node (`hostname` tells you which one).
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
CONF="$HOME/.config/horizon"
TAG="# horizon-telegram-bot"
LINE="* * * * * cd '$DIR' && PATH=/opt/pbs/bin:/usr/bin:/bin flock -n '$CONF/bot.lock' .venv/bin/python scripts/telegram_bot.py --once >> '$CONF/bot.log' 2>&1 $TAG"

case "${1:-status}" in
  install)
    mkdir -p "$CONF"
    .venv/bin/python -m horizon.notify --check | grep -q "chat id: [0-9-]" \
      || { echo "!! Telegram not configured — see the top of this file"; exit 1; }
    ( crontab -l 2>/dev/null | grep -v "$TAG" || true; echo "$LINE" ) | crontab -
    .venv/bin/python scripts/telegram_bot.py --set-commands
    echo "installed on $(hostname): the bot answers within about a minute. Try /status in Telegram."
    ;;
  uninstall)
    ( crontab -l 2>/dev/null | grep -v "$TAG" || true ) | crontab -
    echo "removed"
    ;;
  status)
    if crontab -l 2>/dev/null | grep -q "$TAG"; then echo "installed on $(hostname)"; else echo "not installed"; fi
    tail -n 15 "$CONF/bot.log" 2>/dev/null || true
    ;;
  test)
    .venv/bin/python scripts/telegram_bot.py --test /status
    ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
