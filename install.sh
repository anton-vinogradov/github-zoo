#!/usr/bin/env bash
# github-zoo — install/update as a systemd service on a Linux host.
#
# One-liner:
#   curl -fsSL https://raw.githubusercontent.com/anton-vinogradov/github-zoo/main/install.sh | sudo bash
#
# From a clone:
#   git clone <repo> github-zoo && cd github-zoo && sudo ./install.sh
#
# Re-running is the update path; it is idempotent and never overwrites
# /etc/github-zoo.json once it exists.
set -euo pipefail

REPO_URL="${GZ_REPO:-https://github.com/anton-vinogradov/github-zoo.git}"
SVC=github-zoo
DIR="${GZ_DIR:-/opt/github-zoo}"
CONFIG=/etc/github-zoo.json
STATE="${GZ_STATE:-/var/lib/github-zoo}"
PORT="${GZ_PORT:-8817}"

# --- where the code lives: run from a clone, or fetch one ---
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$(dirname -- "$SELF")/collector/hub.py" ]; then
  SRC="$(cd "$(dirname -- "$SELF")" && pwd)"
else
  command -v git >/dev/null || { echo "✗ нужен git"; exit 1; }
  echo "→ код в $DIR"
  if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only
  else git clone --depth 1 "$REPO_URL" "$DIR"; fi
  SRC="$DIR"
fi

if [ "$(id -u)" -eq 0 ]; then RUN_USER="${SUDO_USER:-root}"; SUDO=""; else RUN_USER="$(id -un)"; SUDO="sudo"; fi
command -v python3 >/dev/null || { echo "✗ нужен python3"; exit 1; }

if [ "$SRC" != "$DIR" ]; then
  echo "→ ставлю в $DIR"
  $SUDO mkdir -p "$DIR"
  $SUDO cp -r "$SRC/collector" "$SRC/ui" "$SRC/index.html" "$SRC/style.css" "$DIR/"
fi
$SUDO chown -R "$RUN_USER": "$DIR"

# What is actually running, for the dashboard footer. deploy.sh writes the same
# file from a working copy; without this, an install straight from git shows
# "unknown" and there is no way to tell prod from the repository.
if [ -d "$DIR/.git" ] && command -v git >/dev/null; then
  commit="$(git -C "$DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  printf '%s\n%s\n' "$commit" "$(date +%Y-%m-%dT%H:%M:%S%z)" \
    | $SUDO tee "$DIR/VERSION" >/dev/null
  $SUDO chown "$RUN_USER": "$DIR/VERSION"
fi

$SUDO mkdir -p "$STATE" /etc/github-zoo.d
$SUDO chown "$RUN_USER": "$STATE"
$SUDO chmod 750 /etc/github-zoo.d

# --- config: created once from the example, then left alone ---
if [ ! -f "$CONFIG" ]; then
  echo "→ создаю $CONFIG из примера — отредактируйте и перезапустите сервис"
  $SUDO cp "$DIR/collector/config.example.json" "$CONFIG"
  $SUDO chown "$RUN_USER": "$CONFIG"
  $SUDO chmod 600 "$CONFIG"
else
  echo "→ оставляю существующий $CONFIG"
fi

# --- encrypted secrets, if any exist ---
# systemd refuses to start a service whose LoadCredentialEncrypted file is
# missing, so these lines are generated only for secrets that are there.
CREDS=""
for cred in github-token telegram-token; do
  if [ -f "/etc/github-zoo.d/$cred.cred" ]; then
    CREDS="${CREDS}LoadCredentialEncrypted=$cred:/etc/github-zoo.d/$cred.cred"$'\n'
  fi
done

if [ ! -f /etc/github-zoo.d/github-token.cred ]; then
  cat <<'HINT'

  ⚠ Токен GitHub не найден. Создайте его с правами repo + read:org + notifications
    (https://github.com/settings/tokens) и положите зашифрованным:

      printf %s 'ghp_ваш_токен' | sudo systemd-creds encrypt --name=github-token - \
          /etc/github-zoo.d/github-token.cred

    Затем запустите install.sh ещё раз. Ключ шифрования привязан к этой машине,
    читаемого секрета на диске не остаётся.

HINT
fi

# --- systemd unit ---
echo "→ юнит systemd $SVC"
$SUDO tee "/etc/systemd/system/$SVC.service" >/dev/null <<UNIT
[Unit]
Description=github-zoo dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
$CREDS
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/collector/hub.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
StateDirectory=github-zoo

# Nothing here needs to write outside its own state directory, and the token
# is the sort of thing worth keeping behind a few walls.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$STATE
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
UNIT

$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SVC" >/dev/null
$SUDO systemctl restart "$SVC"
sleep 2
if $SUDO systemctl is-active --quiet "$SVC"; then
  echo "✓ $SVC запущен"
else
  echo "✗ $SVC не стартовал:"
  $SUDO journalctl -u "$SVC" -n 20 --no-pager
  exit 1
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "✓ дашборд: http://${IP:-localhost}:$PORT"
