#!/usr/bin/env bash
# Устанавливает MCP-сервер Qalqan Platform и туннель на Ubuntu 24.04.
# Запуск: sudo bash setup.sh /path/to/.env [/path/to/oauth-storage-dir]
# NO_START=1 — только установить, службы не запускать (пока работает старая копия).
# Повторный запуск безопасен: обновляет код и перезапускает службы.
set -euo pipefail

ENV_FILE=${1:?укажите путь к .env}
OAUTH_DIR=${2:-}
APP=/opt/qalqan-mcp
DATA=/var/lib/qalqan-mcp
REPO=https://github.com/Nurperzent7/qalqan-mcp.git

# У e2-micro всего 1 ГБ памяти; подкачка страхует pip и сервер.
if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

apt-get update -qq
apt-get install -y -qq python3-venv git curl ca-certificates

if ! command -v cloudflared >/dev/null; then
    mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
        > /etc/apt/sources.list.d/cloudflared.list
    apt-get update -qq
    apt-get install -y -qq cloudflared
fi

id qalqan >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin qalqan
mkdir -p "$DATA" /etc/qalqan-mcp

if [ -d "$APP/.git" ]; then
    git -C "$APP" pull -q --ff-only
else
    git clone -q "$REPO" "$APP"
fi
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

install -m 600 "$ENV_FILE" "$APP/.env"
grep '^TUNNEL_TOKEN=' "$APP/.env" | sed -E "s/^TUNNEL_TOKEN=.*[[:space:]]?(eyJ[^[:space:]'\"]+).*/TUNNEL_TOKEN=\1/" \
    > /etc/qalqan-mcp/tunnel.env
chmod 600 /etc/qalqan-mcp/tunnel.env

if [ -n "$OAUTH_DIR" ]; then
    mkdir -p "$DATA/oauth-proxy"
    cp -a "$OAUTH_DIR" "$DATA/oauth-proxy/"
fi

chown -R qalqan:qalqan "$APP" "$DATA" /etc/qalqan-mcp

install -m 644 "$APP/deploy/qalqan-mcp.service" /etc/systemd/system/
install -m 644 "$APP/deploy/qalqan-tunnel.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable qalqan-mcp qalqan-tunnel
if [ "${NO_START:-0}" = 1 ]; then
    echo "Установлено; службы не запущены (NO_START=1)."
    exit 0
fi
systemctl restart qalqan-mcp qalqan-tunnel

sleep 5
curl -fsS http://127.0.0.1:3847/health && echo
systemctl --no-pager --lines=0 status qalqan-mcp qalqan-tunnel
