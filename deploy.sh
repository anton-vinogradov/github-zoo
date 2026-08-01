#!/usr/bin/env bash
# Put this working copy on the dashboard host, and leave behind a note saying
# exactly what was put there.
#
# A deploy that records its own commit is what turns "the file on prod is not
# in any commit" from an afternoon of confusion into a line in the footer.
#
#   GITHUB_ZOO_HOST=user@dashboard ./deploy.sh
#   ./deploy.sh user@dashboard
#
# The host is never written down here: this repository is public, and the CI
# refuses any commit that names a real address.
set -euo pipefail

HOST="${1:-${GITHUB_ZOO_HOST:-}}"
if [ -z "$HOST" ]; then
  echo "куда выкладывать? GITHUB_ZOO_HOST=user@host ./deploy.sh" >&2
  exit 2
fi
DIR="${GITHUB_ZOO_DIR:-/opt/github-zoo}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "== проверки перед выкладкой"
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck --shell=bash install.sh deploy.sh
  echo "   shellcheck: чисто"
else
  echo "   shellcheck не установлен — пропускаю (CI всё равно проверит)"
fi
if python3 -c "import pytest" 2>/dev/null; then
  python3 -m pytest tests/ -q
else
  echo "   pytest не установлен — пропускаю (CI всё равно проверит)"
fi
python3 -m compileall -q collector

commit="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
dirty=""
if ! git diff --quiet HEAD 2>/dev/null; then
  dirty=" +правки-вне-коммита"
  echo "   ⚠ рабочее дерево грязное: выкладывается код, которого нет в коммите"
fi
stamp="$(date +%Y-%m-%dT%H:%M:%S%z)"

echo "== выкладываю на $HOST:$DIR"
# The paths expand here on purpose: they name a directory on that host, and
# this side is the only one that knows which host was asked for.
# shellcheck disable=SC2029
tar czf - collector ui index.html style.css install.sh \
  --exclude='collector/config.json' --exclude='__pycache__' \
  | ssh "$HOST" "tar xzf - -C '$DIR'"
# shellcheck disable=SC2029
printf '%s\n' "$commit$dirty" "$stamp" | ssh "$HOST" "cat > '$DIR/VERSION'"

echo "== перезапуск"
ssh "$HOST" "sudo -n systemctl restart github-zoo && sleep 3 && systemctl is-active github-zoo"

# A service that starts and a dashboard that answers are different claims.
port="${GITHUB_ZOO_PORT:-8817}"
# shellcheck disable=SC2029
if ssh "$HOST" "curl -sf --max-time 15 http://127.0.0.1:$port/api/state >/dev/null"; then
  echo "== готово: $commit$dirty, дашборд отвечает"
else
  echo "== ВНИМАНИЕ: сервис поднялся, но /api/state не отвечает" >&2
  exit 1
fi
