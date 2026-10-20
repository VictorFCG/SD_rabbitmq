# Microsservico Pagamento: cobranca dos pedidos no Mock de Pagamento (externo).
# Exchange eCommerce (tipo direct). Fila propria: fila.pagamento.
# Consome: pedido.estoque_ok (cria a cobranca) e pedido.excluido (cancela).
# Publica: pagamento.pendente, pagamento.aprovado e pagamento.recusado.
# O Mock informa o resultado pelo Webhook POST /webhooks/pagamento (HMAC-SHA256).
# Cobrancas gravadas em SQLite (data/pagamentos.db) e recarregadas ao iniciar.
import hashlib
import hmac
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import config
from common.console import desativar_edicao_rapida
from common.mq import EXCHANGE_ECOMMERCE, Consumer, Publisher
from pagamentos_db import PagamentosDB

SERVICE = "pagamento"
KEYS = Path(__file__).resolve().parent / "keys"
DB_PATH = Path(__file__).resolve().parent / "data" / "pagamentos.db"

PENDENTE = "PENDENTE"
APROVADO = "APROVADO"
RECUSADO = "RECUSADO"
CANCELADO = "CANCELADO"

TENTATIVAS_MOCK = 3
TIMEOUT_MOCK = 5.0
ERROS_HTTP = (httpx.HTTPError, httpx.InvalidURL)
MOTIVO_RECUSADO = "Pagamento recusado pelo cliente no provedor de pagamento"
MOTIVO_FALHA = "Falha ao criar a cobrança no provedor de pagamento"

cobrancas = {}
cobranca_do_pedido = {}
pedidos_excluidos = set()
em_publicacao = set()  # id_cobranca cujo resultado do Webhook esta sendo publicado
lock = threading.Lock()
db = None
publisher = None
transporte_http = None  # os testes trocam por um httpx.MockTransport

app = FastAPI(
    title="MS Pagamento",
    description=(
        "Cria as cobranças dos pedidos no Mock de Pagamento e recebe, via Webhook, "
        "o resultado escolhido pelo cliente (APROVADO ou RECUSADO)."
    ),
)


class NotificacaoPagamento(BaseModel):
    id_cobranca: str = Field(min_length=1)
    id_pedido: str = Field(min_length=1)
    status: Literal["APROVADO", "RECUSADO"]
    valor: float | None = None
    data_hora: str | None = None


def agora():
    return datetime.now(timezone.utc).isoformat()


def publicar(routing_key, dados):
    publisher.publish(EXCHANGE_ECOMMERCE, routing_key, dados)


def cliente_http():
    # trust_env=False: no Windows/macOS o httpx usaria o proxy do sistema ate para 127.0.0.1.
    return httpx.Client(timeout=TIMEOUT_MOCK, transport=transporte_http, trust_env=False)


def url_mock(caminho):
    return config.URL_MOCK_PAGAMENTO.rstrip("/") + caminho


# Falha no SQLite so vai para o log: com o processo no ar vale o estado em memoria.
def gravar_cobranca(cobranca):
    if db is None:
        return
    try:
        db.salvar_cobranca(cobranca)
    except sqlite3.Error as exc:
        print(
            f"[pagamento] falha ao gravar a cobranca {cobranca['id_cobranca']} no banco "
            f"({exc.__class__.__name__}: {exc})"
        )


def gravar_exclusao(id_pedido):
    if db is None:
        return
    try:
        db.marcar_excluido(id_pedido)
    except sqlite3.Error as exc:
        print(
            f"[pagamento] falha ao gravar a exclusao do pedido {id_pedido} no banco "
            f"({exc.__class__.__name__}: {exc})"
        )


def restaurar_cobrancas():
    salvas, excluidos = db.carregar()
    with lock:
        cobrancas.clear()
        cobrancas.update(salvas)
        cobranca_do_pedido.clear()
        cobranca_do_pedido.update({cobranca["id_pedido"]: id_cobranca for id_cobranca, cobranca in salvas.items()})
        pedidos_excluidos.clear()
        pedidos_excluidos.update(excluidos)
    return len(salvas)


# PDF (0,1): pede ao Mock a cobranca (URL de checkout), informando a URL de Webhook.
def corpo_da_cobranca(pedido, valor):
    id_pedido = pedido["id_pedido"]
    quantidade = sum(item.get("quantidade", 0) for item in pedido.get("itens") or [])
    corpo = {
        "id_pedido": id_pedido,
        "valor": valor,
        "descricao": f"Pedido {id_pedido} - {quantidade} item(ns)",
        "webhook_url": config.URL_WEBHOOK_PAGAMENTO,
    }
    email = (pedido.get("cliente") or {}).get("email")
    if email:
        corpo["cliente"] = email
    return corpo


def ler_cobranca_criada(resposta, id_pedido):
    try:
        dados = resposta.json()
    except ValueError:
        dados = None
    if not isinstance(dados, dict) or not dados.get("id_cobranca") or not dados.get("url_checkout"):
        print(f"[pagamento] resposta invalida do Mock ao criar a cobranca do pedido {id_pedido}")
        return None
    return {"id_cobranca": str(dados["id_cobranca"]), "url_checkout": str(dados["url_checkout"])}


def solicitar_cobranca(pedido, valor):
    # Repete em falha de rede ou 5xx; um 4xx e definitivo.
    id_pedido = pedido["id_pedido"]
    corpo = corpo_da_cobranca(pedido, valor)
    for tentativa in range(1, TENTATIVAS_MOCK + 1):
        try:
            with cliente_http() as cliente:
                resposta = cliente.post(url_mock("/api/cobrancas"), json=corpo)
        except ERROS_HTTP as exc:
            falha = exc.__class__.__name__
        else:
            if resposta.is_success:
                return ler_cobranca_criada(resposta, id_pedido)
            if resposta.status_code < 500:
                print(
                    f"[pagamento] Mock rejeitou a cobranca do pedido {id_pedido} "
                    f"(HTTP {resposta.status_code})"
                )
                return None
            falha = f"HTTP {resposta.status_code}"
        print(
            f"[pagamento] falha ao criar a cobranca do pedido {id_pedido} ({falha}), "
            f"tentativa {tentativa}/{TENTATIVAS_MOCK}"
        )
        if tentativa < TENTATIVAS_MOCK:
            time.sleep(tentativa)
    return None


def cancelar_no_mock(id_cobranca):
    try:
        with cliente_http() as cliente:
            resposta = cliente.post(url_mock(f"/api/cobrancas/{id_cobranca}/cancelar"))
    except ERROS_HTTP as exc:
        print(f"[pagamento] falha ao cancelar a cobranca {id_cobranca} no Mock ({exc.__class__.__name__})")
        return
    if resposta.is_success:
        print(f"[pagamento] cobranca {id_cobranca} cancelada no Mock")
    else:
        print(f"[pagamento] Mock nao cancelou a cobranca {id_cobranca} (HTTP {resposta.status_code})")


def dados_pendente(cobranca):
    return {
        "id_pedido": cobranca["id_pedido"],
        "id_cobranca": cobranca["id_cobranca"],
        "url_pagamento": cobranca["url_pagamento"],
        "valor": cobranca["valor"],
    }


def repetir_pendente(cobranca):
    id_pedido, id_cobranca, status = cobranca["id_pedido"], cobranca["id_cobranca"], cobranca["status"]
    if status != PENDENTE:
        print(f"[pagamento] pedido {id_pedido} ja tem a cobranca {id_cobranca} ({status}); evento repetido ignorado")
        return
    print(f"[pagamento] pedido {id_pedido} ja tem a cobranca {id_cobranca}; republicando pagamento.pendente")
    publicar("pagamento.pendente", dados_pendente(cobranca))


def recusar_sem_cobranca(id_pedido):
    with lock:
        excluido = id_pedido in pedidos_excluidos
    if excluido:
        print(f"[pagamento] pedido {id_pedido} ja foi excluido; falha na cobranca ignorada")
        return
    print(f"[pagamento] nao foi possivel criar a cobranca do pedido {id_pedido}; publicando pagamento.recusado")
    publicar("pagamento.recusado", {"id_pedido": id_pedido, "id_cobranca": None, "motivo": MOTIVO_FALHA})


def criar_cobranca(pedido):
    id_pedido = pedido["id_pedido"]
    valor = round(float(pedido["valor_total"]), 2)
    print(f"[pagamento] pedido {id_pedido} com estoque confirmado; criando cobranca de R$ {valor:.2f}")
    criada = solicitar_cobranca(pedido, valor)
    if criada is None:
        recusar_sem_cobranca(id_pedido)
        return

    momento = agora()
    cobranca = {
        "id_cobranca": criada["id_cobranca"],
        "id_pedido": id_pedido,
        "valor": valor,
        "status": PENDENTE,
        "url_pagamento": criada["url_checkout"],
        "criado_em": momento,
        "atualizado_em": momento,
    }
    # O pedido pode ter sido excluido enquanto o Mock era chamado.
    with lock:
        excluido = id_pedido in pedidos_excluidos
        if excluido:
            cobranca["status"] = CANCELADO
        cobrancas[cobranca["id_cobranca"]] = cobranca
        cobranca_do_pedido[id_pedido] = cobranca["id_cobranca"]
        gravar_cobranca(cobranca)
        pendente = dados_pendente(cobranca)
    if excluido:
        print(f"[pagamento] pedido {id_pedido} excluido durante a criacao da cobranca {pendente['id_cobranca']}")
        cancelar_no_mock(pendente["id_cobranca"])
        return
    print(f"[pagamento] cobranca {pendente['id_cobranca']} criada para o pedido {id_pedido}; aguardando o cliente")
    publicar("pagamento.pendente", pendente)


def iniciar_pagamento(pedido):
    id_pedido = pedido["id_pedido"]
    with lock:
        excluido = id_pedido in pedidos_excluidos
        existente = cobrancas.get(cobranca_do_pedido.get(id_pedido))
        existente = dict(existente) if existente else None
    if excluido:
        print(f"[pagamento] pedido {id_pedido} ja foi excluido; cobranca nao criada")
    elif existente is not None:
        repetir_pendente(existente)
    else:
        criar_cobranca(pedido)


def cancelar_pagamento(dados):
    id_pedido = dados["id_pedido"]
    prefixo = f"[pagamento] pedido {id_pedido} excluido ({dados.get('motivo') or 'sem motivo'})"
    with lock:
        pedidos_excluidos.add(id_pedido)
        gravar_exclusao(id_pedido)
        cobranca = cobrancas.get(cobranca_do_pedido.get(id_pedido))
        status = cobranca["status"] if cobranca else None
        if status == PENDENTE:
            cobranca["status"] = CANCELADO
            cobranca["atualizado_em"] = agora()
            gravar_cobranca(cobranca)
    if cobranca is None:
        print(f"{prefixo}; nenhuma cobranca a cancelar")
    elif status != PENDENTE:
        print(f"{prefixo}; cobranca {cobranca['id_cobranca']} ja estava {status}")
    else:
        print(f"{prefixo}; cancelando a cobranca {cobranca['id_cobranca']}")
        cancelar_no_mock(cobranca["id_cobranca"])


def handle(routing_key, data):
    if routing_key == "pedido.estoque_ok":
        iniciar_pagamento(data)
    elif routing_key == "pedido.excluido":
        cancelar_pagamento(data)


def assinatura_valida(corpo, assinatura):
    if not assinatura:
        return False
    esperada = "sha256=" + hmac.new(config.WEBHOOK_SECRET.encode("utf-8"), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(assinatura.strip().lower().encode("utf-8"), esperada.encode("ascii"))


def ler_notificacao(corpo):
    try:
        return NotificacaoPagamento.model_validate_json(corpo)
    except ValidationError as exc:
        print("[pagamento] webhook com corpo invalido rejeitado")
        erros = exc.errors(include_url=False, include_input=False)
        raise RequestValidationError([{**erro, "loc": ("body", *erro["loc"])} for erro in erros]) from None


def cobranca_do_webhook(notificacao):
    # Chamada com o lock adquirido.
    cobranca = cobrancas.get(notificacao.id_cobranca)
    if cobranca is None:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada.")
    if cobranca["id_pedido"] != notificacao.id_pedido:
        raise HTTPException(status_code=422, detail="O pedido informado não corresponde ao da cobrança.")
    if cobranca["status"] == PENDENTE and cobranca["id_pedido"] in pedidos_excluidos:
        cobranca["status"] = CANCELADO
        cobranca["atualizado_em"] = agora()
        gravar_cobranca(cobranca)
    if cobranca["status"] == CANCELADO:
        raise HTTPException(status_code=409, detail="Cobrança cancelada: o pedido foi cancelado.")
    if cobranca["status"] not in (PENDENTE, notificacao.status):
        raise HTTPException(
            status_code=409, detail=f"Cobrança já finalizada com o status {cobranca['status']}."
        )
    return cobranca


def publicar_resultado(cobranca):
    if cobranca["status"] == APROVADO:
        publicar(
            "pagamento.aprovado",
            {"id_pedido": cobranca["id_pedido"], "id_cobranca": cobranca["id_cobranca"], "valor": cobranca["valor"]},
        )
    else:
        publicar(
            "pagamento.recusado",
            {"id_pedido": cobranca["id_pedido"], "id_cobranca": cobranca["id_cobranca"], "motivo": MOTIVO_RECUSADO},
        )


def desfazer_status(cobranca, status):
    # Chamada com o lock adquirido; volta a PENDENTE para o Mock reenviar o Webhook.
    if cobranca["status"] == status:
        excluido = cobranca["id_pedido"] in pedidos_excluidos
        cobranca["status"] = CANCELADO if excluido else PENDENTE
        cobranca["atualizado_em"] = agora()
        gravar_cobranca(cobranca)


def encerrar_publicacao(cobranca, status, publicado):
    # Grava so depois de publicar: se o processo cair antes, a cobranca segue PENDENTE.
    with lock:
        em_publicacao.discard(cobranca["id_cobranca"])
        if publicado:
            gravar_cobranca(cobranca)
        else:
            desfazer_status(cobranca, status)


def processar_webhook(notificacao):
    # Webhook repetido durante a publicacao recebe 503: se ela falhar, o Mock reenvia.
    status = notificacao.status
    with lock:
        cobranca = cobranca_do_webhook(notificacao)
        repetido = cobranca["status"] == status
        em_andamento = repetido and cobranca["id_cobranca"] in em_publicacao
        if not repetido:
            cobranca["status"] = status
            cobranca["atualizado_em"] = agora()
            em_publicacao.add(cobranca["id_cobranca"])
        dados = dict(cobranca)
    if em_andamento:
        print(
            f"[pagamento] webhook repetido da cobranca {dados['id_cobranca']} ({status}) "
            "durante a publicacao do resultado"
        )
        raise HTTPException(status_code=503, detail="Pagamento em processamento. Tente novamente.")
    if repetido:
        print(f"[pagamento] webhook repetido da cobranca {dados['id_cobranca']} ({status}); ja processado")
        return {"resultado": "ja_processado", "status": status}
    publicado = False
    try:
        publicar_resultado(dados)
        publicado = True
    except Exception as exc:  # noqa: BLE001 - qualquer falha: o Mock deve reenviar
        print(
            f"[pagamento] falha ao publicar o resultado da cobranca {dados['id_cobranca']} "
            f"({exc.__class__.__name__}); status desfeito"
        )
        raise HTTPException(
            status_code=503, detail="Não foi possível registrar o pagamento agora. Tente novamente."
        ) from None
    finally:
        encerrar_publicacao(cobranca, status, publicado)
    print(f"[pagamento] pedido {dados['id_pedido']} - pagamento {status} (cobranca {dados['id_cobranca']})")
    return {"resultado": "processado", "status": status}


# PDF (0,1): endpoint que recebe o Webhook do Mock com o status APROVADO ou RECUSADO.
@app.post(
    "/webhooks/pagamento",
    summary="Recebe o Webhook do Mock de Pagamento",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": NotificacaoPagamento.model_json_schema()}},
        }
    },
    responses={
        401: {"description": "Assinatura X-Webhook-Signature ausente ou inválida"},
        404: {"description": "Cobrança desconhecida"},
        409: {"description": "Cobrança cancelada ou já finalizada com outro status"},
        503: {
            "description": "RabbitMQ indisponível ou pagamento ainda em processamento; o Webhook deve ser reenviado"
        },
    },
)
async def receber_webhook(
    request: Request,
    x_webhook_signature: Annotated[
        str | None, Header(description="sha256=<HMAC-SHA256 do corpo, em hexadecimal>")
    ] = None,
):
    corpo = await request.body()
    if not assinatura_valida(corpo, x_webhook_signature):
        print("[pagamento] webhook com assinatura ausente ou invalida rejeitado")
        raise HTTPException(status_code=401, detail="Assinatura do Webhook ausente ou inválida.")
    notificacao = ler_notificacao(corpo)
    try:
        return await run_in_threadpool(processar_webhook, notificacao)
    except HTTPException as exc:
        print(f"[pagamento] webhook da cobranca {notificacao.id_cobranca} rejeitado (HTTP {exc.status_code})")
        raise


@app.get("/pagamentos/{id_pedido}", summary="Consulta a cobrança de um pedido")
def consultar_pagamento(id_pedido: str):
    with lock:
        cobranca = cobrancas.get(cobranca_do_pedido.get(id_pedido))
        dados = dict(cobranca) if cobranca else None
    if dados is None:
        raise HTTPException(status_code=404, detail="Nenhuma cobrança encontrada para este pedido.")
    return dados


@app.get("/saude", summary="Verifica se o serviço está no ar")
def saude():
    return {"servico": "pagamento", "status": "ok"}


def main():
    global db, publisher
    desativar_edicao_rapida()
    db = PagamentosDB(DB_PATH)
    print(f"[pagamento] {restaurar_cobrancas()} cobranca(s) restaurada(s) de data/pagamentos.db")
    publisher = Publisher(SERVICE, KEYS)
    Consumer(
        SERVICE,
        KEYS,
        "fila.pagamento",
        [(EXCHANGE_ECOMMERCE, "pedido.estoque_ok"), (EXCHANGE_ECOMMERCE, "pedido.excluido")],
        handle,
    ).start_in_thread()
    print(
        f"[pagamento] iniciado: Webhook em {config.URL_WEBHOOK_PAGAMENTO}, "
        f"Mock de Pagamento em {config.URL_MOCK_PAGAMENTO}"
    )
    try:
        uvicorn.run(app, host=config.HTTP_HOST, port=config.PORTA_PAGAMENTO)
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
