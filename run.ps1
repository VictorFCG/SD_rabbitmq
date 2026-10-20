# Sobe RabbitMQ e Mailpit (Docker), instala dependencias, gera chaves e inicia os
# microsservicos, o Mock de Pagamento e o frontend web, cada um em sua janela.
# Uso:  powershell -ExecutionPolicy Bypass -File .\run.ps1

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest sem barra de progresso
$Root = $PSScriptRoot
Set-Location -LiteralPath $Root

$MinPython = [version]"3.10"
$PyModules = "pika, Crypto, fastapi.sse, uvicorn, httpx"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Services = @("ms_estoque", "ms_pagamento", "ms_entrega", "ms_promocoes", "mock_pagamento", "ms_principal")
# Portas dos processos com HTTP (o ms_entrega so usa o RabbitMQ).
$HttpPorts = [ordered]@{
    "ms_principal"   = 8001
    "ms_estoque"     = 8002
    "ms_pagamento"   = 8003
    "ms_promocoes"   = 8004
    "mock_pagamento" = 8005
}
$FrontendPort = 5173
$Frontend = Join-Path $Root "frontend"
$GatewayHealthUrl = "http://127.0.0.1:8001/api/saude"
$StoreUrl = "http://localhost:5173"

# No Windows PowerShell 5.1, com "Stop", o stderr de comandos nativos (docker, pip)
# vira erro terminante (NativeCommandError). O exit code fica em $NativeExit.
function Invoke-Native {
    param([Parameter(Mandatory)][scriptblock]$Block)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Block
    $script:NativeExit = $LASTEXITCODE
    $ErrorActionPreference = $previous
}

# $null se nao executar (ex.: o atalho "python" da Microsoft Store sem o Python).
function Get-PythonVersion {
    param([string]$Command)
    $out = Invoke-Native { & $Command -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null }
    if ($NativeExit -ne 0 -or "$out" -notmatch '^\d+\.\d+$') { return $null }
    return [version]"$out"
}

function Test-PythonDeps {
    param([string]$Command)
    Invoke-Native { & $Command -c "import $PyModules" *> $null }
    return ($NativeExit -eq 0)
}

function Test-VenvPython {
    if (-not (Test-Path -LiteralPath $VenvPython)) { return $false }
    $version = Get-PythonVersion $VenvPython
    if (-not $version -or $version -lt $MinPython) { return $false }
    if (Test-PythonDeps $VenvPython) { return $true }
    Invoke-Native { & $VenvPython -m pip --version *> $null }
    return ($NativeExit -eq 0)
}

function Get-Python {
    if (Test-VenvPython) { return $VenvPython }
    $tooOld = @()
    foreach ($candidate in @("python", "py")) {
        if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
        $version = Get-PythonVersion $candidate
        if ($version -and $version -ge $MinPython) { return $candidate }
        if ($version) { $tooOld += "$candidate $version" }
    }
    $hint = "Instale o Python 3.10 ou mais recente (https://www.python.org/downloads/, marcando 'Add python.exe to PATH')."
    if ($tooOld.Count -gt 0) {
        throw "Python >= 3.10 nao encontrado (encontrado: $($tooOld -join ', ')). $hint"
    }
    throw "Python nao encontrado ou nao executavel no PATH. $hint"
}

function Get-ListeningPorts {
    try {
        $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        return @($listeners | ForEach-Object { $_.Port })
    }
    catch {
        return @()
    }
}

function Test-PortInUse {
    param([int]$Port)
    return ((Get-ListeningPorts) -contains $Port)
}

function Test-Url {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -eq 200)
    }
    catch {
        return $false
    }
}

# -NoExit mantem a janela aberta para ver o log e os erros.
function Start-Window {
    param([string]$Title, [string]$Folder, [string]$Command)
    $folderLiteral = $Folder.Replace("'", "''")
    $cmd = "`$host.UI.RawUI.WindowTitle='$Title'; Set-Location -LiteralPath '$folderLiteral'; $Command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd
}

# npm.cmd/npm.exe primeiro: o npm.ps1 dependeria da politica de execucao.
function Get-Npm {
    foreach ($candidate in @("npm.cmd", "npm.exe", "npm")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) { return $candidate }
    }
    return $null
}

function Get-NodeVersionText {
    $out = Invoke-Native { node --version 2>$null }
    if ($NativeExit -ne 0) { return "" }
    return "$out".Trim()
}

# Vite 8 requer Node.js ^20.19 ou >= 22.12.
function Test-NodeVersion {
    param([string]$Text)
    if (-not ($Text -match '^v?(\d+)\.(\d+)')) { return $false }
    $version = [version]"$($Matches[1]).$($Matches[2])"
    return (($version -ge [version]"22.12") -or ($version.Major -eq 20 -and $version -ge [version]"20.19"))
}

# O npm grava node_modules\.package-lock.json a cada install.
function Test-FrontendInstallNeeded {
    $marker = Join-Path $Frontend "node_modules\.package-lock.json"
    if (-not (Test-Path -LiteralPath $marker)) { return $true }
    $packageJson = Get-Item -LiteralPath (Join-Path $Frontend "package.json")
    return ($packageJson.LastWriteTimeUtc -gt (Get-Item -LiteralPath $marker -Force).LastWriteTimeUtc)
}

function Write-Url {
    param([string]$Name, [string]$Url)
    Write-Host ("  {0,-23} {1}" -f $Name, $Url)
}

$Python = Get-Python
Write-Host "Python: $Python ($(Get-PythonVersion $Python))"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker nao encontrado. Abra o Docker Desktop e tente novamente."
}

Invoke-Native { docker info *> $null }
if ($NativeExit -ne 0) {
    throw "Docker Desktop nao esta em execucao. Abra-o e rode o script de novo."
}

Invoke-Native { docker compose version *> $null }
if ($NativeExit -ne 0) {
    throw "Docker Compose nao encontrado (comando 'docker compose')."
}

foreach ($s in $Services) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root "$s\main.py"))) {
        throw "Arquivo $s\main.py nao encontrado."
    }
}

$busyPorts = @($HttpPorts.Values | Where-Object { Test-PortInUse $_ })
if ($busyPorts.Count -gt 0) {
    throw "Porta(s) ja em uso: $($busyPorts -join ', '). Feche as janelas de uma execucao anterior (ou o processo que usa a porta) e rode o script de novo."
}

Write-Host "Subindo RabbitMQ e Mailpit via docker compose (pode baixar as imagens na primeira vez)..."
$composeOutput = Invoke-Native { docker compose up -d --wait 2>&1 | ForEach-Object { "$_" } }
if ($NativeExit -ne 0) {
    if ($composeOutput) { $composeOutput | ForEach-Object { Write-Host $_ } }
    throw "Falha ao subir RabbitMQ/Mailpit via docker compose."
}
Write-Host "RabbitMQ pronto (painel: http://localhost:15672, guest/guest)."
Write-Host "Mailpit pronto (e-mails: http://localhost:8025)."

if ($Python -ne $VenvPython) {
    $acao = "Criando"
    if (Test-Path -LiteralPath (Join-Path $Root ".venv")) { $acao = "Recriando" }
    Write-Host "$acao o ambiente virtual do projeto (.venv) com $Python..."
    Invoke-Native { & $Python -m venv --clear .venv 2>&1 | ForEach-Object { "$_" } }
    if ($NativeExit -eq 0 -and (Test-Path -LiteralPath $VenvPython)) {
        $Python = $VenvPython
    }
    else {
        Write-Host "AVISO: nao foi possivel criar o .venv (veja a mensagem acima). Usando $Python diretamente; as dependencias que faltarem serao instaladas nele." -ForegroundColor Yellow
    }
}
if (Test-PythonDeps $Python) {
    Write-Host "Dependencias Python ja disponiveis."
}
else {
    Write-Host "Instalando dependencias Python..."
    Invoke-Native { & $Python -m pip install -r requirements.txt 2>&1 | ForEach-Object { "$_" } }
    if ($NativeExit -ne 0) { throw "Falha ao instalar as dependencias (pip)." }
    if (-not (Test-PythonDeps $Python)) { throw "As dependencias Python continuam indisponiveis para $Python." }
}
Write-Host "Python em uso: $Python"

Write-Host "Verificando as chaves RSA (geradas so na primeira execucao)..."
Invoke-Native { & $Python setup_keys.py 2>&1 | ForEach-Object { "$_" } }
if ($NativeExit -ne 0) { throw "Falha ao gerar/distribuir as chaves." }

$pythonLiteral = $Python.Replace("'", "''")
foreach ($s in $Services) {
    Write-Host "Iniciando $s..."
    Start-Window $s $Root "& '$pythonLiteral' '$s/main.py'"
}

Write-Host "Aguardando o API Gateway responder em $GatewayHealthUrl..."
$deadline = (Get-Date).AddSeconds(30)
$gatewayReady = $false
$notListening = @($HttpPorts.Keys)
while ((Get-Date) -lt $deadline) {
    if (-not $gatewayReady) { $gatewayReady = Test-Url $GatewayHealthUrl }
    $notListening = @($HttpPorts.Keys | Where-Object { -not (Test-PortInUse $HttpPorts[$_]) })
    if ($gatewayReady -and $notListening.Count -eq 0) { break }
    Start-Sleep -Seconds 1
}
if ($gatewayReady) {
    Write-Host "API Gateway pronto."
}
else {
    Write-Host "AVISO: o API Gateway nao respondeu em $GatewayHealthUrl. Veja a janela 'ms_principal'." -ForegroundColor Yellow
}
foreach ($name in $notListening) {
    Write-Host "AVISO: $name nao esta escutando na porta $($HttpPorts[$name]). Veja a janela '$name'." -ForegroundColor Yellow
}

$frontendProblem = ""
$npm = Get-Npm
if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not $npm) {
    $frontendProblem = "Node.js/npm nao encontrados"
}
elseif (-not (Test-Path -LiteralPath (Join-Path $Frontend "package.json"))) {
    $frontendProblem = "frontend\package.json nao encontrado"
}
elseif (Test-PortInUse $FrontendPort) {
    $frontendProblem = "a porta $FrontendPort ja esta em uso"
}
else {
    $nodeVersion = Get-NodeVersionText
    if (-not (Test-NodeVersion $nodeVersion)) {
        Write-Host "AVISO: o Vite 8 requer Node.js ^20.19 ou >= 22.12 (encontrado: $nodeVersion)." -ForegroundColor Yellow
    }
    if (Test-FrontendInstallNeeded) {
        Write-Host "Instalando dependencias do frontend (npm install)..."
        Push-Location -LiteralPath $Frontend
        try {
            Invoke-Native { & $npm install 2>&1 | ForEach-Object { "$_" } }
        }
        finally {
            Pop-Location
        }
        if ($NativeExit -ne 0) { $frontendProblem = "npm install falhou" }
    }
}

if (-not $frontendProblem) {
    Write-Host "Iniciando o frontend (npm run dev)..."
    Start-Window "frontend" $Frontend "& '$npm' run dev"
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-PortInUse $FrontendPort) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (Test-PortInUse $FrontendPort) {
        Write-Host "Abrindo $StoreUrl no navegador..."
        try {
            Start-Process $StoreUrl
        }
        catch {
            Write-Host "Nao foi possivel abrir o navegador; acesse $StoreUrl manualmente." -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "AVISO: o frontend ainda nao responde na porta $FrontendPort (veja a janela 'frontend'); abra $StoreUrl quando ele estiver pronto." -ForegroundColor Yellow
    }
}

$storeLine = $StoreUrl
if ($frontendProblem) { $storeLine = "$StoreUrl (nao iniciado: $frontendProblem)" }
Write-Host ""
Write-Host "=========================================================================="
Write-Url "Loja (frontend)" $storeLine
Write-Url "API Gateway (Swagger)" "http://127.0.0.1:8001/docs"
Write-Url "MS Estoque" "http://127.0.0.1:8002/docs"
Write-Url "MS Pagamento" "http://127.0.0.1:8003/docs"
Write-Url "MS Promocoes" "http://127.0.0.1:8004/docs"
Write-Url "Mock de Pagamento" "http://127.0.0.1:8005/docs"
Write-Url "RabbitMQ" "http://localhost:15672 (guest/guest)"
Write-Url "Mailpit (e-mails)" "http://localhost:8025"
Write-Url "Logs dos servicos" "na janela de cada servico (titulo = nome do servico)"
Write-Host "=========================================================================="
if ($frontendProblem -eq "Node.js/npm nao encontrados") {
    Write-Host "Para usar a loja, instale o Node.js 22 LTS com o npm (https://nodejs.org ou: winget install OpenJS.NodeJS.LTS)," -ForegroundColor Yellow
    Write-Host "abra um novo terminal e rode o script de novo. O backend continua em execucao." -ForegroundColor Yellow
}
elseif ($frontendProblem) {
    Write-Host "AVISO: frontend nao iniciado: $frontendProblem. O backend continua em execucao." -ForegroundColor Yellow
}
Write-Host "Para encerrar, feche as janelas abertas. RabbitMQ e Mailpit continuam no Docker"
Write-Host "(encerre-os com: docker compose stop)."
