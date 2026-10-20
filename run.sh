#!/usr/bin/env bash
# Sobe RabbitMQ e Mailpit (Docker), instala dependencias, gera chaves e inicia os
# microsservicos, o Mock de Pagamento e o frontend web.
# Uso:  ./run.sh    (Ctrl+C encerra o frontend e todos os servicos)
if [ -z "${BASH_VERSION:-}" ]; then
    echo "Execute com o bash: ./run.sh (ou bash run.sh)." >&2
    exit 1
fi
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
FRONTEND_DIR="$ROOT/frontend"
VENV_PYTHON="$ROOT/.venv/bin/python"
PY_MODULES="pika, Crypto, fastapi.sse, uvicorn, httpx"
SERVICES=(ms_estoque ms_pagamento ms_entrega ms_promocoes mock_pagamento ms_principal)
BACKEND_PORTS=(8001 8002 8003 8004 8005)
FRONTEND_PORT=5173
GATEWAY_HEALTH_URL="http://127.0.0.1:8001/api/saude"
STORE_URL="http://localhost:5173"

python_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

has_deps() {
    "$1" -c "import $PY_MODULES" >/dev/null 2>&1
}

PYTHON=""
too_old=""
if [ -x "$VENV_PYTHON" ] && python_ok "$VENV_PYTHON" && has_deps "$VENV_PYTHON"; then
    PYTHON="$VENV_PYTHON"
else
    for candidate in python3 python python3.14 python3.13 python3.12 python3.11 python3.10; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if python_ok "$candidate"; then
            PYTHON="$candidate"
            break
        fi
        version="$("$candidate" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
        if [ -n "$version" ]; then
            too_old="$too_old $candidate=$version"
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    echo "Python >= 3.10 nao encontrado no PATH.${too_old:+ Encontrado(s):$too_old}" >&2
    echo "Instale o Python 3.10 ou mais recente e tente novamente." >&2
    exit 1
fi
echo "Python: $PYTHON ($("$PYTHON" --version 2>&1))"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker nao encontrado. Instale/abra o Docker e tente novamente." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker nao esta em execucao (ou o usuario nao tem permissao)." >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose nao encontrado (comando 'docker compose')." >&2
    exit 1
fi

for s in "${SERVICES[@]}"; do
    if [ ! -f "$s/main.py" ]; then
        echo "Arquivo $s/main.py nao encontrado." >&2
        exit 1
    fi
done

# Sucesso se algum processo ja escuta em host:porta (usa o /dev/tcp do bash).
port_in_use() {
    (exec 3<>"/dev/tcp/$1/$2") >/dev/null 2>&1
}

busy=""
for port in "${BACKEND_PORTS[@]}"; do
    if port_in_use 127.0.0.1 "$port"; then
        busy="$busy $port"
    fi
done
if [ -n "$busy" ]; then
    echo "Porta(s) ja em uso:$busy." >&2
    echo "Encerre a execucao anterior (ou o processo que usa a porta; veja com 'ss -ltnp' ou 'lsof -iTCP -sTCP:LISTEN') e tente novamente." >&2
    exit 1
fi

echo "Subindo RabbitMQ e Mailpit via docker compose (pode baixar as imagens na primeira vez)..."
if ! docker compose up -d --wait; then
    echo "Falha ao subir RabbitMQ/Mailpit via docker compose." >&2
    exit 1
fi
echo "RabbitMQ pronto (painel: http://localhost:15672, guest/guest)."
echo "Mailpit pronto (e-mails: http://localhost:8025)."

install_in_venv() {
    if ! { python_ok "$VENV_PYTHON" && "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; }; then
        "$PYTHON" -m venv --clear "$ROOT/.venv" || return 1
    fi
    "$VENV_PYTHON" -m pip install -r requirements.txt || return 1
    PYTHON="$VENV_PYTHON"
}

deps_failed() {
    echo "Nao foi possivel instalar as dependencias (veja a mensagem do pip acima)." >&2
    echo "Se faltar o modulo venv, instale-o (ex.: sudo apt install python3-venv) ou rode:" >&2
    echo "  $PYTHON -m pip install --break-system-packages -r requirements.txt" >&2
    exit 1
}

if has_deps "$PYTHON"; then
    echo "Dependencias Python ja disponiveis."
elif [ -e "$VENV_PYTHON" ]; then
    echo "Instalando dependencias Python no .venv do projeto..."
    install_in_venv || deps_failed
else
    echo "Instalando dependencias Python..."
    if ! "$PYTHON" -m pip install -r requirements.txt; then
        echo "pip falhou (ambiente gerenciado pelo SO?). Tentando virtualenv (.venv)..."
        install_in_venv || deps_failed
    fi
fi
if ! has_deps "$PYTHON"; then
    echo "As dependencias Python continuam indisponiveis para $PYTHON." >&2
    exit 1
fi
echo "Python em uso: $PYTHON"

echo "Verificando as chaves RSA (geradas so na primeira execucao)..."
if ! "$PYTHON" setup_keys.py; then
    echo "Falha ao gerar/distribuir as chaves." >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
PIDS=()

alive_pids() {
    local pid
    for pid in "${PIDS[@]:-}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
        fi
    done
}

# INT apos o TERM: o uvicorn so larga as conexoes SSE abertas no segundo sinal.
cleanup() {
    set +e
    trap '' INT TERM HUP
    trap - EXIT
    echo
    echo "Encerrando servicos..."
    local sig pid i
    for sig in TERM INT KILL; do
        for pid in $(alive_pids); do
            kill -s "$sig" "$pid" 2>/dev/null
        done
        for i in 1 2 3 4; do
            if [ -z "$(alive_pids)" ]; then
                break
            fi
            sleep 0.5
        done
    done
    wait 2>/dev/null
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

for s in "${SERVICES[@]}"; do
    echo "Iniciando $s (log: logs/$s.log)..."
    "$PYTHON" -u "$s/main.py" >"$LOG_DIR/$s.log" 2>&1 &
    PIDS+=("$!")
done
GATEWAY_PID="$!"  # ms_principal e o ultimo da lista

gateway_responds() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --noproxy '*' -m 2 -o /dev/null "$GATEWAY_HEALTH_URL" >/dev/null 2>&1
    else
        "$PYTHON" -c 'import sys, urllib.request as r; r.build_opener(r.ProxyHandler({})).open(sys.argv[1], timeout=2)' \
            "$GATEWAY_HEALTH_URL" >/dev/null 2>&1
    fi
}

echo "Aguardando o API Gateway responder em $GATEWAY_HEALTH_URL..."
deadline=$(( $(date +%s) + 30 ))
gateway_ready=0
while [ "$(date +%s)" -lt "$deadline" ] && kill -0 "$GATEWAY_PID" 2>/dev/null; do
    if gateway_responds; then
        gateway_ready=1
        break
    fi
    sleep 1
done
if [ "$gateway_ready" -eq 1 ]; then
    echo "API Gateway pronto."
else
    echo "AVISO: o API Gateway nao respondeu em $GATEWAY_HEALTH_URL. Veja logs/ms_principal.log" >&2
fi

i=0
for s in "${SERVICES[@]}"; do
    if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
        echo "AVISO: $s encerrou. Ultimas linhas de logs/$s.log:" >&2
        tail -n 5 "$LOG_DIR/$s.log" | sed 's/^/    /' >&2 || true
    fi
    i=$((i + 1))
done

# Vite 8 requer Node.js ^20.19 ou >= 22.12.
node_version_ok() {
    local version major minor
    version="$(node --version 2>/dev/null)" || return 1
    version="${version#v}"
    major="${version%%.*}"
    minor="${version#*.}"
    minor="${minor%%.*}"
    case "$major.$minor" in
        *[!0-9.]* | .* | *.) return 1 ;;
    esac
    [ "$major" -gt 22 ] && return 0
    [ "$major" -eq 22 ] && [ "$minor" -ge 12 ] && return 0
    [ "$major" -eq 20 ] && [ "$minor" -ge 19 ] && return 0
    return 1
}

# O npm grava node_modules/.package-lock.json a cada install; sem ele, -nt da true.
frontend_needs_install() {
    [ "$FRONTEND_DIR/package.json" -nt "$FRONTEND_DIR/node_modules/.package-lock.json" ]
}

frontend_problem=""
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    frontend_problem="Node.js/npm nao encontrados"
elif [ ! -f "$FRONTEND_DIR/package.json" ]; then
    frontend_problem="frontend/package.json nao encontrado"
elif port_in_use localhost "$FRONTEND_PORT"; then
    frontend_problem="a porta $FRONTEND_PORT ja esta em uso"
else
    if ! node_version_ok; then
        echo "AVISO: o Vite 8 requer Node.js ^20.19 ou >= 22.12 (encontrado: $(node --version 2>&1 || true))." >&2
    fi
    if frontend_needs_install; then
        echo "Instalando dependencias do frontend (npm install)..."
        if ! (cd "$FRONTEND_DIR" && npm install); then
            frontend_problem="npm install falhou"
        fi
    fi
fi

store_note=""
if [ -n "$frontend_problem" ]; then
    store_note=" (nao iniciado: $frontend_problem)"
fi
echo
echo "=========================================================================="
printf '  %-23s %s\n' \
    "Loja (frontend)" "$STORE_URL$store_note" \
    "API Gateway (Swagger)" "http://127.0.0.1:8001/docs" \
    "MS Estoque" "http://127.0.0.1:8002/docs" \
    "MS Pagamento" "http://127.0.0.1:8003/docs" \
    "MS Promocoes" "http://127.0.0.1:8004/docs" \
    "Mock de Pagamento" "http://127.0.0.1:8005/docs" \
    "RabbitMQ" "http://localhost:15672 (guest/guest)" \
    "Mailpit (e-mails)" "http://localhost:8025" \
    "Logs dos servicos" "$LOG_DIR/<servico>.log"
echo "=========================================================================="
echo "Ctrl+C encerra tudo o que este script iniciou (servicos e frontend). RabbitMQ"
echo "e Mailpit continuam no Docker (encerre-os com: docker compose stop)."

if [ -z "$frontend_problem" ]; then
    echo "Iniciando o frontend (npm run dev)..."
    status=0
    (cd "$FRONTEND_DIR" && exec npm run dev) || status=$?
    echo "AVISO: o frontend encerrou (codigo $status). Os servicos continuam em execucao; Ctrl+C para encerrar." >&2
else
    echo "AVISO: frontend nao iniciado: $frontend_problem." >&2
    if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
        echo "Instale o Node.js 22 LTS (ou >= 20.19) com o npm: https://nodejs.org/en/download" >&2
        echo "(o nodejs do apt costuma ser antigo; prefira o nvm: nvm install 22) e rode ./run.sh de novo." >&2
    fi
    echo "Backend em execucao. Pressione Ctrl+C para encerrar."
fi
wait || true
echo "Todos os servicos encerraram."
