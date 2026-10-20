# Testes unitarios do MS Pagamento: criacao/cancelamento da cobranca no Mock
# (httpx.MockTransport) a partir dos eventos, o endpoint de Webhook (TestClient) e
# a persistencia em SQLite (pasta temporaria), inclusive um reinicio simulado.
# Nao dependem de RabbitMQ: o Publisher e substituido por um falso.
import hashlib
import hmac
import json
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pika.exceptions import AMQPConnectionError

from common import config
from common.mq import EXCHANGE_ECOMMERCE
from conftest import carregar_main
from pagamentos_db import CAMPOS, PagamentosDB

pagamento = carregar_main("ms_pagamento")

SEGREDO = "segredo-de-teste"
URL_MOCK = "http://mock.teste"
URL_WEBHOOK = "http://pagamento.teste/webhooks/pagamento"

PEDIDO = {
    "id_pedido": "PED1A2B3C4D",
    "cliente": {"email": "ana@exemplo.com", "nome": "Ana"},
    "itens": [
        {"codigo": "A1", "nome": "Notebook Gamer", "categoria": "A", "quantidade": 1,
         "preco_unitario": 4500.0, "subtotal": 4500.0},
        {"codigo": "B1", "nome": "Teclado Mecânico", "categoria": "B", "quantidade": 2,
         "preco_unitario": 350.0, "subtotal": 700.0},
    ],
    "valor_total": 5200.0,
}

COBRANCA_SALVA = {
    "id_cobranca": "COB0000000001",
    "id_pedido": "PED1A2B3C4D",
    "valor": 5200.0,
    "status": "PENDENTE",
    "url_pagamento": f"{URL_MOCK}/checkout/COB0000000001",
    "criado_em": "2026-10-19T12:00:00+00:00",
    "atualizado_em": "2026-10-19T12:00:00+00:00",
}


class PublisherFalso:
    def __init__(self):
        self.publicados = []
        self.falhas = 0
        self.ao_publicar = None
        self._lock = threading.Lock()

    def publish(self, exchange, routing_key, data):
        if self.ao_publicar is not None:
            self.ao_publicar()
        with self._lock:
            if self.falhas > 0:
                self.falhas -= 1
                raise AMQPConnectionError("RabbitMQ fora do ar")
            self.publicados.append((exchange, routing_key, data))

    def chaves(self):
        return [routing_key for _, routing_key, _ in self.publicados]


class MockPagamentoFalso:
    # Simula o Mock de Pagamento; respostas_criacao define, em ordem, o resultado
    # de cada POST /api/cobrancas (status HTTP ou excecao); vazio = 201.
    def __init__(self):
        self.chamadas = []
        self.respostas_criacao = []
        self.resposta_cancelamento = 200
        self.ao_criar = None
        self.criadas = 0

    def __call__(self, request):
        corpo = json.loads(request.content) if request.content else None
        self.chamadas.append((request.method, request.url.path, corpo))
        if request.method == "POST" and request.url.path == "/api/cobrancas":
            return self._criar(request)
        if request.method == "POST" and request.url.path.endswith("/cancelar"):
            if isinstance(self.resposta_cancelamento, Exception):
                raise self.resposta_cancelamento
            return httpx.Response(self.resposta_cancelamento, json={})
        return httpx.Response(404, json={"detail": "nao encontrado"})

    def _criar(self, request):
        if self.ao_criar is not None:
            self.ao_criar()
        resposta = self.respostas_criacao.pop(0) if self.respostas_criacao else 201
        if isinstance(resposta, Exception):
            raise resposta
        if resposta != 201:
            return httpx.Response(resposta, json={"detail": "erro"})
        self.criadas += 1
        id_cobranca = f"COB{self.criadas:010X}"
        return httpx.Response(
            201,
            json={"id_cobranca": id_cobranca, "status": "PENDENTE",
                  "url_checkout": f"{URL_MOCK}/checkout/{id_cobranca}"},
        )

    def caminhos(self):
        return [caminho for _, caminho, _ in self.chamadas]


def estado_vazio(monkeypatch):
    monkeypatch.setattr(pagamento, "cobrancas", {})
    monkeypatch.setattr(pagamento, "cobranca_do_pedido", {})
    monkeypatch.setattr(pagamento, "pedidos_excluidos", set())
    monkeypatch.setattr(pagamento, "em_publicacao", set())


@pytest.fixture
def ctx(monkeypatch):
    estado_vazio(monkeypatch)
    monkeypatch.setattr(pagamento, "db", None)
    publicador = PublisherFalso()
    monkeypatch.setattr(pagamento, "publisher", publicador)
    mock = MockPagamentoFalso()
    monkeypatch.setattr(pagamento, "transporte_http", httpx.MockTransport(mock))
    monkeypatch.setattr(config, "URL_MOCK_PAGAMENTO", URL_MOCK + "/")
    monkeypatch.setattr(config, "URL_WEBHOOK_PAGAMENTO", URL_WEBHOOK)
    monkeypatch.setattr(config, "WEBHOOK_SECRET", SEGREDO)
    pausas = []
    monkeypatch.setattr(pagamento.time, "sleep", pausas.append)
    return SimpleNamespace(publicador=publicador, mock=mock, pausas=pausas, cliente=TestClient(pagamento.app))


@pytest.fixture
def banco(ctx, tmp_path, monkeypatch):
    # SQLite em pasta temporaria. ctx.reiniciar() simula um reinicio do servico:
    # memoria vazia e estado recarregado por uma nova conexao com o mesmo arquivo.
    abertos = []

    def abrir():
        aberto = PagamentosDB(tmp_path / "data" / "pagamentos.db")
        abertos.append(aberto)
        monkeypatch.setattr(pagamento, "db", aberto)
        return aberto

    def reiniciar():
        pagamento.db.fechar()
        estado_vazio(monkeypatch)
        abrir()
        return pagamento.restaurar_cobrancas()

    ctx.reiniciar = reiniciar
    yield abrir()
    for aberto in abertos:
        aberto.fechar()


def status_gravado(id_cobranca="COB0000000001"):
    # Le por outra conexao o que um processo reiniciado agora carregaria.
    leitor = PagamentosDB(pagamento.db.caminho)
    try:
        return leitor.carregar()[0][id_cobranca]["status"]
    finally:
        leitor.fechar()


def criar_cobranca_pendente(ctx, pedido=PEDIDO):
    pagamento.handle("pedido.estoque_ok", pedido)
    _, routing_key, dados = ctx.publicador.publicados[-1]
    assert routing_key == "pagamento.pendente"
    return dados


def assinar(corpo, segredo=SEGREDO):
    return "sha256=" + hmac.new(segredo.encode("utf-8"), corpo, hashlib.sha256).hexdigest()


def enviar_webhook(ctx, dados=None, corpo=None, assinatura="auto"):
    if corpo is None:
        corpo = json.dumps(dados).encode("utf-8")
    cabecalhos = {"Content-Type": "application/json"}
    if assinatura == "auto":
        assinatura = assinar(corpo)
    if assinatura is not None:
        cabecalhos["X-Webhook-Signature"] = assinatura
    return ctx.cliente.post("/webhooks/pagamento", content=corpo, headers=cabecalhos)


def notificacao(pendente, status="APROVADO", **extra):
    dados = {
        "id_cobranca": pendente["id_cobranca"],
        "id_pedido": pendente["id_pedido"],
        "status": status,
        "valor": pendente["valor"],
        "data_hora": "2026-10-19T12:00:00+00:00",
    }
    dados.update(extra)
    return dados


def status_da_cobranca(ctx, id_pedido=PEDIDO["id_pedido"]):
    resposta = ctx.cliente.get(f"/pagamentos/{id_pedido}")
    assert resposta.status_code == 200
    return resposta.json()["status"]


# --- Importacao -------------------------------------------------------------------

def test_importar_nao_conecta_nem_inicia_threads():
    assert pagamento.publisher is None
    assert pagamento.db is None
    nomes = {thread.name for thread in threading.enumerate()}
    assert "consumer-fila.pagamento" not in nomes
    assert "keepalive-pagamento" not in nomes
    assert pagamento.app.title == "MS Pagamento"


# --- pedido.estoque_ok --------------------------------------------------------

def test_estoque_ok_cria_cobranca_no_mock_e_publica_pendente(ctx):
    pagamento.handle("pedido.estoque_ok", PEDIDO)

    assert ctx.mock.chamadas == [(
        "POST",
        "/api/cobrancas",
        {
            "id_pedido": "PED1A2B3C4D",
            "valor": 5200.0,
            "descricao": "Pedido PED1A2B3C4D - 3 item(ns)",
            "cliente": "ana@exemplo.com",
            "webhook_url": URL_WEBHOOK,
        },
    )]
    assert ctx.publicador.publicados == [(
        "eCommerce",
        "pagamento.pendente",
        {
            "id_pedido": "PED1A2B3C4D",
            "id_cobranca": "COB0000000001",
            "url_pagamento": f"{URL_MOCK}/checkout/COB0000000001",
            "valor": 5200.0,
        },
    )]
    cobranca = ctx.cliente.get("/pagamentos/PED1A2B3C4D").json()
    assert cobranca["status"] == "PENDENTE"
    assert cobranca["id_cobranca"] == "COB0000000001"
    assert cobranca["url_pagamento"] == f"{URL_MOCK}/checkout/COB0000000001"
    assert cobranca["valor"] == 5200.0


def test_estoque_ok_sem_email_nao_envia_cliente(ctx):
    pagamento.handle("pedido.estoque_ok", {**PEDIDO, "cliente": None})
    _, _, corpo = ctx.mock.chamadas[0]
    assert "cliente" not in corpo


def test_estoque_ok_repetido_republica_a_mesma_cobranca(ctx):
    pagamento.handle("pedido.estoque_ok", PEDIDO)
    pagamento.handle("pedido.estoque_ok", PEDIDO)

    assert ctx.mock.caminhos() == ["/api/cobrancas"]
    assert ctx.publicador.chaves() == ["pagamento.pendente", "pagamento.pendente"]
    assert ctx.publicador.publicados[0] == ctx.publicador.publicados[1]


def test_estoque_ok_repetido_apos_finalizar_nao_publica(ctx):
    pendente = criar_cobranca_pendente(ctx)
    assert enviar_webhook(ctx, notificacao(pendente)).status_code == 200

    pagamento.handle("pedido.estoque_ok", PEDIDO)
    assert ctx.publicador.chaves() == ["pagamento.pendente", "pagamento.aprovado"]
    assert ctx.mock.caminhos() == ["/api/cobrancas"]


def test_estoque_ok_de_pedido_ja_excluido_e_ignorado(ctx):
    pagamento.handle("pedido.excluido", {"id_pedido": "PED1A2B3C4D", "motivo": "cancelado_pelo_cliente"})
    pagamento.handle("pedido.estoque_ok", PEDIDO)

    assert ctx.mock.chamadas == []
    assert ctx.publicador.publicados == []


def test_falhas_temporarias_do_mock_sao_repetidas(ctx):
    ctx.mock.respostas_criacao = [503, httpx.ConnectError("conexao recusada")]
    pagamento.handle("pedido.estoque_ok", PEDIDO)

    assert ctx.mock.caminhos() == ["/api/cobrancas"] * 3
    assert ctx.pausas == [1, 2]
    assert ctx.publicador.chaves() == ["pagamento.pendente"]


def test_mock_fora_do_ar_publica_pagamento_recusado(ctx):
    ctx.mock.respostas_criacao = [httpx.ConnectError("conexao recusada")] * 2 + [httpx.ReadTimeout("timeout")]
    pagamento.handle("pedido.estoque_ok", PEDIDO)

    assert len(ctx.mock.chamadas) == 3
    assert ctx.pausas == [1, 2]
    assert ctx.publicador.publicados == [(
        "eCommerce",
        "pagamento.recusado",
        {
            "id_pedido": "PED1A2B3C4D",
            "id_cobranca": None,
            "motivo": "Falha ao criar a cobrança no provedor de pagamento",
        },
    )]
    assert ctx.cliente.get("/pagamentos/PED1A2B3C4D").status_code == 404


def test_url_do_mock_invalida_publica_pagamento_recusado(ctx, monkeypatch):
    monkeypatch.setattr(config, "URL_MOCK_PAGAMENTO", "http://[::1")
    pagamento.handle("pedido.estoque_ok", PEDIDO)
    assert ctx.publicador.chaves() == ["pagamento.recusado"]
    assert ctx.pausas == [1, 2]


@pytest.mark.parametrize("status_http", [400, 422])
def test_mock_rejeita_a_cobranca_sem_nova_tentativa(ctx, status_http):
    ctx.mock.respostas_criacao = [status_http]
    pagamento.handle("pedido.estoque_ok", PEDIDO)

    assert len(ctx.mock.chamadas) == 1
    assert ctx.pausas == []
    assert ctx.publicador.chaves() == ["pagamento.recusado"]


def test_resposta_invalida_do_mock_publica_pagamento_recusado(ctx, monkeypatch):
    transporte = httpx.MockTransport(lambda request: httpx.Response(201, json={"status": "PENDENTE"}))
    monkeypatch.setattr(pagamento, "transporte_http", transporte)
    pagamento.handle("pedido.estoque_ok", PEDIDO)
    assert ctx.publicador.chaves() == ["pagamento.recusado"]


def test_pedido_excluido_durante_a_chamada_ao_mock_cancela_a_cobranca(ctx):
    ctx.mock.ao_criar = lambda: pagamento.pedidos_excluidos.add("PED1A2B3C4D")
    pagamento.handle("pedido.estoque_ok", PEDIDO)

    assert ctx.mock.caminhos() == ["/api/cobrancas", "/api/cobrancas/COB0000000001/cancelar"]
    assert ctx.publicador.publicados == []
    assert status_da_cobranca(ctx) == "CANCELADO"


# --- pedido.excluido ------------------------------------------------------------

def test_pedido_excluido_cancela_a_cobranca_pendente(ctx):
    pendente = criar_cobranca_pendente(ctx)
    pagamento.handle("pedido.excluido", {"id_pedido": "PED1A2B3C4D", "motivo": "cancelado_pelo_cliente"})

    assert ctx.mock.caminhos()[-1] == f"/api/cobrancas/{pendente['id_cobranca']}/cancelar"
    assert status_da_cobranca(ctx) == "CANCELADO"

    resposta = enviar_webhook(ctx, notificacao(pendente, "APROVADO"))
    assert resposta.status_code == 409
    assert ctx.publicador.chaves() == ["pagamento.pendente"]


def test_cancelamento_no_mock_e_melhor_esforco(ctx):
    criar_cobranca_pendente(ctx)
    ctx.mock.resposta_cancelamento = httpx.ConnectError("conexao recusada")
    pagamento.handle("pedido.excluido", {"id_pedido": "PED1A2B3C4D", "motivo": "cancelado_pelo_cliente"})
    assert status_da_cobranca(ctx) == "CANCELADO"

    ctx.mock.resposta_cancelamento = 409
    pagamento.handle("pedido.excluido", {"id_pedido": "PED1A2B3C4D", "motivo": "cancelado_pelo_cliente"})
    assert status_da_cobranca(ctx) == "CANCELADO"


def test_pedido_excluido_sem_cobranca_so_e_registrado(ctx):
    pagamento.handle("pedido.excluido", {"id_pedido": "PED9", "motivo": "estoque_indisponivel"})
    assert ctx.mock.chamadas == []
    assert "PED9" in pagamento.pedidos_excluidos


def test_pedido_excluido_apos_recusa_nao_altera_a_cobranca(ctx):
    pendente = criar_cobranca_pendente(ctx)
    assert enviar_webhook(ctx, notificacao(pendente, "RECUSADO")).status_code == 200
    pagamento.handle("pedido.excluido", {"id_pedido": "PED1A2B3C4D", "motivo": "pagamento_recusado"})

    assert status_da_cobranca(ctx) == "RECUSADO"
    assert not any(caminho.endswith("/cancelar") for caminho in ctx.mock.caminhos())
    resposta = enviar_webhook(ctx, notificacao(pendente, "RECUSADO"))
    assert resposta.json() == {"resultado": "ja_processado", "status": "RECUSADO"}


# --- Webhook --------------------------------------------------------------------

def test_webhook_aprovado_publica_pagamento_aprovado(ctx):
    pendente = criar_cobranca_pendente(ctx)
    resposta = enviar_webhook(ctx, notificacao(pendente, "APROVADO"))

    assert resposta.status_code == 200
    assert resposta.json() == {"resultado": "processado", "status": "APROVADO"}
    assert ctx.publicador.publicados[-1] == (
        "eCommerce",
        "pagamento.aprovado",
        {"id_pedido": "PED1A2B3C4D", "id_cobranca": pendente["id_cobranca"], "valor": 5200.0},
    )
    assert status_da_cobranca(ctx) == "APROVADO"


def test_webhook_recusado_publica_pagamento_recusado(ctx):
    pendente = criar_cobranca_pendente(ctx)
    resposta = enviar_webhook(ctx, notificacao(pendente, "RECUSADO"))

    assert resposta.status_code == 200
    assert resposta.json() == {"resultado": "processado", "status": "RECUSADO"}
    assert ctx.publicador.publicados[-1] == (
        "eCommerce",
        "pagamento.recusado",
        {
            "id_pedido": "PED1A2B3C4D",
            "id_cobranca": pendente["id_cobranca"],
            "motivo": "Pagamento recusado pelo cliente no provedor de pagamento",
        },
    )
    assert status_da_cobranca(ctx) == "RECUSADO"


def test_webhook_aceita_hex_maiusculo_na_assinatura(ctx):
    pendente = criar_cobranca_pendente(ctx)
    corpo = json.dumps(notificacao(pendente)).encode("utf-8")
    resposta = enviar_webhook(ctx, corpo=corpo, assinatura="sha256=" + assinar(corpo)[7:].upper())
    assert resposta.status_code == 200


@pytest.mark.parametrize(
    "assinatura",
    [None, "", "sha256=", "sha256=abc", "md5=" + "0" * 64, "sha256=" + "0" * 64, "sha256=café".encode("latin-1")],
)
def test_webhook_sem_assinatura_valida_retorna_401(ctx, assinatura):
    pendente = criar_cobranca_pendente(ctx)
    resposta = enviar_webhook(ctx, notificacao(pendente), assinatura=assinatura)

    assert resposta.status_code == 401
    assert ctx.publicador.chaves() == ["pagamento.pendente"]
    assert status_da_cobranca(ctx) == "PENDENTE"


def test_webhook_assinado_com_outro_segredo_ou_corpo_retorna_401(ctx):
    pendente = criar_cobranca_pendente(ctx)
    corpo = json.dumps(notificacao(pendente)).encode("utf-8")
    assert enviar_webhook(ctx, corpo=corpo, assinatura=assinar(corpo, "outro-segredo")).status_code == 401

    adulterado = json.dumps(notificacao(pendente, "APROVADO", valor=1.0)).encode("utf-8")
    assert enviar_webhook(ctx, corpo=adulterado, assinatura=assinar(corpo)).status_code == 401
    assert ctx.publicador.chaves() == ["pagamento.pendente"]


@pytest.mark.parametrize(
    "corpo",
    [
        b"nao e json",
        b"[]",
        b'{"id_pedido": "PED1A2B3C4D", "status": "APROVADO"}',
        b'{"id_cobranca": "COB0000000001", "id_pedido": "PED1A2B3C4D", "status": "PAGO"}',
        b'{"id_cobranca": "", "id_pedido": "PED1A2B3C4D", "status": "APROVADO"}',
        b'\xff\xfe{"status": "APROVADO"}',
    ],
)
def test_webhook_com_corpo_invalido_retorna_422(ctx, corpo):
    criar_cobranca_pendente(ctx)
    resposta = enviar_webhook(ctx, corpo=corpo)

    assert resposta.status_code == 422
    assert isinstance(resposta.json()["detail"], list)
    assert ctx.publicador.chaves() == ["pagamento.pendente"]


def test_webhook_de_outro_pedido_retorna_422(ctx):
    pendente = criar_cobranca_pendente(ctx)
    resposta = enviar_webhook(ctx, notificacao(pendente, id_pedido="PEDOUTRO"))

    assert resposta.status_code == 422
    assert status_da_cobranca(ctx) == "PENDENTE"
    assert ctx.publicador.chaves() == ["pagamento.pendente"]


def test_webhook_de_cobranca_desconhecida_retorna_404(ctx):
    resposta = enviar_webhook(
        ctx, {"id_cobranca": "COBNAOEXISTE", "id_pedido": "PED1", "status": "APROVADO", "valor": 1.0}
    )
    assert resposta.status_code == 404
    assert ctx.publicador.publicados == []


def test_webhook_repetido_publica_uma_unica_vez(ctx):
    pendente = criar_cobranca_pendente(ctx)
    primeira = enviar_webhook(ctx, notificacao(pendente, "APROVADO"))
    repetida = enviar_webhook(ctx, notificacao(pendente, "APROVADO"))
    divergente = enviar_webhook(ctx, notificacao(pendente, "RECUSADO"))

    assert primeira.json() == {"resultado": "processado", "status": "APROVADO"}
    assert repetida.status_code == 200
    assert repetida.json() == {"resultado": "ja_processado", "status": "APROVADO"}
    assert divergente.status_code == 409
    assert ctx.publicador.chaves() == ["pagamento.pendente", "pagamento.aprovado"]
    assert status_da_cobranca(ctx) == "APROVADO"


def test_falha_ao_publicar_retorna_503_e_permite_reenvio(ctx):
    pendente = criar_cobranca_pendente(ctx)
    ctx.publicador.falhas = 1
    falhou = enviar_webhook(ctx, notificacao(pendente, "APROVADO"))

    assert falhou.status_code == 503
    assert status_da_cobranca(ctx) == "PENDENTE"
    assert ctx.publicador.chaves() == ["pagamento.pendente"]

    reenvio = enviar_webhook(ctx, notificacao(pendente, "APROVADO"))
    assert reenvio.status_code == 200
    assert reenvio.json() == {"resultado": "processado", "status": "APROVADO"}
    assert ctx.publicador.chaves() == ["pagamento.pendente", "pagamento.aprovado"]


def test_falha_ao_publicar_com_pedido_excluido_no_meio_cancela(ctx):
    pendente = criar_cobranca_pendente(ctx)

    def excluir_durante_a_publicacao():
        ctx.publicador.ao_publicar = None
        pagamento.handle("pedido.excluido", {"id_pedido": "PED1A2B3C4D", "motivo": "cancelado_pelo_cliente"})

    ctx.publicador.ao_publicar = excluir_durante_a_publicacao
    ctx.publicador.falhas = 1
    assert enviar_webhook(ctx, notificacao(pendente, "APROVADO")).status_code == 503
    assert status_da_cobranca(ctx) == "CANCELADO"
    assert enviar_webhook(ctx, notificacao(pendente, "APROVADO")).status_code == 409
    assert ctx.publicador.chaves() == ["pagamento.pendente"]


def test_webhooks_simultaneos_publicam_uma_unica_vez(ctx):
    # A primeira publicacao so termina depois que os 7 repetidos foram respondidos:
    # todos chegam durante ela e recebem 503 (e nao ja_processado).
    pendente = criar_cobranca_pendente(ctx)
    respondidos = threading.Semaphore(0)

    def esperar_os_repetidos():
        for _ in range(7):
            assert respondidos.acquire(timeout=5)

    ctx.publicador.ao_publicar = esperar_os_repetidos
    barreira = threading.Barrier(8)
    resultados = []

    def receber():
        barreira.wait()
        try:
            resultados.append(pagamento.processar_webhook(
                pagamento.NotificacaoPagamento(**notificacao(pendente, "APROVADO"))
            )["resultado"])
        except HTTPException as exc:
            resultados.append(exc.status_code)
        finally:
            respondidos.release()

    threads = [threading.Thread(target=receber) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert sorted(resultados, key=str) == [503] * 7 + ["processado"]
    assert ctx.publicador.chaves() == ["pagamento.pendente", "pagamento.aprovado"]
    assert pagamento.em_publicacao == set()
    repetido = enviar_webhook(ctx, notificacao(pendente, "APROVADO"))
    assert repetido.json() == {"resultado": "ja_processado", "status": "APROVADO"}


def test_repetido_durante_publicacao_que_falha_recebe_503(ctx):
    pendente = criar_cobranca_pendente(ctx)
    publicando, liberar = threading.Event(), threading.Event()

    def travar_e_depois_falhar():
        ctx.publicador.ao_publicar = None
        publicando.set()
        assert liberar.wait(5)

    ctx.publicador.ao_publicar = travar_e_depois_falhar
    ctx.publicador.falhas = 1
    primeiro = []

    def receber_o_primeiro():
        try:
            pagamento.processar_webhook(pagamento.NotificacaoPagamento(**notificacao(pendente, "APROVADO")))
        except HTTPException as exc:
            primeiro.append(exc.status_code)

    thread = threading.Thread(target=receber_o_primeiro)
    thread.start()
    try:
        assert publicando.wait(5)
        repetido = enviar_webhook(ctx, notificacao(pendente, "APROVADO"))
        consulta = ctx.cliente.get("/pagamentos/PED1A2B3C4D").json()
    finally:
        liberar.set()
        thread.join(5)

    assert repetido.status_code == 503
    assert repetido.json() == {"detail": "Pagamento em processamento. Tente novamente."}
    assert set(consulta) == set(CAMPOS)  # a marca de publicacao nao vaza na consulta
    assert primeiro == [503]
    assert status_da_cobranca(ctx) == "PENDENTE"
    assert ctx.publicador.chaves() == ["pagamento.pendente"]

    reenvio = enviar_webhook(ctx, notificacao(pendente, "APROVADO"))
    assert reenvio.status_code == 200
    assert reenvio.json() == {"resultado": "processado", "status": "APROVADO"}
    assert ctx.publicador.chaves() == ["pagamento.pendente", "pagamento.aprovado"]


# --- Consultas --------------------------------------------------------------------

def test_consulta_de_pedido_sem_cobranca_retorna_404(ctx):
    assert ctx.cliente.get("/pagamentos/PEDNAOEXISTE").status_code == 404


def test_saude(ctx):
    assert ctx.cliente.get("/saude").json() == {"servico": "pagamento", "status": "ok"}
