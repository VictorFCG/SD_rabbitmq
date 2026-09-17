# Sobe o RabbitMQ (Docker), instala dependencias, gera chaves e inicia os processos.
# Uso:  powershell -ExecutionPolicy Bypass -File .\run.ps1

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location -LiteralPath $Root

function Test-Python {
    param([string]$Command)
    try {
        & $Command --version *> $null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Get-Python {
    foreach ($candidate in @("python", "py")) {
        if ((Get-Command $candidate -ErrorAction SilentlyContinue) -and (Test-Python $candidate)) {
            return $candidate
        }
    }
    throw "Python nao encontrado ou nao executavel no PATH."
}

$Python = Get-Python
Write-Host "Python: $Python"

# 1. RabbitMQ via docker compose ---------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker nao encontrado. Abra o Docker Desktop e tente novamente."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop nao esta em execucao. Abra-o e rode o script de novo."
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose nao encontrado (comando 'docker compose')."
}

Write-Host "Subindo o RabbitMQ via docker compose (pode baixar a imagem na primeira vez)..."
docker compose up -d --wait 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Falha ao subir o RabbitMQ via docker compose." }
Write-Host "RabbitMQ pronto (painel: http://localhost:15672, guest/guest)."

# 2. Dependencias e chaves ----------------------------------------------------
Write-Host "Instalando dependencias Python..."
& $Python -m pip install -r requirements.txt 2>&1
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependencias (pip)." }

Write-Host "Gerando/distribuindo chaves..."
& $Python setup_keys.py 2>&1
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar/distribuir as chaves." }

# 3. Inicia os servicos em janelas separadas ---------------------------------
$services = @("ms_estoque", "ms_pagamento", "ms_entrega", "ms_promocoes", "consumidor_c1", "consumidor_c2")
foreach ($s in $services) {
    Write-Host "Iniciando $s..."
    $cmd = "`$host.UI.RawUI.WindowTitle='$s'; Set-Location -LiteralPath '$Root'; & '$Python' '$s/main.py'"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd
}

Write-Host "Aguardando os servicos subirem..."
Start-Sleep -Seconds 5

# 4. MS Principal no terminal atual ------------------------------------------
Write-Host "Iniciando MS Principal..."
& $Python "ms_principal/main.py"
