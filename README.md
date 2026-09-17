# E-commerce MOM (RabbitMQ)

Comunicação 100% por eventos (RabbitMQ),
com assinatura/validação digital RSA dos envelopes.

## Pré-requisitos

```bash
pip install -r requirements.txt
```

O `docker-compose.yml` sobe o RabbitMQ (painel em http://localhost:15672, guest/guest):

```bash
docker compose up -d
```

## Chaves

```bash
python setup_keys.py
```

Gera um par RSA por processo e coloca em `<processo>/keys/` a chave privada e as
chaves públicas de todos os processos.

## Execução automática (Windows / PowerShell + Docker Desktop)

Com o Docker Desktop aberto, na pasta do projeto:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

O script sobe o RabbitMQ, instala as dependências, gera as chaves, abre uma janela
para cada microsserviço/consumidor e inicia o MS Principal no terminal atual.

## Execução automática (Linux / macOS)

```bash
./run.sh
```

Sobe o RabbitMQ via `docker compose`, verifica/instala as dependências, gera as chaves,
roda os microsserviços/consumidores em background (logs em `logs/`) e inicia o MS
Principal no terminal atual.

## Execução manual

Abra um terminal para cada processo (nesta ordem):

```bash
python ms_estoque/main.py
python ms_pagamento/main.py
python ms_entrega/main.py
python ms_promocoes/main.py
python consumidor_c1/main.py
python consumidor_c2/main.py
python ms_principal/main.py   # interface de terminal
```

No `ms_principal`, use o menu para ver produtos, fazer/consultar/excluir pedidos.
