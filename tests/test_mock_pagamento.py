# Testes do Mock de Pagamento "PayMock" (sistema externo): API REST de
# cobrancas, pagina de checkout, decisao do cliente (POST -> 303 -> GET) e
# Webhook assinado com HMAC-SHA256, repetido em erro de rede/HTTP 5xx.
# Nao usam RabbitMQ, Docker nem outros processos: a loja que recebe o Webhook
# e simulada com httpx.MockTransport.
import ast
import hashlib
import hmac
import json
import re
import threading
import time
import types
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import carregar_main

mock = carregar_main("mock_pagamento")
paginas = mock.mock_paginas

SEGREDO = "segredo-dos-testes"
URL_PUBLICA = "http://paymock.teste:8005"
URL_WEBHOOK = "http://loja.teste/webhooks/pagamento"


class LojaFalsa:
    # Destino do Webhook: guarda as requisicoes e responde na ordem de
    # "respostas" (codigo HTTP ou "rede" para falha de conexao); depois, 200.
    def __init__(self):
        self.respostas = []
        self.requisicoes = []
        self.lock_preso = []

    def __call__(self, request):
        self.requisicoes.append(request)
        self.lock_preso.append(mock.lock.locked())
        resposta = self.respostas.pop(0) if self.respostas else 200
        if resposta == "rede":
            raise httpx.ConnectError("conexao recusada", request=request)
        return httpx.Response(resposta, json={"resultado": f"HTTP {resposta}"})


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setattr(mock, "WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setattr(mock, "URL_PUBLICA", URL_PUBLICA)
    monkeypatch.setattr(mock, "ESPERAS_WEBHOOK", (0, 0))
    mock.cobrancas.clear()
    mock.em_envio.clear()
    yield
    mock.cobrancas.clear()
    mock.em_envio.clear()


@pytest.fixture(autouse=True)
def loja(monkeypatch):
    destino = LojaFalsa()
    monkeypatch.setattr(mock, "transporte_webhook", httpx.MockTransport(destino))
    return destino


@pytest.fixture
def cliente():
    with TestClient(mock.app, follow_redirects=False) as cliente_http:
        yield cliente_http


def criar(http, **campos):
    corpo = {
        "id_pedido": "PED1A2B3C4D",
        "valor": 4500.0,
        "descricao": "Notebook Gamer (1 item)",
        "cliente": {"email": "ana@exemplo.com", "nome": "Ana"},
        "webhook_url": URL_WEBHOOK,
    }
    corpo.update(campos)
    resposta = http.post("/api/cobrancas", json=corpo)
    assert resposta.status_code == 201, resposta.text
    return resposta.json()["id_cobranca"]


def consultar(http, id_cobranca):
    resposta = http.get(f"/api/cobrancas/{id_cobranca}")
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def decidir(http, id_cobranca, decisao):
    return http.post(f"/checkout/{id_cobranca}/decisao", data={"decisao": decisao})


def assinatura(corpo):
    return "sha256=" + hmac.new(SEGREDO.encode("utf-8"), corpo, hashlib.sha256).hexdigest()


# --- API REST ------------------------------------------------------------------
def test_criar_cobranca_devolve_url_de_checkout(cliente):
    resposta = cliente.post(
        "/api/cobrancas", json={"id_pedido": "PED1", "valor": 4500, "webhook_url": URL_WEBHOOK}
    )
    assert resposta.status_code == 201
    corpo = resposta.json()
    assert set(corpo) == {"id_cobranca", "status", "url_checkout"}
    assert re.fullmatch(r"COB[0-9A-F]{10}", corpo["id_cobranca"])
    assert corpo["status"] == "PENDENTE"
    assert corpo["url_checkout"] == f"{URL_PUBLICA}/checkout/{corpo['id_cobranca']}"


def test_consultar_cobranca(cliente):
    id_cobranca = criar(cliente, valor=19.999, descricao="  Teclado  ")
    dados = consultar(cliente, id_cobranca)
    assert dados["id_cobranca"] == id_cobranca
    assert dados["id_pedido"] == "PED1A2B3C4D"
    assert dados["valor"] == 20.0
    assert dados["descricao"] == "Teclado"
    assert dados["cliente"] == {"email": "ana@exemplo.com", "nome": "Ana"}
    assert dados["webhook_url"] == URL_WEBHOOK
    assert dados["status"] == "PENDENTE"
    assert dados["webhook"] is None
    assert dados["criado_em"] == dados["atualizado_em"]
    assert dados["url_checkout"] == f"{URL_PUBLICA}/checkout/{id_cobranca}"
    assert consultar(cliente, id_cobranca.lower())["id_cobranca"] == id_cobranca
    nao_existe = cliente.get("/api/cobrancas/COB0000000000")
    assert nao_existe.status_code == 404
    assert nao_existe.json()["detail"] == "Cobrança não encontrada."


def test_ids_de_cobranca_sao_unicos(cliente):
    ids = {criar(cliente) for _ in range(20)}
    assert len(ids) == 20


@pytest.mark.parametrize(
    "alteracao",
    [
        {"valor": 0},
        {"valor": -10},
        {"valor": 0.001},
        {"valor": "abc"},
        {"id_pedido": ""},
        {"id_pedido": "   "},
        {"id_pedido": 123},
        {"webhook_url": "ftp://loja.teste/webhook"},
        {"webhook_url": "loja.teste/webhook"},
        {"webhook_url": "http://"},
        {"webhook_url": "http://loja.teste:99999/webhook"},
        {"webhook_url": "http://[::1/webhook"},
        {"webhook_url": "http://loja.teste/a b"},
        {"webhook_url": "http://loja.teste/a\r\nX-Falso: 1"},
        {"cliente": 42},
    ],
)
def test_criar_cobranca_invalida_422(cliente, alteracao):
    corpo = {"id_pedido": "PED1", "valor": 10.0, "webhook_url": URL_WEBHOOK, **alteracao}
    assert cliente.post("/api/cobrancas", json=corpo).status_code == 422
    assert mock.cobrancas == {}


@pytest.mark.parametrize("campo", ["id_pedido", "valor", "webhook_url"])
def test_criar_cobranca_sem_campo_obrigatorio_422(cliente, campo):
    corpo = {"id_pedido": "PED1", "valor": 10.0, "webhook_url": URL_WEBHOOK}
    del corpo[campo]
    assert cliente.post("/api/cobrancas", json=corpo).status_code == 422


@pytest.mark.parametrize("valor", ["Infinity", "-Infinity", "NaN"])
def test_criar_cobranca_com_valor_nao_finito_422(cliente, valor):
    # O json do Python aceita NaN/Infinity; a resposta 422 nao pode quebrar.
    corpo = f'{{"id_pedido": "PED1", "valor": {valor}, "webhook_url": "{URL_WEBHOOK}"}}'
    resposta = cliente.post(
        "/api/cobrancas", content=corpo.encode(), headers={"Content-Type": "application/json"}
    )
    assert resposta.status_code == 422
    assert resposta.json()["detail"][0]["loc"] == ["body", "valor"]
    assert mock.cobrancas == {}


def test_descricao_longa_e_aceita(cliente):
    # A loja pode listar muitos itens na descricao: nao pode virar 422.
    descricao = ", ".join(f"Produto {n} x2" for n in range(60))
    id_cobranca = criar(cliente, descricao=descricao)
    assert consultar(cliente, id_cobranca)["descricao"] == descricao


def test_cliente_guarda_so_email_e_nome(cliente):
    id_cobranca = criar(
        cliente, cliente={"email": " ana@exemplo.com ", "nome": None, "extra": {"x": [1, 2]}}
    )
    assert consultar(cliente, id_cobranca)["cliente"] == {"email": "ana@exemplo.com", "nome": None}
    sem_cliente = criar(cliente, cliente=None)
    assert consultar(cliente, sem_cliente)["cliente"] is None


def test_cancelar_cobranca_pendente(cliente, loja):
    id_cobranca = criar(cliente)
    resposta = cliente.post(f"/api/cobrancas/{id_cobranca}/cancelar")
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "CANCELADO"
    de_novo = cliente.post(f"/api/cobrancas/{id_cobranca}/cancelar")
    assert de_novo.status_code == 200
    assert de_novo.json()["status"] == "CANCELADO"

    # O cliente ainda estava com a pagina aberta e clicou depois do cancelamento.
    assert decidir(cliente, id_cobranca, "APROVADO").status_code == 303
    assert loja.requisicoes == []
    assert consultar(cliente, id_cobranca)["status"] == "CANCELADO"

    pagina = cliente.get(f"/checkout/{id_cobranca}").text
    assert "Cobrança cancelada pela loja" in pagina
    assert 'value="APROVADO"' not in pagina
    assert "Reenviar notificação" not in pagina
    assert "Fechar aba" in pagina


def test_cancelar_cobranca_finalizada_409_e_inexistente_404(cliente):
    id_cobranca = criar(cliente)
    decidir(cliente, id_cobranca, "APROVADO")
    resposta = cliente.post(f"/api/cobrancas/{id_cobranca}/cancelar")
    assert resposta.status_code == 409
    assert "APROVADO" in resposta.json()["detail"]
    assert consultar(cliente, id_cobranca)["status"] == "APROVADO"
    assert cliente.post("/api/cobrancas/COB0000000000/cancelar").status_code == 404


def test_saude_e_pagina_inicial(cliente):
    assert cliente.get("/saude").json() == {"servico": "mock_pagamento", "status": "ok"}
    inicio = cliente.get("/")
    assert inicio.status_code == 200
    assert "PayMock" in inicio.text


# --- Pagina de checkout --------------------------------------------------------
def test_pagina_checkout_pendente(cliente):
    id_cobranca = criar(cliente)
    resposta = cliente.get(f"/checkout/{id_cobranca}")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/html")
    assert resposta.headers["cache-control"] == "no-store"
    pagina = resposta.text
    assert '<html lang="pt-BR">' in pagina
    assert "PayMock" in pagina
    assert "PED1A2B3C4D" in pagina and id_cobranca in pagina
    assert "Notebook Gamer (1 item)" in pagina
    assert "Ana (ana@exemplo.com)" in pagina
    assert "R$ 4.500,00" in pagina
    assert "Aguardando pagamento" in pagina
    assert f'method="post" action="/checkout/{id_cobranca}/decisao" autocomplete="off"' in pagina
    assert re.search(r'<button[^>]*name="decisao" value="APROVADO">Pagamento Aprovado</button>', pagina)
    assert re.search(r'<button[^>]*name="decisao" value="RECUSADO">Pagamento Recusado</button>', pagina)
    # Clique duplo: o valor do botao vai para o campo oculto antes de desabilitar.
    assert '<input type="hidden" id="campo-decisao" name="decisao" value="">' in pagina
    assert "campo.value = evento.currentTarget.value" in pagina
    assert "botoes[j].disabled = true" in pagina
    # Autocontida: nada carregado de fora.
    assert "<script src" not in pagina and 'rel="stylesheet"' not in pagina
    assert "https://" not in pagina
    assert "Fechar aba" not in pagina


def test_pagina_checkout_escapa_valores_dinamicos(cliente):
    id_cobranca = criar(
        cliente,
        id_pedido='PED"><i>x</i>',
        descricao='<script>alert("x")</script>',
        cliente={"nome": "<b>Ana</b>", "email": "ana@exemplo.com"},
    )
    pagina = cliente.get(f"/checkout/{id_cobranca}").text
    assert '<script>alert("x")</script>' not in pagina
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in pagina
    assert "<b>Ana</b>" not in pagina and "&lt;b&gt;Ana&lt;/b&gt;" in pagina
    assert "<i>x</i>" not in pagina and "PED&quot;&gt;&lt;i&gt;x&lt;/i&gt;" in pagina


def test_cliente_como_texto_e_sem_descricao(cliente):
    id_cobranca = criar(cliente, cliente="Bruno <bruno@exemplo.com>", descricao=None)
    pagina = cliente.get(f"/checkout/{id_cobranca}").text
    assert "Bruno &lt;bruno@exemplo.com&gt;" in pagina
    assert "Descrição" not in pagina


def test_pagina_checkout_inexistente_404(cliente):
    resposta = cliente.get("/checkout/%3Cb%3Ecob")
    assert resposta.status_code == 404
    assert resposta.headers["content-type"].startswith("text/html")
    assert "Cobrança não encontrada" in resposta.text
    assert "&lt;B&gt;COB" in resposta.text and "<B>COB" not in resposta.text


# --- Decisao do cliente e Webhook ----------------------------------------------
def test_decisao_aprovada_envia_webhook_assinado_e_redireciona(cliente, loja):
    id_cobranca = criar(cliente)
    resposta = decidir(cliente, id_cobranca, "APROVADO")
    assert resposta.status_code == 303
    assert resposta.headers["location"] == f"/checkout/{id_cobranca}"

    assert len(loja.requisicoes) == 1
    requisicao = loja.requisicoes[0]
    assert requisicao.method == "POST"
    assert str(requisicao.url) == URL_WEBHOOK
    assert requisicao.headers["content-type"] == "application/json"
    assert requisicao.headers["x-webhook-signature"] == assinatura(requisicao.content)

    cobranca = consultar(cliente, id_cobranca)
    assert json.loads(requisicao.content) == {
        "id_cobranca": id_cobranca,
        "id_pedido": "PED1A2B3C4D",
        "status": "APROVADO",
        "valor": 4500.0,
        "data_hora": cobranca["atualizado_em"],
    }
    assert cobranca["status"] == "APROVADO"
    webhook = cobranca["webhook"]
    assert webhook["entregue"] is True
    assert webhook["http_status"] == 200
    assert webhook["tentativas"] == 1
    assert webhook["erro"] is None
    assert "resultado" in webhook["resposta"]


def test_assinatura_muda_com_o_segredo(cliente, loja, monkeypatch):
    monkeypatch.setattr(mock, "WEBHOOK_SECRET", "outro-segredo")
    decidir(cliente, criar(cliente), "RECUSADO")
    requisicao = loja.requisicoes[0]
    esperado = hmac.new(b"outro-segredo", requisicao.content, hashlib.sha256).hexdigest()
    assert requisicao.headers["x-webhook-signature"] == f"sha256={esperado}"
    assert requisicao.headers["x-webhook-signature"] != assinatura(requisicao.content)


def test_pagina_apos_decisao_mostra_resultado_do_webhook(cliente):
    id_cobranca = criar(cliente)
    resposta = cliente.post(
        f"/checkout/{id_cobranca}/decisao", data={"decisao": "APROVADO"}, follow_redirects=True
    )
    assert resposta.status_code == 200
    pagina = resposta.text
    assert "Pagamento aprovado" in pagina
    assert "Notificação enviada à loja (HTTP 200)" in pagina
    assert "Você já pode fechar esta aba e acompanhar o pedido na loja." in pagina
    assert "Fechar aba" in pagina and "window.close()" in pagina
    assert "Reenviar notificação" not in pagina
    assert 'value="APROVADO"' not in pagina
    assert 'http-equiv="refresh"' not in pagina


def test_segunda_decisao_nao_reenvia_webhook(cliente, loja):
    id_cobranca = criar(cliente)
    assert decidir(cliente, id_cobranca, "RECUSADO").status_code == 303
    resposta = decidir(cliente, id_cobranca, "APROVADO")
    assert resposta.status_code == 303
    assert resposta.headers["location"] == f"/checkout/{id_cobranca}"
    assert len(loja.requisicoes) == 1
    assert json.loads(loja.requisicoes[0].content)["status"] == "RECUSADO"
    assert consultar(cliente, id_cobranca)["status"] == "RECUSADO"
    assert "Pagamento recusado" in cliente.get(f"/checkout/{id_cobranca}").text


@pytest.mark.parametrize(
    "corpo",
    ["decisao=TALVEZ", "decisao=", "", "outro=APROVADO", "decisao=APROVADO&decisao=RECUSADO"],
)
def test_decisao_invalida_400(cliente, loja, corpo):
    id_cobranca = criar(cliente)
    resposta = cliente.post(
        f"/checkout/{id_cobranca}/decisao",
        content=corpo,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resposta.status_code == 400
    assert "Decisão inválida" in resposta.text
    assert loja.requisicoes == []
    assert consultar(cliente, id_cobranca)["status"] == "PENDENTE"


@pytest.mark.parametrize(
    "corpo, esperado",
    [("decisao=&decisao=APROVADO", "APROVADO"), ("decisao=recusado", "RECUSADO")],
)
def test_decisao_aceita_formulario_sem_javascript(cliente, loja, corpo, esperado):
    # Sem JavaScript o campo oculto vai vazio junto com o valor do botao clicado.
    id_cobranca = criar(cliente)
    resposta = cliente.post(
        f"/checkout/{id_cobranca}/decisao",
        content=corpo,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resposta.status_code == 303
    assert consultar(cliente, id_cobranca)["status"] == esperado
    assert len(loja.requisicoes) == 1


def test_decisao_em_cobranca_inexistente_404(cliente, loja):
    resposta = decidir(cliente, "COB0000000000", "APROVADO")
    assert resposta.status_code == 404
    assert "Cobrança não encontrada" in resposta.text
    assert loja.requisicoes == []


def test_webhook_repete_em_erro_de_rede_e_5xx(cliente, loja):
    loja.respostas[:] = [503, "rede", 200]
    id_cobranca = criar(cliente)
    assert decidir(cliente, id_cobranca, "APROVADO").status_code == 303
    assert len(loja.requisicoes) == 3
    corpos = {requisicao.content for requisicao in loja.requisicoes}
    assert len(corpos) == 1
    corpo = corpos.pop()
    assert {r.headers["x-webhook-signature"] for r in loja.requisicoes} == {assinatura(corpo)}
    webhook = consultar(cliente, id_cobranca)["webhook"]
    assert webhook["entregue"] is True
    assert webhook["tentativas"] == 3
    assert webhook["http_status"] == 200


def test_webhook_desiste_apos_tres_tentativas(cliente, loja, monkeypatch):
    esperas = []
    monkeypatch.setattr(mock, "time", types.SimpleNamespace(sleep=esperas.append))
    monkeypatch.setattr(mock, "ESPERAS_WEBHOOK", (0.5, 1.0))
    loja.respostas[:] = ["rede", 500, 502, 200]
    id_cobranca = criar(cliente)
    assert decidir(cliente, id_cobranca, "APROVADO").status_code == 303
    assert len(loja.requisicoes) == 3
    assert esperas == [0.5, 1.0]
    webhook = consultar(cliente, id_cobranca)["webhook"]
    assert webhook["entregue"] is False
    assert webhook["tentativas"] == 3
    assert webhook["http_status"] == 502
    assert webhook["erro"] == "HTTP 502"


def test_webhook_nao_repete_em_4xx_e_pagina_oferece_reenvio(cliente, loja):
    loja.respostas[:] = [409]
    id_cobranca = criar(cliente)
    decidir(cliente, id_cobranca, "APROVADO")
    assert len(loja.requisicoes) == 1
    webhook = consultar(cliente, id_cobranca)["webhook"]
    assert webhook["entregue"] is False
    assert webhook["http_status"] == 409
    assert webhook["tentativas"] == 1
    assert webhook["erro"] == "HTTP 409"
    pagina = cliente.get(f"/checkout/{id_cobranca}").text
    assert "Falha ao notificar a loja: HTTP 409 (após 1 tentativa)." in pagina
    assert "Reenviar notificação" in pagina
    assert f'action="/checkout/{id_cobranca}/reenviar"' in pagina
    assert "Você já pode fechar esta aba e acompanhar o pedido na loja." in pagina
    assert "Fechar aba" in pagina


def test_reenviar_notificacao_nao_entregue(cliente, loja):
    loja.respostas[:] = ["rede", "rede", "rede"]
    id_cobranca = criar(cliente)
    decidir(cliente, id_cobranca, "RECUSADO")
    webhook = consultar(cliente, id_cobranca)["webhook"]
    assert webhook["entregue"] is False
    assert webhook["tentativas"] == 3
    assert webhook["http_status"] is None
    assert "ConnectError" in webhook["erro"]
    pagina = cliente.get(f"/checkout/{id_cobranca}").text
    assert "Falha ao notificar a loja: ConnectError" in pagina
    assert "Reenviar notificação" in pagina

    resposta = cliente.post(f"/checkout/{id_cobranca}/reenviar")
    assert resposta.status_code == 303
    assert resposta.headers["location"] == f"/checkout/{id_cobranca}"
    assert len(loja.requisicoes) == 4
    assert loja.requisicoes[3].content == loja.requisicoes[0].content
    assert consultar(cliente, id_cobranca)["webhook"]["entregue"] is True
    assert "Notificação enviada à loja (HTTP 200)" in cliente.get(f"/checkout/{id_cobranca}").text

    cliente.post(f"/checkout/{id_cobranca}/reenviar")
    assert len(loja.requisicoes) == 4  # ja entregue: nao envia de novo


def test_reenviar_so_vale_para_cobranca_finalizada(cliente, loja):
    id_cobranca = criar(cliente)
    resposta = cliente.post(f"/checkout/{id_cobranca}/reenviar")
    assert resposta.status_code == 303
    assert loja.requisicoes == []
    cliente.post(f"/api/cobrancas/{id_cobranca}/cancelar")
    cliente.post(f"/checkout/{id_cobranca}/reenviar")
    assert loja.requisicoes == []
    assert cliente.post("/checkout/COB0000000000/reenviar").status_code == 404


def test_envio_em_andamento_nao_e_duplicado(cliente, loja):
    loja.respostas[:] = [500, 500, 500]
    id_cobranca = criar(cliente)
    decidir(cliente, id_cobranca, "APROVADO")
    assert len(loja.requisicoes) == 3
    mock.em_envio.add(id_cobranca)  # outra aba esta reenviando agora
    cliente.post(f"/checkout/{id_cobranca}/reenviar")
    assert len(loja.requisicoes) == 3
    pagina = cliente.get(f"/checkout/{id_cobranca}").text
    assert "Enviando a notificação para a loja" in pagina
    assert 'http-equiv="refresh"' in pagina
    assert "Reenviar notificação" not in pagina


def test_webhook_enviado_sem_segurar_o_lock(cliente, loja):
    loja.respostas[:] = [503, 200]
    decidir(cliente, criar(cliente), "APROVADO")
    assert loja.lock_preso == [False, False]


def test_cliques_simultaneos_enviam_um_unico_webhook(cliente, loja, monkeypatch):
    # A loja demora a responder: enquanto o primeiro Webhook esta em envio, os
    # outros cliques sao respondidos na hora (o lock nao fica preso no envio).
    liberar = threading.Event()

    def loja_lenta(request):
        loja.requisicoes.append(request)
        liberar.wait(5)
        return httpx.Response(200, json={"resultado": "processado"})

    monkeypatch.setattr(mock, "transporte_webhook", httpx.MockTransport(loja_lenta))
    id_cobranca = criar(cliente)
    respostas = []

    def clicar(decisao):
        respostas.append(decidir(cliente, id_cobranca, decisao).status_code)

    threads = [
        threading.Thread(target=clicar, args=("APROVADO" if n % 2 else "RECUSADO",)) for n in range(8)
    ]
    for thread in threads:
        thread.start()
    limite = time.monotonic() + 5
    while len(respostas) < 7 and time.monotonic() < limite:
        time.sleep(0.01)
    try:
        assert len(respostas) == 7
        assert len(loja.requisicoes) == 1
        pagina = cliente.get(f"/checkout/{id_cobranca}").text
        assert "Enviando a notificação para a loja" in pagina
        assert cliente.post(f"/api/cobrancas/{id_cobranca}/cancelar").status_code == 409
    finally:
        liberar.set()
        for thread in threads:
            thread.join(5)
    assert respostas == [303] * 8
    assert len(loja.requisicoes) == 1
    enviado = json.loads(loja.requisicoes[0].content)["status"]
    cobranca = consultar(cliente, id_cobranca)
    assert cobranca["status"] == enviado
    assert cobranca["webhook"]["entregue"] is True
    assert id_cobranca not in mock.em_envio


def test_webhook_com_erro_inesperado_nao_derruba_a_pagina(cliente, monkeypatch):
    def quebrar(request):
        raise RuntimeError("falha inesperada")

    monkeypatch.setattr(mock, "transporte_webhook", httpx.MockTransport(quebrar))
    id_cobranca = criar(cliente)
    assert decidir(cliente, id_cobranca, "APROVADO").status_code == 303
    webhook = consultar(cliente, id_cobranca)["webhook"]
    assert webhook["entregue"] is False
    assert webhook["tentativas"] == 1
    assert webhook["erro"] == "RuntimeError: falha inesperada"
    assert id_cobranca not in mock.em_envio


# --- Funcoes auxiliares e configuracao -------------------------------------------
@pytest.mark.parametrize(
    "valor, texto",
    [(4500, "R$ 4.500,00"), (0.5, "R$ 0,50"), (30, "R$ 30,00"), (1234567.891, "R$ 1.234.567,89")],
)
def test_formatar_reais(valor, texto):
    assert paginas.formatar_reais(valor) == texto


def test_configuracao_le_env_com_as_regras_do_projeto(tmp_path, monkeypatch):
    arquivo = tmp_path / ".env"
    arquivo.write_text(
        "MOCK_PORT=9005\n"
        "# comentario\n"
        "\n"
        "export MOCK_PUBLIC_URL='http://pay.local:9005/'\n"
        'WEBHOOK_SECRET = "abc"\n'
        "WEBHOOK_SECRET=ignorado\n"
        "LINHA_SEM_IGUAL\n",
        encoding="utf-8-sig",  # BOM do Bloco de Notas do Windows
    )
    valores = mock._ler_env(arquivo)
    assert valores == {
        "MOCK_PORT": "9005",
        "MOCK_PUBLIC_URL": "http://pay.local:9005/",
        "WEBHOOK_SECRET": "abc",
    }
    assert mock._ler_env(tmp_path / "nao_existe") == {}

    monkeypatch.setattr(mock, "_ARQUIVO_ENV", valores)
    monkeypatch.setenv("MOCK_PORT", "7005")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    assert mock._env("MOCK_PORT", "8005") == "7005"  # o ambiente vence o .env
    assert mock._env("WEBHOOK_SECRET", "padrao") == "abc"
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    assert mock._env("WEBHOOK_SECRET", "padrao") == "padrao"  # vazio = padrao
    assert mock._env("VARIAVEL_QUE_NAO_EXISTE_XYZ", "padrao") == "padrao"


def test_mock_e_independente_do_backend():
    for arquivo in Path(mock.__file__).resolve().parent.glob("*.py"):
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                modulos = [alias.name for alias in no.names]
            elif isinstance(no, ast.ImportFrom):
                modulos = [no.module or ""]
            else:
                continue
            for modulo in modulos:
                raiz = modulo.split(".")[0]
                assert raiz not in ("common", "pika", "Crypto"), f"{arquivo.name} importa {modulo}"
