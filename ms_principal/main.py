# Microsservico Principal (API Gateway): API REST do frontend e notificacoes
# em tempo real via SSE (/api/eventos). Exchanges eCommerce e Promocoes.
# Publica: pedido.criado, pedido.excluido e interesse.promocao.
# Consome: pedido.estoque_ok, estoque.indisponivel, pagamento.pendente,
#   pagamento.aprovado, pagamento.recusado, pedido.enviado e promocao.categoria.*
# Fila propria: fila.principal.
import asyncio
import re
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pika.exceptions import AMQPError
from pydantic import AfterValidator, BaseModel, Field, field_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import config
from common.catalogo import CATEGORIAS
from common.console import desativar_edicao_rapida
from common.mq import EXCHANGE_ECOMMERCE, EXCHANGE_PROMOCOES, Consumer, Publisher
from notificacoes import HubNotificacoes
from pedidos import (
    AGUARDANDO_PAGAMENTO,
    CANCELADO,
    CANCELAVEIS,
    ENVIADO,
    ESTOQUE_CONFIRMADO,
    ESTOQUE_INDISPONIVEL,
    PAGAMENTO_APROVADO,
    PAGAMENTO_RECUSADO,
    PedidosDB,
    agora,
)

SERVICE = "principal"
KEYS = Path(__file__).resolve().parent / "keys"
DADOS = Path(__file__).resolve().parent / "data"

FILA = "fila.principal"
# PDF (Gateway): consome os eventos de Estoque, Pagamento e Entrega.
BINDINGS = [
    (EXCHANGE_ECOMMERCE, "pedido.estoque_ok"),
    (EXCHANGE_ECOMMERCE, "estoque.indisponivel"),
    (EXCHANGE_ECOMMERCE, "pagamento.pendente"),
    (EXCHANGE_ECOMMERCE, "pagamento.aprovado"),
    (EXCHANGE_ECOMMERCE, "pagamento.recusado"),
    (EXCHANGE_ECOMMERCE, "pedido.enviado"),
    (EXCHANGE_PROMOCOES, "promocao.categoria.*"),
]

TIMEOUT_HTTP = httpx.Timeout(5.0, connect=2.0)
VALIDADE_PROMOCAO = timedelta(minutes=5)
PADRAO_EMAIL = re.compile(r"^[^@\s]+@[^@\s/]+\.[^@\s/]+$")
FALHA_MENSAGERIA = "serviço de mensageria indisponível. Tente novamente em instantes."

publisher = None
db = None
hub = HubNotificacoes()
transporte_http = None  # os testes trocam por httpx.MockTransport
# Mantem as notificacoes SSE na mesma ordem das mudancas de status.
trava_pedidos = threading.Lock()


def log(mensagem):
    print(f"[principal] {mensagem}".encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def validar_email(valor):
    email = valor.strip().lower()
    if len(email) > 254 or not PADRAO_EMAIL.match(email):
        raise ValueError("E-mail inválido.")
    return email


def validar_categorias(valores):
    if valores is None:
        return None
    categorias = []
    for valor in valores:
        codigo = valor.strip().upper()
        if codigo not in CATEGORIAS:
            raise ValueError(f"Categoria inválida: '{valor}'. Use: {', '.join(CATEGORIAS)}.")
        if codigo not in categorias:
            categorias.append(codigo)
    return categorias


def limpar_nome(valor):
    if valor is None:
        return None
    return valor.strip() or None


Email = Annotated[str, AfterValidator(validar_email)]
Nome = Annotated[str | None, Field(max_length=100), AfterValidator(limpar_nome)]


class ItemPedido(BaseModel):
    codigo: Annotated[str, Field(min_length=1, max_length=20)]
    quantidade: Annotated[int, Field(ge=1, le=99)]

    @field_validator("codigo")
    @classmethod
    def normalizar_codigo(cls, valor):
        codigo = valor.strip().upper()
        if not codigo:
            raise ValueError("Informe o código do produto.")
        return codigo


class NovoPedido(BaseModel):
    email: Email
    nome: Nome = None
    itens: Annotated[list[ItemPedido], Field(min_length=1, max_length=20)]

    @field_validator("itens")
    @classmethod
    def juntar_repetidos(cls, itens):
        quantidades = {}
        for item in itens:
            quantidades[item.codigo] = quantidades.get(item.codigo, 0) + item.quantidade
        for codigo, quantidade in quantidades.items():
            if quantidade > 99:
                raise ValueError(f"A quantidade máxima por produto é 99 (produto {codigo}).")
        return [ItemPedido(codigo=codigo, quantidade=qtd) for codigo, qtd in quantidades.items()]


class NovoInteresse(BaseModel):
    email: Email
    nome: Nome = None
    categorias: Annotated[list[str], Field(min_length=1, max_length=20), AfterValidator(validar_categorias)]


MENSAGENS_VALIDACAO = {
    "missing": "Campo obrigatório.",
    "string_type": "Deve ser um texto.",
    "int_type": "Deve ser um número inteiro.",
    "int_parsing": "Deve ser um número inteiro.",
    "int_from_float": "Deve ser um número inteiro.",
    "bool_type": "Deve ser verdadeiro ou falso.",
    "bool_parsing": "Deve ser verdadeiro ou falso.",
    "list_type": "Deve ser uma lista.",
    "dict_type": "Deve ser um objeto JSON.",
    "model_type": "Deve ser um objeto JSON.",
    "model_attributes_type": "Deve ser um objeto JSON.",
    "json_invalid": "JSON inválido.",
    "greater_than_equal": "Deve ser maior ou igual a {ge}.",
    "less_than_equal": "Deve ser menor ou igual a {le}.",
    "too_short": "Deve ter pelo menos {min_length} item(ns).",
    "too_long": "Deve ter no máximo {max_length} itens.",
    "string_too_short": "Deve ter pelo menos {min_length} caractere(s).",
    "string_too_long": "Deve ter no máximo {max_length} caracteres.",
    "string_pattern_mismatch": "Formato inválido.",
}


def traduzir_erro(erro):
    tipo = erro.get("type", "")
    mensagem = str(erro.get("msg", ""))
    if tipo == "value_error":
        return mensagem.removeprefix("Value error, ")
    modelo = MENSAGENS_VALIDACAO.get(tipo)
    if modelo is None:
        return mensagem
    try:
        return modelo.format(**(erro.get("ctx") or {}))
    except (KeyError, IndexError, ValueError):
        return mensagem


@asynccontextmanager
async def ciclo_de_vida(_app):
    global publisher, db
    db = PedidosDB(DADOS / "principal.db")
    publisher = Publisher(SERVICE, KEYS)
    hub.configurar(asyncio.get_running_loop())
    Consumer(SERVICE, KEYS, FILA, BINDINGS, tratar_evento).start_in_thread()
    log(f"API Gateway em http://{config.HTTP_HOST}:{config.PORTA_PRINCIPAL} (Swagger em /docs)")
    yield
    hub.configurar(None)
    publisher.close()
    log("encerrado")


app = FastAPI(
    title="MS Principal - API Gateway",
    version="2.0.0",
    description=(
        "Ponto de entrada do frontend do e-commerce: catálogo (via MS Estoque), pedidos, "
        "interesse em promoções e notificações em tempo real via SSE (/api/eventos). "
        "As ações viram eventos assinados digitalmente no RabbitMQ."
    ),
    lifespan=ciclo_de_vida,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ORIGENS_FRONTEND,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def erro_de_validacao(_request, exc):
    detalhes = [
        {"type": erro.get("type"), "loc": list(erro.get("loc", ())), "msg": traduzir_erro(erro)}
        for erro in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detalhes})


def consultar(url, servico, params=None):
    # trust_env=False: no Windows o httpx usaria o proxy do sistema ate para 127.0.0.1.
    try:
        with httpx.Client(timeout=TIMEOUT_HTTP, transport=transporte_http, trust_env=False) as cliente:
            resposta = cliente.get(url, params=params)
            resposta.raise_for_status()
            return resposta.json()
    except (httpx.HTTPError, ValueError) as exc:
        log(f"falha ao consultar {url}: {exc!r}")
        raise HTTPException(status_code=503, detail=f"{servico} indisponível") from exc


# PDF (0,1): publica pedido.criado, pedido.excluido e interesse.promocao.
def publicar(exchange, routing_key, dados, falha):
    try:
        publisher.publish(exchange, routing_key, dados)
    except (AMQPError, OSError) as exc:
        log(f"falha ao publicar {routing_key}: {exc!r}")
        raise HTTPException(status_code=503, detail=f"{falha}: {FALHA_MENSAGERIA}") from exc


# PDF (0,5): notificacao SSE de cada mudanca de status ao dono do pedido.
def notificar(pedido, tipo, mensagem):
    hub.enviar(
        pedido["email"],
        "pedido",
        {"tipo": tipo, "mensagem": mensagem, "pedido": pedido, "timestamp": agora()},
    )


def ultimo_id_recebido(*valores):
    ids = []
    for valor in valores:
        if valor is None:
            continue
        try:
            ids.append(int(str(valor).strip()))
        except ValueError:
            continue
    return max(ids) if ids else None


def evento_sse(item):
    return ServerSentEvent(event=item["evento"], id=str(item["id"]), data=item["dados"])


# PDF (0,1): lista os produtos disponiveis, consultando o MS Estoque via REST.
@app.get("/api/produtos", tags=["Produtos"], summary="Lista os produtos (consulta o MS Estoque)")
def listar_produtos(incluir_esgotados: bool = False):
    return consultar(
        f"{config.URL_ESTOQUE.rstrip('/')}/produtos",
        "MS Estoque",
        params={"disponiveis": "false" if incluir_esgotados else "true"},
    )


@app.get("/api/categorias", tags=["Produtos"], summary="Lista as categorias de produtos")
def listar_categorias():
    return [{"codigo": codigo, "nome": nome} for codigo, nome in CATEGORIAS.items()]


# PDF (0,1): cria pedidos.
@app.post("/api/pedidos", status_code=201, tags=["Pedidos"], summary="Cria um pedido")
def criar_pedido(novo: NovoPedido):
    itens = [item.model_dump() for item in novo.itens]
    pedido = db.criar(novo.email, novo.nome, itens)
    id_pedido = pedido["id_pedido"]
    evento = {
        "id_pedido": id_pedido,
        "cliente": {"email": pedido["email"], "nome": pedido["nome"]},
        "itens": itens,
        "criado_em": pedido["criado_em"],
    }
    with trava_pedidos:
        try:
            publicar(EXCHANGE_ECOMMERCE, "pedido.criado", evento, "Não foi possível registrar o pedido")
        except HTTPException:
            db.remover(id_pedido)
            raise
        notificar(pedido, "pedido.criado", f"Pedido {id_pedido} criado. Aguardando confirmação de estoque.")
    log(f"pedido {id_pedido} criado ({pedido['email']}) -> pedido.criado publicado")
    return pedido


@app.get("/api/pedidos", tags=["Pedidos"], summary="Lista os pedidos de um cliente (mais recentes primeiro)")
def listar_pedidos(email: Annotated[Email, Query(max_length=254)]):
    return db.listar_por_email(email)


@app.get("/api/pedidos/{id_pedido}", tags=["Pedidos"], summary="Consulta um pedido")
def obter_pedido(id_pedido: Annotated[str, PathParam(max_length=40)]):
    pedido = db.obter(id_pedido.strip().upper())
    if pedido is None:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")
    return pedido


@app.delete("/api/pedidos/{id_pedido}", tags=["Pedidos"], summary="Cancela um pedido")
def cancelar_pedido(id_pedido: Annotated[str, PathParam(max_length=40)]):
    id_pedido = id_pedido.strip().upper()
    mensagem = f"Pedido {id_pedido} cancelado."
    with trava_pedidos:
        pedido = db.obter(id_pedido)
        if pedido is None:
            raise HTTPException(status_code=404, detail="Pedido não encontrado.")
        if pedido["status"] not in CANCELAVEIS:
            raise HTTPException(
                status_code=409,
                detail=f"O pedido não pode mais ser cancelado (status: {pedido['status_label']}).",
            )
        publicar(
            EXCHANGE_ECOMMERCE,
            "pedido.excluido",
            {"id_pedido": id_pedido, "motivo": "cancelado_pelo_cliente"},
            "Não foi possível cancelar o pedido",
        )
        cancelado, aplicado = db.transicionar(
            id_pedido, CANCELADO, mensagem, motivo="Pedido cancelado pelo cliente."
        )
        if not aplicado:
            raise HTTPException(
                status_code=409, detail="O status do pedido mudou e ele não pode mais ser cancelado."
            )
        notificar(cancelado, "pedido.cancelado", mensagem)
    log(f"pedido {id_pedido} cancelado pelo cliente -> pedido.excluido publicado")
    return cancelado


# PDF (0,1): registra interesse (e-mail) em promocoes de categorias.
@app.post("/api/interesses", status_code=202, tags=["Promoções"], summary="Registra interesse em promoções")
def registrar_interesse(interesse: NovoInteresse):
    publicar(
        EXCHANGE_PROMOCOES,
        "interesse.promocao",
        {
            "acao": "INSCREVER",
            "email": interesse.email,
            "nome": interesse.nome,
            "categorias": interesse.categorias,
        },
        "Não foi possível registrar o interesse",
    )
    nomes = ", ".join(CATEGORIAS[codigo] for codigo in interesse.categorias)
    log(f"interesse.promocao INSCREVER {interesse.email} {interesse.categorias}")
    return {
        "mensagem": f"Interesse registrado! Você receberá por e-mail as promoções de: {nomes}.",
        "email": interesse.email,
        "categorias": interesse.categorias,
    }


# PDF (0,1): cancela o interesse em receber e-mail de promocoes.
# {email:path}: "/" e valido na parte local do e-mail.
@app.delete(
    "/api/interesses/{email:path}",
    status_code=202,
    tags=["Promoções"],
    summary="Cancela o interesse em promoções (categorias informadas ou todas)",
)
def cancelar_interesse(
    email: Annotated[Email, PathParam(max_length=254)],
    categorias: Annotated[list[str] | None, Query(), AfterValidator(validar_categorias)] = None,
):
    categorias = categorias or []
    publicar(
        EXCHANGE_PROMOCOES,
        "interesse.promocao",
        {"acao": "CANCELAR", "email": email, "nome": None, "categorias": categorias},
        "Não foi possível cancelar o interesse",
    )
    if categorias:
        nomes = ", ".join(CATEGORIAS[codigo] for codigo in categorias)
        mensagem = f"Interesse cancelado nas categorias: {nomes}."
    else:
        mensagem = "Interesse cancelado em todas as categorias. Você não receberá mais promoções por e-mail."
    log(f"interesse.promocao CANCELAR {email} {categorias or 'todas'}")
    return {"mensagem": mensagem, "email": email, "categorias": categorias}


@app.get(
    "/api/interesses/{email:path}",
    tags=["Promoções"],
    summary="Consulta as categorias de interesse de um e-mail",
)
def consultar_interesse(email: Annotated[Email, PathParam(max_length=254)]):
    return consultar(
        f"{config.URL_PROMOCOES.rstrip('/')}/interesses/{quote(email, safe='@')}", "MS Promoções"
    )


@app.get("/api/promocoes/recentes", tags=["Promoções"], summary="Lista as promoções mais recentes")
def promocoes_recentes():
    return consultar(f"{config.URL_PROMOCOES.rstrip('/')}/promocoes/recentes", "MS Promoções")


@app.get("/api/saude", tags=["Saúde"], summary="Situação do API Gateway")
def saude():
    return {"servico": "principal", "status": "ok", "conexoes_sse": hub.conexoes()}


# PDF (0,5): conexao SSE com o frontend.
@app.get(
    "/api/eventos",
    response_class=EventSourceResponse,
    tags=["Notificações (SSE)"],
    summary="Stream SSE com o status dos pedidos do cliente e as promoções",
)
async def eventos(
    email: Annotated[Email, Query(max_length=254)],
    last_event_id: Annotated[str | None, Header()] = None,
    ultimo_id: Annotated[str | None, Query(max_length=20)] = None,
):
    ultimo = ultimo_id_recebido(last_event_id, ultimo_id)
    if ultimo is not None and ultimo > hub.ultimo_id():
        ultimo = 0  # id de uma execucao anterior do gateway: reenvia todo o buffer
    fila = hub.inscrever(email)
    log(f"SSE conectado: {email} (ultimo id {ultimo})")
    try:
        if hub.encerrado:
            return
        yield ServerSentEvent(event="conectado", data={"email": email, "timestamp": agora()}, retry=3000)
        enviado = 0
        if ultimo is not None:
            enviado = ultimo
            for item in hub.pendentes(email, ultimo):
                yield evento_sse(item)
                enviado = item["id"]
        while True:
            item = await fila.get()
            if item is None:
                return  # gateway desligando
            if item["id"] <= enviado:
                continue
            yield evento_sse(item)
            enviado = item["id"]
    finally:
        hub.cancelar(email, fila)
        log(f"SSE desconectado: {email}")


def sem_nulos(**campos):
    return {nome: valor for nome, valor in campos.items() if valor is not None}


def valor_monetario(valor):
    try:
        return round(float(valor), 2)
    except (TypeError, ValueError):
        return None


def aplicar(id_pedido, tipo, novo_status, mensagem, **campos):
    with trava_pedidos:
        pedido, aplicado = db.transicionar(id_pedido, novo_status, mensagem, **campos)
        if aplicado:
            notificar(pedido, tipo, mensagem)
    if pedido is None:
        log(f"{tipo} ignorado: pedido {id_pedido} desconhecido")
    elif not aplicado:
        log(f"{tipo} ignorado: pedido {id_pedido} esta {pedido['status']}")
    else:
        log(f"{tipo}: pedido {id_pedido} -> {novo_status}")
    return pedido, aplicado


def excluir_pedido(id_pedido, motivo):
    # Tambem roda na reentrega, caso a publicacao anterior tenha falhado.
    try:
        publisher.publish(
            EXCHANGE_ECOMMERCE, "pedido.excluido", {"id_pedido": id_pedido, "motivo": motivo}
        )
        log(f"pedido {id_pedido} excluido ({motivo}) -> pedido.excluido publicado")
    except (AMQPError, OSError) as exc:
        log(f"falha ao publicar pedido.excluido de {id_pedido}: {exc!r}")


def descrever_indisponiveis(indisponiveis):
    partes = []
    for item in indisponiveis if isinstance(indisponiveis, list) else []:
        if not isinstance(item, dict):
            partes.append(str(item))
        elif item.get("nome"):
            partes.append(
                f"{item['nome']} (solicitado {item.get('solicitado')}, "
                f"disponível {item.get('disponivel')})"
            )
        else:
            partes.append(f"produto {item.get('codigo')} inexistente")
    return ", ".join(partes) or "itens indisponíveis"


def estoque_confirmado(id_pedido, dados):
    campos = sem_nulos(valor_total=valor_monetario(dados.get("valor_total")))
    if isinstance(dados.get("itens"), list) and dados["itens"]:
        campos["itens"] = dados["itens"]
    aplicar(
        id_pedido, "pedido.estoque_ok", ESTOQUE_CONFIRMADO,
        f"Estoque Confirmado para o pedido {id_pedido}.", **campos,
    )


def estoque_indisponivel(id_pedido, dados):
    itens = descrever_indisponiveis(dados.get("indisponiveis"))
    mensagem = f"Estoque Indisponível para o pedido {id_pedido}: {itens}."
    pedido, _ = aplicar(id_pedido, "estoque.indisponivel", ESTOQUE_INDISPONIVEL, mensagem, motivo=mensagem)
    if pedido is not None and pedido["status"] == ESTOQUE_INDISPONIVEL:
        excluir_pedido(id_pedido, "estoque_indisponivel")


def pagamento_pendente(id_pedido, dados):
    aplicar(
        id_pedido, "pagamento.pendente", AGUARDANDO_PAGAMENTO,
        f"Pedido {id_pedido} aguardando pagamento.",
        **sem_nulos(url_pagamento=dados.get("url_pagamento"), id_cobranca=dados.get("id_cobranca")),
    )


def pagamento_aprovado(id_pedido, dados):
    aplicar(
        id_pedido, "pagamento.aprovado", PAGAMENTO_APROVADO,
        f"Pagamento Aprovado para o pedido {id_pedido}.",
        **sem_nulos(id_cobranca=dados.get("id_cobranca")),
    )


def pagamento_recusado(id_pedido, dados):
    motivo = dados.get("motivo") if isinstance(dados.get("motivo"), str) else None
    pedido, _ = aplicar(
        id_pedido, "pagamento.recusado", PAGAMENTO_RECUSADO,
        f"Pagamento Recusado para o pedido {id_pedido}.",
        motivo=motivo or "Pagamento recusado.",
        **sem_nulos(id_cobranca=dados.get("id_cobranca")),
    )
    if pedido is not None and pedido["status"] == PAGAMENTO_RECUSADO:
        excluir_pedido(id_pedido, "pagamento_recusado")


def pedido_enviado(id_pedido, dados):
    nota_fiscal = dados.get("nota_fiscal")
    codigo_rastreio = dados.get("codigo_rastreio")
    aplicar(
        id_pedido, "pedido.enviado", ENVIADO,
        f"Pedido {id_pedido} Enviado! Nota fiscal {nota_fiscal}, rastreio {codigo_rastreio}.",
        **sem_nulos(nota_fiscal=nota_fiscal, codigo_rastreio=codigo_rastreio),
    )


TRATADORES = {
    "pedido.estoque_ok": estoque_confirmado,
    "estoque.indisponivel": estoque_indisponivel,
    "pagamento.pendente": pagamento_pendente,
    "pagamento.aprovado": pagamento_aprovado,
    "pagamento.recusado": pagamento_recusado,
    "pedido.enviado": pedido_enviado,
}


def promocao_recente(dados):
    criado_em = dados.get("criado_em")
    if not isinstance(criado_em, str):
        return True
    try:
        instante = datetime.fromisoformat(criado_em.replace("Z", "+00:00"))
    except ValueError:
        return True
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - instante <= VALIDADE_PROMOCAO


def repassar_promocao(routing_key, dados):
    if not promocao_recente(dados):
        log(f"{routing_key} antiga ignorada ({dados.get('id_promocao')})")
        return
    hub.difundir("promocao", dados)
    log(f"{routing_key} repassada via SSE ({hub.conexoes()} conexoes)")


def tratar_evento(routing_key, dados):
    if not isinstance(dados, dict):
        log(f"{routing_key} ignorado: dados invalidos")
        return
    if routing_key.startswith("promocao.categoria."):
        repassar_promocao(routing_key, dados)
        return
    tratador = TRATADORES.get(routing_key)
    id_pedido = dados.get("id_pedido")
    if tratador is None or not isinstance(id_pedido, str) or not id_pedido:
        log(f"{routing_key} ignorado: evento sem tratamento ou sem id_pedido")
        return
    tratador(id_pedido, dados)


class Servidor(uvicorn.Server):
    # No Ctrl+C encerra tambem os streams SSE abertos, que nao terminam sozinhos.
    def handle_exit(self, sig, frame):
        hub.encerrar()
        super().handle_exit(sig, frame)


def main():
    desativar_edicao_rapida()
    servidor = Servidor(
        uvicorn.Config(app, host=config.HTTP_HOST, port=config.PORTA_PRINCIPAL, timeout_graceful_shutdown=2)
    )
    try:
        servidor.run()
    except KeyboardInterrupt:
        pass
    if not servidor.started:
        sys.exit(1)


if __name__ == "__main__":
    main()
