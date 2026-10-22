#!/usr/bin/env bash
# Goal Engine connector installer.
#   curl -fsSL https://solve.chetanbuilds.com/connector/install.sh | bash -s -- gec_TOKEN
# Installs into ~/goal-connector, finds your database settings, starts in the background,
# and starts again after a reboot. No root needed (sudo is only asked for to create a read-only DB user).
set -euo pipefail

TOKEN="${1:-}"
SERVER="${GOAL_SERVER:-__SERVER__}"
DIR="${GOAL_DIR:-$HOME/goal-connector}"

if [[ ! "$TOKEN" =~ ^gec_[A-Za-z0-9_-]+$ ]]; then
  echo "Copy the full command from Goal Engine (it ends with your gec_ token)."; exit 1
fi
command -v python3 >/dev/null || { echo "Python 3 is needed:  sudo apt install python3 python3-venv"; exit 1; }

echo "== Installing into $DIR"
mkdir -p "$DIR" && cd "$DIR"
curl -fsSL "$SERVER/connector/goal_connector.py" -o goal_connector.py
if [ ! -x .venv/bin/python ]; then
  if ! python3 -m venv .venv >/dev/null 2>&1; then
    rm -rf .venv
    echo "Python's venv module is missing. Run:  sudo apt install python3-venv   then this command again."; exit 1
  fi
fi
.venv/bin/pip install -q --disable-pip-version-check pymysql cryptography

# Stop an older copy (this folder runs one connector)
.venv/bin/python goal_connector.py stop >/dev/null 2>&1 || true

echo
# questions are read from the keyboard, not from this script
.venv/bin/python goal_connector.py setup --server "$SERVER" --token "$TOKEN" </dev/tty

echo "== Starting in the background"
nohup .venv/bin/python goal_connector.py run >> connector.log 2>&1 &
sleep 3
if ! .venv/bin/python -c "import os,sys; pid=int(open('goal_connector.pid').read()); os.kill(pid,0)" 2>/dev/null; then
  echo "It didn't start. Last lines of $DIR/connector.log:"; tail -5 connector.log; exit 1
fi

# Start again after a reboot (user crontab, no root)
LINE="@reboot cd $DIR && .venv/bin/python goal_connector.py run >> connector.log 2>&1"
if command -v crontab >/dev/null && { crontab -l 2>/dev/null | grep -v "goal_connector.py run" ; echo "$LINE" ; } | crontab - 2>/dev/null; then
  echo "It will start again by itself after a server reboot."
else
  echo "(After a server reboot, start it with:  cd $DIR && nohup .venv/bin/python goal_connector.py run >> connector.log 2>&1 &)"
fi

echo
echo "CONNECTED. Go back to Goal Engine, your tables appear in a few seconds."
echo "Stop it any time:  $DIR/.venv/bin/python $DIR/goal_connector.py stop"
