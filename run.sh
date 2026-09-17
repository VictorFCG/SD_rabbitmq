#!/usr/bin/env bash
# Sobe o RabbitMQ (Docker), instala dependencias, gera chaves e inicia os processos.
# Uso:  ./run.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

CONTAINER="rabbitmq-ecommerce"
LOG_DIR="$ROOT/logs"

# --- Python -----------------------------------------------------------------
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "Python 3 nao encontrado ou nao executavel no PATH." >&2
    exit 1
fi
echo "Python: $PYTHON ($("$PYTHON" --version 2>&1))"

# --- Docker -----------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "Docker nao encontrado. Instale/abra o Docker e tente novamente." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker nao esta em execucao (ou o usuario nao tem permissao)." >&2
    exit 1
fi

if [ -n "$(docker ps --filter "name=^/${CONTAINER}$" --format '{{.Names}}')" ]; then
    echo "RabbitMQ ja esta em execucao ($CONTAINER)."
elif [ -n "$(docker ps -a --filter "name=^/${CONTAINER}$" --format '{{.Names}}')" ]; then
    echo "Iniciando container $CONTAINER..."
    docker start "$CONTAINER" >/dev/null
else
    echo "Criando container $CONTAINER (pode baixar a imagem na primeira vez)..."
    docker run -d --name "$CONTAINER" -p 5672:5672 -p 15672:15672 rabbitmq:3-management >/dev/null
fi

echo "Aguardando o RabbitMQ ficar pronto..."
deadline=$(( $(date +%s) + 90 ))
ready=0
while [ "$(date +%s)" -lt "$deadline" ]; do
    if docker exec "$CONTAINER" rabbitmq-diagnostics -q ping >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
if [ "$ready" -ne 1 ]; then
    echo "RabbitMQ nao respondeu a tempo. Verifique o Docker." >&2
    exit 1
fi
echo "RabbitMQ pronto (painel: http://localhost:15672, guest/guest)."

# --- Dependencias e chaves ---------------------------------------------------
if "$PYTHON" -c "import pika, Crypto" >/dev/null 2>&1; then
    echo "Dependencias Python ja disponiveis."
else
    echo "Instalando dependencias Python..."
    if ! "$PYTHON" -m pip install -r requirements.txt; then
        echo "pip falhou (ambiente gerenciado pelo SO?). Tentando virtualenv..."
        if "$PYTHON" -m venv "$ROOT/.venv"; then
            PYTHON="$ROOT/.venv/bin/python"
            "$PYTHON" -m pip install -r requirements.txt
        else
            echo "Nao foi possivel instalar as dependencias." >&2
            echo "Instale o modulo venv (ex.: sudo apt install python3-venv) ou rode:" >&2
            echo "  $PYTHON -m pip install --break-system-packages -r requirements.txt" >&2
            exit 1
        fi
    fi
fi

echo "Gerando/distribuindo chaves..."
"$PYTHON" setup_keys.py

# --- Servicos em background --------------------------------------------------
mkdir -p "$LOG_DIR"
SERVICES=(ms_estoque ms_pagamento ms_entrega ms_promocoes consumidor_c1 consumidor_c2)
PIDS=()

cleanup() {
    echo
    echo "Encerrando servicos..."
    for pid in "${PIDS[@]:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for s in "${SERVICES[@]}"; do
    echo "Iniciando $s (log: logs/$s.log)..."
    "$PYTHON" -u "$s/main.py" >"$LOG_DIR/$s.log" 2>&1 &
    PIDS+=("$!")
done

echo "Aguardando os servicos subirem..."
sleep 5

# --- MS Principal no terminal atual -----------------------------------------
echo "Iniciando MS Principal..."
"$PYTHON" -u ms_principal/main.py
