#!/usr/bin/env bash
# Restart the gym chat server. It runs under flock from cron (@reboot plus a
# */5 watchdog), not systemd, so a restart means: drop the lock holder, then
# relaunch detached under the same lock.
#
# Deliberately avoids `pkill -f chat_server.py` — over ssh that pattern also
# matches the invoking shell's own command line and kills the session.
set -uo pipefail

LOCK=/tmp/gym_chat.lock
GYM="$HOME/services/gym"

mapfile -t PIDS < <(pgrep -f "[c]hat_server\.py")
for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done
mapfile -t LOCKERS < <(pgrep -f "[g]ym_chat\.lock")
for p in "${LOCKERS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done
sleep 3

setsid nohup flock -n "$LOCK" -c \
  "$GYM/.env/bin/python -u $GYM/chat_server.py >> $GYM/chat_server.log 2>&1" \
  >/dev/null 2>&1 </dev/null &

for _ in $(seq 1 15); do
  if curl -sf -o /dev/null "http://127.0.0.1:8090/login"; then
    echo "chat server up"; exit 0
  fi
  sleep 1
done
echo "chat server did NOT come up; log tail:" >&2
tail -5 "$GYM/chat_server.log" >&2
exit 1
