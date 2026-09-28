#!/usr/bin/env bash
# Start the dashboard on macOS / Linux:  ./start.sh
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null; then
  echo "Python 3 is not installed. Get it from https://www.python.org/downloads/"
  exit 1
fi
if [ ! -d .venv ]; then
  echo "First run: setting things up, this takes a minute..."
  python3 -m venv .venv
fi
. .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r requirements.txt

[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -f .env ] || cp .env.example .env

URL="http://127.0.0.1:${PORT:-8000}"
echo "Dashboard running at $URL  (Ctrl+C to stop)"
( sleep 3; (command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") || true ) >/dev/null 2>&1 &
exec python -m app.main
