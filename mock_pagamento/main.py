# Mock de Pagamento "PayMock" (sistema externo): nao usa o RabbitMQ nem common/.
# API REST usada pelo MS Pagamento (criar, consultar e cancelar cobrancas) e
# pagina de checkout onde o cliente aprova ou recusa o pagamento.
# O resultado vai para a loja por Webhook (POST na webhook_url), assinado com
# HMAC-SHA256 no cabecalho X-Webhook-Signature. Cobrancas apenas em memoria.
import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

import mock_paginas


def _valor_env(bruto):
    valor = bruto.strip()
    if valor[:1] in ("'", '"'):
        fim = valor.find(valor[0], 1)
        if fim > 0:
            return valor[1:fim]
    for i, caractere in enumerate(valor):
        if caractere == "#" and (i == 0 or valor[i - 1] in " \t"):
            return valor[:i].rstrip()
    return valor


def _ler_env(caminho):
    # Mesmas regras de common/config.py (o mock nao importa common/).
    valores = {}
    if not caminho.is_file():
        return valores
    for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        if linha.startswith("export "):
            linha = linha[len("export "):]
        chave, valor = linha.split("=", 1)
        valores.setdefault(chave.strip(), _valor_env(valor))
    return valores


_ARQUIVO_ENV = _ler_env(Path(__file__).resolve().parent.parent / ".env")


def _env(nome, padrao):
    valor = os.environ[nome] if nome in os.environ else _ARQUIVO_ENV.get(nome, "")
    return valor.strip() or padrao


HTTP_HOST = _env("HTTP_HOST", "127.0.0.1")
PORTA_MOCK = int(_env("MOCK_PORT", "8005"))
URL_PUBLICA = _env("MOCK_PUBLIC_URL", f"http://127.0.0.1:{PORTA_MOCK}").rstrip("/")
WEBHOOK_SECRET = _env("WEBHOOK_SECRET", "trab2-webhook-secret")

TIMEOUT_WEBHOOK = 5.0
ESPERAS_WEBHOOK = (0.5, 1.0)  # segundos entre as tentativas
LIMITE_RESPOSTA = 500

PENDENTE = "PENDENTE"
APROVADO = "APROVADO"
RECUSADO = "RECUSADO"
CANCELADO = "CANCELADO"
DECISOES = (APROVADO, RECUSADO)

cobrancas = {}
em_envio = set()
lock = threading.Lock()
transporte_webhook = None  # os testes trocam por httpx.MockTransport


def log(mensagem):
    print(f"[mock] {mensagem}".encode("ascii", "replace").decode("ascii"), flush=True)


def agora():
    return datetime.now(timezone.utc).isoformat()


def url_http_valida(url):
    if any(ord(caractere) <= 32 or ord(caractere) == 127 for caractere in url):
        return False
    try:
        partes = urlsplit(url)
        partes.port  # noqa: B018 - porta invalida levanta ValueError
    except ValueError:
        return False
    return partes.scheme in ("http", "https") and bool(partes.hostname)


def normalizar_id(id_cobranca):
    return id_cobranca.strip().upper()


def url_checkout(id_cobranca):
    return f"{URL_PUBLICA}/checkout/{id_cobranca}"


def novo_id():
    # Chamada com o lock preso.
    while True:
        id_cobranca = "COB" + secrets.token_hex(5).upper()
        if id_cobranca not in cobrancas:
            return id_cobranca


def obter(id_cobranca):
    with lock:
        cobranca = cobrancas.get(normalizar_id(id_cobranca))
        if cobranca is None:
            return None, False
        return copy.deepcopy(cobranca), cobranca["id_cobranca"] in em_envio


def resposta_api(cobranca):
    return {**cobranca, "url_checkout": url_checkout(cobranca["id_cobranca"])}


def html(conteudo, status_code=200):
    return HTMLResponse(conteudo, status_code=status_code, headers={"Cache-Control": "no-store"})


def ler_decisao(corpo):
    try:
        campos = parse_qs(corpo.decode("utf-8", errors="replace"), max_num_fields=10)
    except ValueError:
        return None
    valores = {valor.strip().upper() for valor in campos.get("decisao", []) if valor.strip()}
    if len(valores) != 1:
        return None
    decisao = valores.pop()
    return decisao if decisao in DECISOES else None


def assinar(corpo):
    digest = hmac.new(WEBHOOK_SECRET.encode("utf-8"), corpo, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def descrever_erro(exc):
    detalhe = str(exc).strip()
    nome = exc.__class__.__name__
    return (f"{nome}: {detalhe}" if detalhe else nome)[:300]


def enviar_webhook(url, payload):
    # Corpo serializado uma vez: a assinatura vale para todas as tentativas.
    corpo = json.dumps(payload).encode("utf-8")
    cabecalhos = {"Content-Type": "application/json", "X-Webhook-Signature": assinar(corpo)}
    rotulo = f"{payload['id_cobranca']} {payload['status']}"
    total = len(ESPERAS_WEBHOOK) + 1
    resultado = {"entregue": False, "http_status": None, "tentativas": 0, "erro": None, "resposta": None}
    with httpx.Client(timeout=TIMEOUT_WEBHOOK, transport=transporte_webhook, trust_env=False) as cliente:
        for tentativa in range(1, total + 1):
            resultado["tentativas"] = tentativa
            try:
                resposta = cliente.post(url, content=corpo, headers=cabecalhos)
            except httpx.TransportError as exc:
                resultado.update(http_status=None, resposta=None, erro=descrever_erro(exc))
                log(f"webhook {rotulo} -> {exc.__class__.__name__} (tentativa {tentativa}/{total})")
                repetir = True
            except Exception as exc:  # noqa: BLE001 - repetir nao adianta
                resultado.update(http_status=None, resposta=None, erro=descrever_erro(exc))
                log(f"webhook {rotulo} -> {exc.__class__.__name__}")
                break
            else:
                entregue = resposta.is_success
                resultado.update(
                    entregue=entregue,
                    http_status=resposta.status_code,
                    resposta=resposta.text[:LIMITE_RESPOSTA] or None,
                    erro=None if entregue else f"HTTP {resposta.status_code}",
                )
                sufixo = "" if entregue else f" (tentativa {tentativa}/{total})"
                log(f"webhook {rotulo} -> HTTP {resposta.status_code}{sufixo}")
                repetir = resposta.status_code >= 500
            if not repetir or tentativa == total:
                break
            time.sleep(ESPERAS_WEBHOOK[tentativa - 1])
    return resultado


def preparar_envio(cobranca):
    # Chamada com o lock preso.
    em_envio.add(cobranca["id_cobranca"])
    payload = {
        "id_cobranca": cobranca["id_cobranca"],
        "id_pedido": cobranca["id_pedido"],
        "status": cobranca["status"],
        "valor": cobranca["valor"],
        "data_hora": cobranca["atualizado_em"],
    }
    return cobranca["webhook_url"], payload


def entregar_webhook(id_cobranca, url, payload):
    # Chamada sem o lock: a requisicao HTTP pode demorar.
    resultado = {
        "entregue": False,
        "http_status": None,
        "tentativas": 0,
        "erro": "Erro inesperado ao enviar a notificação",
        "resposta": None,
    }
    try:
        resultado = enviar_webhook(url, payload)
    finally:
        with lock:
            cobranca = cobrancas.get(id_cobranca)
            if cobranca is not None:
                cobranca["webhook"] = resultado
            em_envio.discard(id_cobranca)
    if not resultado["entregue"]:
        log(f"webhook {id_cobranca} nao entregue ({resultado['tentativas']} tentativa(s))")
    return resultado


def pode_reenviar(cobranca):
    # Chamada com o lock preso.
    webhook = cobranca["webhook"]
    return (
        cobranca["status"] in DECISOES
        and cobranca["id_cobranca"] not in em_envio
        and not (webhook and webhook["entregue"])
    )


def registrar_decisao(id_cobranca, decisao):
    envio = None
    with lock:
        cobranca = cobrancas.get(id_cobranca)
        existe = cobranca is not None
        if existe and decisao is not None and cobranca["status"] == PENDENTE:
            cobranca["status"] = decisao
            cobranca["atualizado_em"] = agora()
            envio = preparar_envio(cobranca)
    if not existe:
        return html(mock_paginas.pagina_nao_encontrada(id_cobranca), 404)
    if decisao is None:
        return html(mock_paginas.pagina_decisao_invalida(id_cobranca), 400)
    if envio is not None:
        log(f"cobranca {id_cobranca} {decisao} pelo cliente")
        entregar_webhook(id_cobranca, *envio)
    return RedirectResponse(f"/checkout/{id_cobranca}", status_code=303)


class ClienteCobranca(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str | None = None
    nome: str | None = None


class NovaCobranca(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id_pedido: str = Field(min_length=1, max_length=200)
    valor: float = Field(gt=0, allow_inf_nan=False)
    descricao: str | None = None
    cliente: ClienteCobranca | str | None = None
    webhook_url: str = Field(min_length=1, max_length=2000)

    @field_validator("valor")
    @classmethod
    def _arredondar_valor(cls, valor):
        valor = round(valor, 2)
        if valor <= 0:
            raise ValueError("o valor deve ser maior que zero")
        return valor

    @field_validator("webhook_url")
    @classmethod
    def _validar_webhook_url(cls, url):
        if not url_http_valida(url):
            raise ValueError("a webhook_url deve ser uma URL http:// ou https://")
        return url


TAG_API = "Cobranças (API REST)"
TAG_CHECKOUT = "Checkout (navegador)"
FORMULARIO_DECISAO = {
    "requestBody": {
        "required": True,
        "content": {
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "properties": {"decisao": {"type": "string", "enum": list(DECISOES)}},
                    "required": ["decisao"],
                }
            }
        },
    }
}

app = FastAPI(
    title="Mock de Pagamento (PayMock)",
    description=(
        "Sistema externo que simula um provedor de pagamentos. A loja (MS Pagamento) cria "
        "cobranças pela API REST; o cliente aprova ou recusa o pagamento na página de "
        "checkout e o PayMock avisa a loja por Webhook assinado (HMAC-SHA256)."
    ),
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def validacao_invalida(request: Request, exc: RequestValidationError):
    try:
        return await request_validation_exception_handler(request, exc)
    except ValueError:
        # NaN/Infinity no corpo quebram o 422 padrao (nao cabem em JSON).
        erros = [{k: v for k, v in erro.items() if k not in ("input", "ctx")} for erro in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(erros)})


# PDF (0,1): processa via REST a solicitacao de cobranca do MS Pagamento.
@app.post("/api/cobrancas", status_code=201, tags=[TAG_API], summary="Criar cobrança")
def criar_cobranca(dados: NovaCobranca):
    cliente = dados.cliente.model_dump() if isinstance(dados.cliente, ClienteCobranca) else dados.cliente
    criado_em = agora()
    with lock:
        id_cobranca = novo_id()
        cobrancas[id_cobranca] = {
            "id_cobranca": id_cobranca,
            "id_pedido": dados.id_pedido,
            "valor": dados.valor,
            "descricao": dados.descricao,
            "cliente": cliente,
            "webhook_url": dados.webhook_url,
            "status": PENDENTE,
            "criado_em": criado_em,
            "atualizado_em": criado_em,
            "webhook": None,
        }
    log(f"cobranca {id_cobranca} criada para pedido {dados.id_pedido} (R$ {dados.valor:.2f})")
    return {"id_cobranca": id_cobranca, "status": PENDENTE, "url_checkout": url_checkout(id_cobranca)}


@app.get("/api/cobrancas/{id_cobranca}", tags=[TAG_API], summary="Consultar cobrança")
def consultar_cobranca(id_cobranca: str):
    cobranca, _ = obter(id_cobranca)
    if cobranca is None:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada.")
    return resposta_api(cobranca)


@app.post(
    "/api/cobrancas/{id_cobranca}/cancelar",
    tags=[TAG_API],
    summary="Cancelar cobrança pendente",
)
def cancelar_cobranca(id_cobranca: str):
    id_cobranca = normalizar_id(id_cobranca)
    with lock:
        cobranca = cobrancas.get(id_cobranca)
        if cobranca is None:
            raise HTTPException(status_code=404, detail="Cobrança não encontrada.")
        if cobranca["status"] in DECISOES:
            raise HTTPException(
                status_code=409,
                detail=f"A cobrança já foi finalizada ({cobranca['status']}) e não pode ser cancelada.",
            )
        cancelou = cobranca["status"] == PENDENTE
        if cancelou:
            cobranca["status"] = CANCELADO
            cobranca["atualizado_em"] = agora()
        copia = copy.deepcopy(cobranca)
    if cancelou:
        log(f"cobranca {id_cobranca} cancelada pela loja (pedido {copia['id_pedido']})")
    return resposta_api(copia)


@app.get("/saude", tags=["Saúde"], summary="Verificar se o PayMock está no ar")
def saude():
    return {"servico": "mock_pagamento", "status": "ok"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def inicio():
    return html(mock_paginas.pagina_inicial())


# PDF (0,1): pagina no navegador com os botoes "Pagamento Aprovado" e "Pagamento Recusado".
@app.get(
    "/checkout/{id_cobranca}",
    response_class=HTMLResponse,
    tags=[TAG_CHECKOUT],
    summary="Página de checkout da cobrança",
)
def checkout(id_cobranca: str):
    cobranca, enviando = obter(id_cobranca)
    if cobranca is None:
        return html(mock_paginas.pagina_nao_encontrada(normalizar_id(id_cobranca)), 404)
    return html(mock_paginas.pagina_checkout(cobranca, enviando))


# PDF (0,1): clique em "Pagamento Aprovado/Recusado" -> POST (Webhook) para a URL do MS Pagamento.
@app.post(
    "/checkout/{id_cobranca}/decisao",
    response_class=HTMLResponse,
    tags=[TAG_CHECKOUT],
    summary="Registrar a decisão do cliente e notificar a loja (Webhook)",
    openapi_extra=FORMULARIO_DECISAO,
)
async def decidir_pagamento(id_cobranca: str, request: Request):
    # Formulario lido "na mao" (python-multipart nao e dependencia do projeto).
    decisao = ler_decisao(await request.body())
    return await run_in_threadpool(registrar_decisao, normalizar_id(id_cobranca), decisao)


@app.post(
    "/checkout/{id_cobranca}/reenviar",
    response_class=HTMLResponse,
    tags=[TAG_CHECKOUT],
    summary="Reenviar o Webhook que não foi entregue",
)
def reenviar_notificacao(id_cobranca: str):
    id_cobranca = normalizar_id(id_cobranca)
    envio = None
    with lock:
        cobranca = cobrancas.get(id_cobranca)
        existe = cobranca is not None
        if existe and pode_reenviar(cobranca):
            envio = preparar_envio(cobranca)
    if not existe:
        return html(mock_paginas.pagina_nao_encontrada(id_cobranca), 404)
    if envio is not None:
        log(f"reenviando webhook {id_cobranca}")
        entregar_webhook(id_cobranca, *envio)
    return RedirectResponse(f"/checkout/{id_cobranca}", status_code=303)


def desativar_edicao_rapida():
    # Mesma logica de common/console.py (Edicao Rapida do console do Windows).
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        entrada = ctypes.c_void_p(kernel32.GetStdHandle(-10))
        modo = ctypes.c_uint32()
        if kernel32.GetConsoleMode(entrada, ctypes.byref(modo)):
            kernel32.SetConsoleMode(entrada, (modo.value & ~0x0040) | 0x0080)
    except Exception:  # noqa: BLE001 - ajuste opcional
        pass


def main():
    desativar_edicao_rapida()
    log(f"PayMock iniciando em http://{HTTP_HOST}:{PORTA_MOCK} (checkout em {URL_PUBLICA}/checkout/...)")
    uvicorn.run(app, host=HTTP_HOST, port=PORTA_MOCK)


if __name__ == "__main__":
    main()
