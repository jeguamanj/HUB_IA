#!/usr/bin/env bash
# =============================================================================
# deploy_server.sh — BLOQUEADO; no usar para despliegues
# -----------------------------------------------------------------------------
# Sustituido por el procedimiento revisado en DEPLOYMENT_PRODUCTION.md.
# =============================================================================

set -euo pipefail

echo "ERROR: deploy_server.sh está bloqueado. El script heredado puede borrar"
echo "paquetes/logs y configurar SQLite/credenciales inseguras."
echo "Consulta DEPLOYMENT_PRODUCTION.md y despliega solo con autorización."
exit 1

# ----------------------------------------------------------------------------
# CONFIG — edita estos valores
# ----------------------------------------------------------------------------
APP_NAME="mcp-hub-ia"
APP_DIR="/opt/mcp-hub-ia"
APP_PORT="8000"
GIT_REPO="https://github.com/jeguamanj/HUB_IA.git"
# Usuario unix que ejecutará el servicio (tu usuario del server):
APP_USER="${SUDO_USER:-$USER}"
APP_USER="${APP_USER:-ubuntu}"

# Secretos (DEBES rellenarlos; nunca subas passwords al git público)
OPENROUTER_KEY="${OPENROUTER_API_KEY:-}"
# DATABASE_URL: usa SQLite local por defecto, o Postgres de la nube si pones la URL.
DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///$APP_DIR/data/app.db}"

echo "======================================================================"
echo "  Despliegue $APP_NAME en servidor virtual"
echo "  - App dir:    $APP_DIR"
echo "  - Puerto:     $APP_PORT"
echo "  - Usuario:    $APP_USER"
echo "======================================================================"

# ----------------------------------------------------------------------------
# 1) Pre-chequeos + limpieza de disco (estaba al 96%!).
# ----------------------------------------------------------------------------
echo ">>> Limpiando disco antes de instalar..."
sudo apt-get update -y >/dev/null 2>&1 || echo "apt update no completo (ok si offline)" 
sudo apt-get autoremove --purge -y >/dev/null 2>&1 || true
sudo apt-get clean >/dev/null 2>&1 || true
sudo journalctl --vacuum-time=7d >/dev/null 2>&1 || true
df -h / | tail -1

# ----------------------------------------------------------------------------
# 2) Instalar Python 3.11 (deadsnakes) SIN quitar python 3.10
# ----------------------------------------------------------------------------
if command -v python3.11 >/dev/null 2>&1; then
  echo ">>> Python 3.11 ya instalado."
else
  echo ">>> Instalando Python 3.11 (deadsnakes)..."
  sudo apt-get install -y software-properties-common >/dev/null 2>&1 || true
  sudo add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1 || true
  sudo apt-get update -y >/dev/null 2>&1 || true
  sudo apt-get install -y python3.11 python3.11-venv python3.11-dev python3.11-distutils \
      libpq-dev build-essential 2>&1 | tail -5
fi
python3.11 --version || { echo "ERROR: no se instalo Python 3.11"; exit 1; }

# ----------------------------------------------------------------------------
# 3) Instalar uv (gestor de paquetes moderno del proyecto)
# ----------------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  echo ">>> uv ya instalado."
else
  echo ">>> Instalando uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || {
    # fallback: pip
    python3.11 -m pip install --user uv >/dev/null 2>&1 || sudo apt-get install -y uv 2>&1 | tail -3
  }
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version || echo "aviso: uv no en PATH; se usara ruta relativa"

# ----------------------------------------------------------------------------
# 4) Crear dir y clonar/actualizar repo
# ----------------------------------------------------------------------------
echo ">>> Preparando $APP_DIR ..."
sudo mkdir -p "$APP_DIR" "$APP_DIR/data"
sudo chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

cd "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  echo ">>> Repo ya clonado. Actualizando..."
  git pull --ff-only origin main 2>/dev/null || git pull --ff-only origin master 2>/dev/null || true
else
  echo ">>> Clonando repo $GIT_REPO ..."
  # Intento con credenciales/ssh; si falla y es publico, clona directo.
  git clone "$GIT_REPO" . || {
    echo "Falló clone autenticado. Prueba con token o ssh:";
    echo "  git clone git@github.com:jeguamanj/HUB_IA.git .";
    exit 1;
  }
fi

# ----------------------------------------------------------------------------
# 5) Crear .env de producción (edita con tus secretos reales)
# ----------------------------------------------------------------------------
echo ">>> Creando .env ..."
cat > "$APP_DIR/.env" <<EOF
# Producction env para $APP_NAME
DATABASE_URL=$DATABASE_URL
OPENROUTER_API_KEY=$OPENROUTER_KEY
MAX_SUBMISSION_ATTEMPTS=2
LOG_LEVEL=INFO
PORT=$APP_PORT
EOF
chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

# ----------------------------------------------------------------------------
# 6) Instalar dependencias con uv (Python 3.11)
# ----------------------------------------------------------------------------
echo ">>> Instalando dependencias (uv sync) ..."
export PATH="$HOME/.local/bin:$PATH"
cd "$APP_DIR"
# El proyecto trae requirements.txt portable; usa uv con py 3.11
uv sync --python 3.11 --frozen --all-extras --no-dev 2>&1 | tail -8 \
  || { echo "uv sync falló; probando install con requirements.txt"; \
       python3.11 -m pip install -r "$APP_DIR/requirements.txt" 2>&1 | tail -8; }

echo ">>> Verificación de import:"
"$APP_DIR/.venv/bin/python" -c "import fastapi, uvicorn, asyncpg; print('deps OK')" 2>&1 \
  || python3.11 -c "import fastapi, uvicorn, asyncpg; print('deps OK (system)')" 2>&1 || true

# ----------------------------------------------------------------------------
# 7) Servicio systemd
# ----------------------------------------------------------------------------
echo ">>> Configurando systemd ..."
cat > /tmp/mcp-hub.service <<EOF
[Unit]
Description=MCP HUB Server IA
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PYTHONUNBUFFERED=1
# Carga variables del .env (las define systemd como EnvironmentFile)
EnvironmentFile=$APP_DIR/.env
# Comando: usa el venv creado por uv; si no existe, usa python3.11 de sistema
ExecStart=$APP_DIR/.venv/bin/uvicorn API.server:app --host 0.0.0.0 --port $APP_PORT --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/mcp-hub.service /etc/systemd/system/"$APP_NAME".service
sudo systemctl daemon-reload
sudo systemctl enable "$APP_NAME" 2>&1 | tail -2
sudo systemctl start "$APP_NAME" 2>&1 | tail -3 || sudo systemctl status "$APP_NAME" --no-pager | head -20
echo ">>> Estado del servicio:"
sleep 3
sudo systemctl status "$APP_NAME" --no-pager | head -15

# ----------------------------------------------------------------------------
# 8) Nginx (reverse proxy)
# ----------------------------------------------------------------------------
echo ">>> Instalando/configurando Nginx (opcional, interrumpible con Ctrl+C)..."
sudo apt-get install -y nginx >/dev/null 2>&1 || true
sudo tee /etc/nginx/sites-available/"$APP_NAME" >/dev/null <<EOF
server {
    listen 80 default_server;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # LLM / cola async puede tardar: no limitar en 60s
        proxy_read_timeout 300;
        proxy_connect_timeout 60;
        proxy_send_timeout 300;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/"$APP_NAME" /etc/nginx/sites-enabled/"$APP_NAME"
sudo rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
sudo nginx -t 2>&1 | tail -3 && sudo systemctl reload nginx

echo ""
echo "======================================================================"
echo "  ✅ Despliegue completado"
echo "  - Servicio:    systemctl status $APP_NAME"
echo "  - Logs:        journalctl -u $APP_NAME -f"
echo "  - Smoke test:  curl -s http://127.0.0.1:$APP_PORT/openapi.json | head"
echo "  - Web:         http://IP_DEL_SERVIDOR/docs  (si Nginx en :80)"
echo "  - Edita secretos: sudo nano $APP_DIR/.env  (y systemctl restart $APP_NAME)"
echo "======================================================================"