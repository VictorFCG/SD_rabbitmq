# Testes do MS Principal (API Gateway) sem RabbitMQ: publisher falso, SQLite em
# tmp_path e httpx.MockTransport no lugar do MS Estoque/MS Promocoes. O lifespan
# do FastAPI nao e executado.
import asyncio
import signal
import sys
import threading
import time
import warnings
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from pika.exceptions import AMQPConnectionError

from conftest import carregar_main

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

principal = carregar_main("ms_principal")
# Os mesmos modulos auxiliares que o main.py importou.
pedidos = sys.modules[principal.PedidosDB.__module__]
notificacoes = sys.modules[principal.HubNotificacoes.__module__]

EMAIL = "ana@exemplo.com"
TODOS_STATUS = list(pedidos.ROTULOS)


class PublicadorFalso:
    def __init__(self):
        self.publicados = []
        self.falhar = False

    def publish(self, exchange, routing_key, data):
        if self.falhar:
            raise AMQPConnectionError("RabbitMQ fora do ar")
        self.publicados.append((exchange, routing_key, data))

    def chaves(self):
        return [routing_key for _, routing_key, _ in self.publicados]


@pytest.fixture
def db(tmp_path):
    banco = pedidos.PedidosDB(tmp_path / "data" / "principal.db")
    yield banco
    banco.fechar()


@pytest.fixture
def gateway(db, monkeypatch):
    publicador = PublicadorFalso()
    hub = notificacoes.HubNotificacoes()
    monkeypatch.setattr(principal, "db", db)
    monkeypatch.setattr(principal, "publisher", publicador)
    monkeypatch.setattr(principal, "hub", hub)
    return SimpleNamespace(db=db, pub=publicador, hub=hub, client=TestClient(principal.app))


def criar_pedido(gateway, email=EMAIL, itens=None, nome="Ana"):
    resposta = gateway.client.post(
        "/api/pedidos",
        json={"email": email, "nome": nome, "itens": itens or [{"codigo": "B1", "quantidade": 2}]},
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def dados_evento(routing_key, id_pedido):
    if routing_key == "pedido.estoque_ok":
        return {
            "id_pedido": id_pedido,
            "cliente": {"email": EMAIL, "nome": "Ana"},
            "itens": [
                {
                    "codigo": "B1",
                    "nome": "Teclado Mecânico",
                    "categoria": "B",
                    "quantidade": 2,
                    "preco_unitario": 350.0,
                    "subtotal": 700.0,
                }
            ],
            "valor_total": 700.0,
        }
    if routing_key == "estoque.indisponivel":
        return {
            "id_pedido": id_pedido,
            "indisponiveis": [{"codigo": "A2", "nome": 'Monitor 69"', "solicitado": 1, "disponivel": 0}],
        }
    if routing_key == "pagamento.pendente":
        return {
            "id_pedido": id_pedido,
            "id_cobranca": "COB0123456789",
            "url_pagamento": "http://127.0.0.1:8005/checkout/COB0123456789",
            "valor": 700.0,
        }
    if routing_key == "pagamento.aprovado":
        return {"id_pedido": id_pedido, "id_cobranca": "COB0123456789", "valor": 700.0}
    if routing_key == "pagamento.recusado":
        return {
            "id_pedido": id_pedido,
            "id_cobranca": "COB0123456789",
            "motivo": "Pagamento recusado pelo cliente no provedor de pagamento",
        }
    if routing_key == "pedido.enviado":
        return {
            "id_pedido": id_pedido,
            "nota_fiscal": "NF-1A2B3C4D",
            "codigo_rastreio": "SD123456789BR",
            "enviado_em": datetime.now(timezone.utc).isoformat(),
        }
    raise AssertionError(routing_key)


CAMINHOS = {
    "CRIADO": [],
    "ESTOQUE_CONFIRMADO": ["pedido.estoque_ok"],
    "AGUARDANDO_PAGAMENTO": ["pedido.estoque_ok", "pagamento.pendente"],
    "PAGAMENTO_APROVADO": ["pedido.estoque_ok", "pagamento.pendente", "pagamento.aprovado"],
    "ENVIADO": ["pedido.estoque_ok", "pagamento.pendente", "pagamento.aprovado", "pedido.enviado"],
    "ESTOQUE_INDISPONIVEL": ["estoque.indisponivel"],
    "PAGAMENTO_RECUSADO": ["pedido.estoque_ok", "pagamento.pendente", "pagamento.recusado"],
}


def levar_ao_status(id_pedido, status):
    for routing_key in CAMINHOS[status]:
        principal.tratar_evento(routing_key, dados_evento(routing_key, id_pedido))


def notificacoes_sse(hub, email=EMAIL):
    return [item for item in hub.pendentes(email, 0) if item["evento"] == "pedido"]


async def proximo(gerador, timeout=2):
    return await asyncio.wait_for(gerador.__anext__(), timeout)


def test_db_cria_pedido_no_formato_do_contrato(db):
    pedido = db.criar(EMAIL, "Ana", [{"codigo": "B1", "quantidade": 2}])
    assert pedido["id_pedido"].startswith("PED") and len(pedido["id_pedido"]) == 11
    assert all(c in "0123456789ABCDEF" for c in pedido["id_pedido"][3:])
    assert pedido["status"] == "CRIADO" and pedido["status_label"] == "Pedido Criado"
    assert pedido["itens"] == [{"codigo": "B1", "quantidade": 2}]
    for campo in ("valor_total", "url_pagamento", "id_cobranca", "nota_fiscal", "codigo_rastreio", "motivo"):
        assert pedido[campo] is None
    assert pedido["historico"] == [
        {
            "status": "CRIADO",
            "status_label": "Pedido Criado",
            "mensagem": "Pedido criado. Aguardando confirmação de estoque.",
            "timestamp": pedido["criado_em"],
        }
    ]
    assert pedido["criado_em"] == pedido["atualizado_em"]
    assert datetime.fromisoformat(pedido["criado_em"]).tzinfo is not None
    assert db.obter(pedido["id_pedido"]) == pedido
    assert db.obter("PEDNAOEXISTE") is None


def test_db_lista_por_email_mais_recentes_primeiro(db):
    ids = [db.criar(EMAIL, None, [{"codigo": "C1", "quantidade": 1}])["id_pedido"] for _ in range(3)]
    db.criar("outro@exemplo.com", None, [{"codigo": "C1", "quantidade": 1}])
    assert [p["id_pedido"] for p in db.listar_por_email(EMAIL)] == list(reversed(ids))
    assert db.listar_por_email("ninguem@exemplo.com") == []


def test_db_remover_e_persistencia(tmp_path):
    caminho = tmp_path / "principal.db"
    banco = pedidos.PedidosDB(caminho)
    mantido = banco.criar(EMAIL, "Ana", [{"codigo": "A1", "quantidade": 1}])
    removido = banco.criar(EMAIL, "Ana", [{"codigo": "A1", "quantidade": 1}])
    banco.remover(removido["id_pedido"])
    banco.fechar()
    reaberto = pedidos.PedidosDB(caminho)
    assert reaberto.obter(mantido["id_pedido"]) == mantido
    assert reaberto.obter(removido["id_pedido"]) is None
    reaberto.fechar()


def test_db_transicoes_validas_atualizam_campos_e_historico(db):
    pedido = db.criar(EMAIL, "Ana", [{"codigo": "B1", "quantidade": 2}])
    id_pedido = pedido["id_pedido"]
    itens = dados_evento("pedido.estoque_ok", id_pedido)["itens"]

    atual, aplicado = db.transicionar(id_pedido, "ESTOQUE_CONFIRMADO", "ok", itens=itens, valor_total=700.0)
    assert aplicado and atual["status"] == "ESTOQUE_CONFIRMADO"
    assert atual["status_label"] == "Estoque Confirmado"
    assert atual["itens"] == itens and atual["valor_total"] == 700.0

    db.transicionar(id_pedido, "AGUARDANDO_PAGAMENTO", "pendente", url_pagamento="http://x/c", id_cobranca="COB1")
    db.transicionar(id_pedido, "PAGAMENTO_APROVADO", "aprovado")
    final, aplicado = db.transicionar(
        id_pedido, "ENVIADO", "enviado", nota_fiscal="NF-1", codigo_rastreio="SD1BR"
    )
    assert aplicado
    assert final == db.obter(id_pedido)
    assert final["url_pagamento"] == "http://x/c" and final["id_cobranca"] == "COB1"
    assert final["nota_fiscal"] == "NF-1" and final["codigo_rastreio"] == "SD1BR"
    assert [h["status"] for h in final["historico"]] == [
        "CRIADO", "ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO", "PAGAMENTO_APROVADO", "ENVIADO",
    ]
    assert final["historico"][-1]["status_label"] == "Pedido Enviado"
    assert final["historico"][-1]["mensagem"] == "enviado"
    assert final["atualizado_em"] >= final["criado_em"]


def test_db_transicao_nao_permitida_nao_altera_nada(db):
    pedido = db.criar(EMAIL, None, [{"codigo": "B1", "quantidade": 1}])
    atual, aplicado = db.transicionar(pedido["id_pedido"], "PAGAMENTO_APROVADO", "x", motivo="y")
    assert not aplicado and atual == pedido
    assert db.obter(pedido["id_pedido"]) == pedido
    assert db.transicionar("PEDNAOEXISTE", "CANCELADO", "x") == (None, False)
    with pytest.raises(ValueError):
        db.transicionar(pedido["id_pedido"], "CANCELADO", "x", email="outro@exemplo.com")


def test_tabela_de_transicoes_igual_ao_contrato():
    permitidas = {
        ("CRIADO", "ESTOQUE_CONFIRMADO"), ("CRIADO", "ESTOQUE_INDISPONIVEL"), ("CRIADO", "CANCELADO"),
        ("ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO"), ("ESTOQUE_CONFIRMADO", "PAGAMENTO_RECUSADO"),
        ("ESTOQUE_CONFIRMADO", "CANCELADO"),
        ("AGUARDANDO_PAGAMENTO", "PAGAMENTO_APROVADO"), ("AGUARDANDO_PAGAMENTO", "PAGAMENTO_RECUSADO"),
        ("AGUARDANDO_PAGAMENTO", "CANCELADO"),
        ("PAGAMENTO_APROVADO", "ENVIADO"),
    }
    for atual in TODOS_STATUS:
        for novo in TODOS_STATUS:
            assert pedidos.transicao_permitida(atual, novo) == ((atual, novo) in permitidas), (atual, novo)
    assert pedidos.ROTULOS == {
        "CRIADO": "Pedido Criado",
        "ESTOQUE_CONFIRMADO": "Estoque Confirmado",
        "AGUARDANDO_PAGAMENTO": "Aguardando Pagamento",
        "PAGAMENTO_APROVADO": "Pagamento Aprovado",
        "ENVIADO": "Pedido Enviado",
        "ESTOQUE_INDISPONIVEL": "Estoque Indisponível",
        "PAGAMENTO_RECUSADO": "Pagamento Recusado",
        "CANCELADO": "Pedido Cancelado",
    }


def test_db_transicao_atomica_entre_threads(db):
    pedido = db.criar(EMAIL, None, [{"codigo": "B1", "quantidade": 1}])
    barreira = threading.Barrier(8)
    resultados = []

    def disputar(status):
        barreira.wait()
        resultados.append(db.transicionar(pedido["id_pedido"], status, status)[1])

    threads = [
        threading.Thread(target=disputar, args=("CANCELADO" if i % 2 else "ESTOQUE_INDISPONIVEL",))
        for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert resultados.count(True) == 1
    assert len(db.obter(pedido["id_pedido"])["historico"]) == 2


def test_hub_entrega_evento_enviado_de_outra_thread():
    hub = notificacoes.HubNotificacoes()

    async def cenario():
        hub.configurar(asyncio.get_running_loop())
        fila = hub.inscrever(EMAIL)
        outra = hub.inscrever("outro@exemplo.com")
        enviados = []
        thread = threading.Thread(target=lambda: enviados.append(hub.enviar(EMAIL, "pedido", {"n": 1})))
        thread.start()
        item = await asyncio.wait_for(fila.get(), 2)
        thread.join()
        assert item == enviados[0]
        assert item["evento"] == "pedido" and item["dados"] == {"n": 1}
        await asyncio.sleep(0.05)
        assert outra.empty()

    asyncio.run(cenario())


def test_hub_ids_crescentes_a_partir_do_relogio():
    inicio = int(time.time() * 1000)
    hub = notificacoes.HubNotificacoes()
    ids = [hub.enviar(EMAIL, "pedido", {"n": n})["id"] for n in range(5)]
    ids.append(hub.difundir("promocao", {"n": 5})["id"])
    assert ids[0] > inicio - 1
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert hub.ultimo_id() == ids[-1]


def test_hub_sem_loop_apenas_guarda_para_reenvio():
    hub = notificacoes.HubNotificacoes(tamanho_historico=3)
    itens = [hub.enviar(EMAIL, "pedido", {"n": n}) for n in range(5)]
    assert hub.pendentes(EMAIL, 0) == itens[2:]
    assert hub.pendentes(EMAIL, itens[3]["id"]) == itens[4:]
    assert hub.pendentes("outro@exemplo.com", 0) == []


def test_hub_difunde_somente_para_emails_conectados():
    hub = notificacoes.HubNotificacoes()

    async def cenario():
        hub.configurar(asyncio.get_running_loop())
        filas = [hub.inscrever(EMAIL), hub.inscrever(EMAIL), hub.inscrever("bia@exemplo.com")]
        item = hub.difundir("promocao", {"codigo": "B1"})
        for fila in filas:
            assert await asyncio.wait_for(fila.get(), 2) == item
        assert hub.pendentes(EMAIL, 0) == [item]
        assert hub.pendentes("bia@exemplo.com", 0) == [item]
        assert hub.pendentes("desconectado@exemplo.com", 0) == []

    asyncio.run(cenario())


def test_hub_fila_cheia_descarta_sem_erro():
    hub = notificacoes.HubNotificacoes(tamanho_fila=1)

    async def cenario():
        hub.configurar(asyncio.get_running_loop())
        fila = hub.inscrever(EMAIL)
        for n in range(3):
            hub.enviar(EMAIL, "pedido", {"n": n})
        await asyncio.sleep(0.05)
        assert fila.qsize() == 1
        assert (await fila.get())["dados"] == {"n": 0}

    asyncio.run(cenario())


def test_hub_conexoes_e_cancelamento():
    hub = notificacoes.HubNotificacoes()
    a1, a2, b = hub.inscrever(EMAIL), hub.inscrever(EMAIL), hub.inscrever("bia@exemplo.com")
    assert hub.conexoes() == 3
    hub.cancelar(EMAIL, a1)
    hub.cancelar(EMAIL, a1)
    assert hub.conexoes() == 2
    hub.cancelar(EMAIL, a2)
    hub.cancelar("bia@exemplo.com", b)
    hub.cancelar("nunca@exemplo.com", b)
    assert hub.conexoes() == 0


def test_hub_com_loop_encerrado_nao_quebra_quem_envia():
    hub = notificacoes.HubNotificacoes()
    loop = asyncio.new_event_loop()
    hub.configurar(loop)
    hub.inscrever(EMAIL)
    loop.close()
    item = hub.enviar(EMAIL, "pedido", {"n": 1})
    assert hub.pendentes(EMAIL, 0) == [item]


def test_sse_envia_conectado_e_eventos_ao_vivo(gateway):
    hub = gateway.hub

    async def cenario():
        hub.configurar(asyncio.get_running_loop())
        gerador = principal.eventos(email=EMAIL, last_event_id=None, ultimo_id=None)
        conectado = await proximo(gerador)
        assert conectado.event == "conectado" and conectado.retry == 3000 and conectado.id is None
        assert conectado.data["email"] == EMAIL and conectado.data["timestamp"]
        assert hub.conexoes() == 1

        enviados = []
        thread = threading.Thread(
            target=lambda: enviados.append(hub.enviar(EMAIL, "pedido", {"tipo": "pedido.estoque_ok"}))
        )
        thread.start()
        evento = await proximo(gerador)
        thread.join()
        assert evento.event == "pedido"
        assert evento.id == str(enviados[0]["id"])
        assert evento.data == {"tipo": "pedido.estoque_ok"}

        promocao = hub.difundir("promocao", {"codigo": "C1"})
        evento = await proximo(gerador)
        assert (evento.event, evento.id, evento.data) == ("promocao", str(promocao["id"]), {"codigo": "C1"})

        await gerador.aclose()
        assert hub.conexoes() == 0

    asyncio.run(cenario())


def test_sse_reenvia_perdidos_sem_duplicar(gateway):
    hub = gateway.hub

    async def cenario():
        hub.configurar(asyncio.get_running_loop())
        e1, e2, e3 = (hub.enviar(EMAIL, "pedido", {"n": n}) for n in (1, 2, 3))
        gerador = principal.eventos(email=EMAIL, last_event_id=str(e1["id"]), ultimo_id=None)
        assert (await proximo(gerador)).event == "conectado"
        # Ja inscrito, mas o buffer ainda nao foi lido: e4 fica no buffer E na fila.
        e4 = hub.enviar(EMAIL, "pedido", {"n": 4})
        recebidos = [await proximo(gerador) for _ in range(3)]
        e5 = hub.enviar(EMAIL, "pedido", {"n": 5})
        recebidos.append(await proximo(gerador))
        assert [ev.id for ev in recebidos] == [str(e["id"]) for e in (e2, e3, e4, e5)]
        assert [ev.data["n"] for ev in recebidos] == [2, 3, 4, 5]
        await gerador.aclose()
        assert hub.conexoes() == 0

    asyncio.run(cenario())


def test_sse_usa_o_maior_id_entre_cabecalho_e_query(gateway):
    hub = gateway.hub

    async def cenario():
        hub.configurar(asyncio.get_running_loop())
        e1, e2, e3 = (hub.enviar(EMAIL, "pedido", {"n": n}) for n in (1, 2, 3))
        gerador = principal.eventos(email=EMAIL, last_event_id=str(e1["id"]), ultimo_id=str(e2["id"]))
        await proximo(gerador)
        assert (await proximo(gerador)).id == str(e3["id"])
        await gerador.aclose()

        gerador = principal.eventos(email=EMAIL, last_event_id="lixo", ultimo_id=str(e2["id"]))
        await proximo(gerador)
        assert (await proximo(gerador)).id == str(e3["id"])
        await gerador.aclose()

    asyncio.run(cenario())


def test_sse_sem_ultimo_id_nao_reenvia_e_id_futuro_reenvia_tudo(gateway):
    hub = gateway.hub

    async def cenario():
        hub.configurar(asyncio.get_running_loop())
        antigo = hub.enviar(EMAIL, "pedido", {"n": 1})

        gerador = principal.eventos(email=EMAIL, last_event_id=None, ultimo_id=None)
        await proximo(gerador)
        novo = hub.enviar(EMAIL, "pedido", {"n": 2})
        assert (await proximo(gerador)).id == str(novo["id"])
        await gerador.aclose()

        # Id de uma execucao anterior do gateway (maior que todos): reenvia o buffer.
        futuro = str(hub.ultimo_id() + 10_000)
        gerador = principal.eventos(email=EMAIL, last_event_id=futuro, ultimo_id=None)
        await proximo(gerador)
        assert [(await proximo(gerador)).id for _ in range(2)] == [str(antigo["id"]), str(novo["id"])]
        await gerador.aclose()

    asyncio.run(cenario())


def test_sse_termina_os_streams_no_desligamento(gateway):
    hub = gateway.hub

    async def cenario():
        hub.configurar(asyncio.get_running_loop())
        geradores = [principal.eventos(email=email, last_event_id=None, ultimo_id=None)
                     for email in (EMAIL, EMAIL, "bia@exemplo.com")]
        for gerador in geradores:
            await proximo(gerador)
        assert hub.conexoes() == 3
        # O tratador de sinal roda fora do loop/sem lock: simulado por outra thread.
        thread = threading.Thread(target=hub.encerrar)
        thread.start()
        thread.join()
        for gerador in geradores:
            with pytest.raises(StopAsyncIteration):
                await proximo(gerador)
        assert hub.conexoes() == 0

        tardio = principal.eventos(email=EMAIL, last_event_id=None, ultimo_id=None)
        with pytest.raises(StopAsyncIteration):
            await proximo(tardio)
        assert hub.conexoes() == 0

    asyncio.run(cenario())


def test_ctrl_c_no_servidor_encerra_os_streams(gateway):
    servidor = principal.Servidor(uvicorn.Config(principal.app))
    servidor.handle_exit(signal.SIGINT, None)
    assert servidor.should_exit and gateway.hub.encerrado


def test_sse_email_invalido_responde_422_antes_do_stream(gateway):
    for params in ({"email": "x"}, {}, {"email": "a b@c.com"}):
        resposta = gateway.client.get("/api/eventos", params=params)
        assert resposta.status_code == 422
        assert resposta.headers["content-type"].startswith("application/json")
    assert gateway.hub.conexoes() == 0


def test_criar_pedido_publica_e_notifica(gateway):
    pedido = criar_pedido(gateway)
    id_pedido = pedido["id_pedido"]
    assert pedido["status"] == "CRIADO" and pedido["status_label"] == "Pedido Criado"
    assert pedido["email"] == EMAIL and pedido["nome"] == "Ana"
    assert set(pedido) == {
        "id_pedido", "email", "nome", "itens", "valor_total", "status", "status_label", "url_pagamento",
        "id_cobranca", "nota_fiscal", "codigo_rastreio", "motivo", "historico", "criado_em", "atualizado_em",
    }
    assert gateway.pub.publicados == [
        (
            "eCommerce",
            "pedido.criado",
            {
                "id_pedido": id_pedido,
                "cliente": {"email": EMAIL, "nome": "Ana"},
                "itens": [{"codigo": "B1", "quantidade": 2}],
                "criado_em": pedido["criado_em"],
            },
        )
    ]
    [item] = notificacoes_sse(gateway.hub)
    assert item["dados"]["tipo"] == "pedido.criado"
    assert item["dados"]["mensagem"] == f"Pedido {id_pedido} criado. Aguardando confirmação de estoque."
    assert item["dados"]["pedido"] == pedido and item["dados"]["timestamp"]
    assert gateway.db.obter(id_pedido) == pedido


def test_criar_pedido_normaliza_entrada_e_junta_codigos_repetidos(gateway):
    pedido = criar_pedido(
        gateway,
        email="  Ana@Exemplo.COM ",
        nome="   ",
        itens=[{"codigo": " b1 ", "quantidade": 2}, {"codigo": "c2", "quantidade": 1}, {"codigo": "B1", "quantidade": 3}],
    )
    assert pedido["email"] == EMAIL and pedido["nome"] is None
    assert pedido["itens"] == [{"codigo": "B1", "quantidade": 5}, {"codigo": "C2", "quantidade": 1}]
    assert gateway.pub.publicados[0][2]["itens"] == pedido["itens"]
    assert gateway.pub.publicados[0][2]["cliente"] == {"email": EMAIL, "nome": None}


@pytest.mark.parametrize(
    "corpo",
    [
        {"email": "invalido", "itens": [{"codigo": "B1", "quantidade": 1}]},
        {"itens": [{"codigo": "B1", "quantidade": 1}]},
        {"email": EMAIL, "itens": []},
        {"email": EMAIL},
        {"email": EMAIL, "itens": [{"codigo": "B1", "quantidade": 0}]},
        {"email": EMAIL, "itens": [{"codigo": "B1", "quantidade": 100}]},
        {"email": EMAIL, "itens": [{"codigo": "B1", "quantidade": 60}, {"codigo": "b1", "quantidade": 40}]},
        {"email": EMAIL, "itens": [{"codigo": "  ", "quantidade": 1}]},
        {"email": EMAIL, "itens": [{"codigo": f"X{n}", "quantidade": 1} for n in range(21)]},
        {"email": EMAIL, "nome": "x" * 101, "itens": [{"codigo": "B1", "quantidade": 1}]},
    ],
)
def test_criar_pedido_invalido_responde_422(gateway, corpo):
    resposta = gateway.client.post("/api/pedidos", json=corpo)
    assert resposta.status_code == 422
    assert all(isinstance(erro["msg"], str) and erro["msg"] for erro in resposta.json()["detail"])
    assert gateway.pub.publicados == [] and gateway.hub.pendentes(EMAIL, 0) == []


def test_mensagens_de_validacao_em_portugues(gateway):
    detalhe = gateway.client.post("/api/pedidos", json={"email": "x", "itens": []}).json()["detail"]
    mensagens = {tuple(erro["loc"]): erro["msg"] for erro in detalhe}
    assert mensagens[("body", "email")] == "E-mail inválido."
    assert mensagens[("body", "itens")].startswith("Deve ter pelo menos 1")
    detalhe = gateway.client.post(
        "/api/pedidos", json={"email": EMAIL, "itens": [{"codigo": "B1", "quantidade": 0}]}
    ).json()["detail"]
    assert detalhe[0]["msg"] == "Deve ser maior ou igual a 1."


def test_criar_pedido_com_rabbitmq_fora_responde_503_e_nao_guarda(gateway):
    gateway.pub.falhar = True
    resposta = gateway.client.post(
        "/api/pedidos", json={"email": EMAIL, "itens": [{"codigo": "B1", "quantidade": 1}]}
    )
    assert resposta.status_code == 503
    assert resposta.json()["detail"].startswith("Não foi possível registrar o pedido")
    assert gateway.db.listar_por_email(EMAIL) == []
    assert gateway.hub.pendentes(EMAIL, 0) == []


def test_listar_e_consultar_pedidos(gateway):
    primeiro = criar_pedido(gateway)
    segundo = criar_pedido(gateway, itens=[{"codigo": "C1", "quantidade": 1}])
    criar_pedido(gateway, email="bia@exemplo.com")
    resposta = gateway.client.get("/api/pedidos", params={"email": " ANA@exemplo.com"})
    assert resposta.status_code == 200
    assert [p["id_pedido"] for p in resposta.json()] == [segundo["id_pedido"], primeiro["id_pedido"]]
    assert gateway.client.get("/api/pedidos").status_code == 422
    assert gateway.client.get("/api/pedidos", params={"email": "nada"}).status_code == 422

    resposta = gateway.client.get(f"/api/pedidos/{primeiro['id_pedido'].lower()}")
    assert resposta.status_code == 200 and resposta.json() == primeiro
    resposta = gateway.client.get("/api/pedidos/PEDNAOEXISTE")
    assert resposta.status_code == 404 and resposta.json() == {"detail": "Pedido não encontrado."}


@pytest.mark.parametrize("status", ["CRIADO", "ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO"])
def test_cancelar_pedido_publica_excluido_e_notifica(gateway, status):
    id_pedido = criar_pedido(gateway)["id_pedido"]
    levar_ao_status(id_pedido, status)
    gateway.pub.publicados.clear()

    resposta = gateway.client.delete(f"/api/pedidos/{id_pedido}")
    assert resposta.status_code == 200
    pedido = resposta.json()
    assert pedido["status"] == "CANCELADO" and pedido["status_label"] == "Pedido Cancelado"
    assert pedido["motivo"]
    assert gateway.pub.publicados == [
        ("eCommerce", "pedido.excluido", {"id_pedido": id_pedido, "motivo": "cancelado_pelo_cliente"})
    ]
    ultimo = notificacoes_sse(gateway.hub)[-1]["dados"]
    assert ultimo["tipo"] == "pedido.cancelado"
    assert ultimo["mensagem"] == f"Pedido {id_pedido} cancelado."
    assert ultimo["pedido"] == pedido

    resposta = gateway.client.delete(f"/api/pedidos/{id_pedido}")
    assert resposta.status_code == 409 and "não pode mais ser cancelado" in resposta.json()["detail"]


@pytest.mark.parametrize("status", ["PAGAMENTO_APROVADO", "ENVIADO", "ESTOQUE_INDISPONIVEL", "PAGAMENTO_RECUSADO"])
def test_cancelar_pedido_em_status_final_responde_409(gateway, status):
    id_pedido = criar_pedido(gateway)["id_pedido"]
    levar_ao_status(id_pedido, status)
    publicados = list(gateway.pub.publicados)
    total_sse = len(notificacoes_sse(gateway.hub))

    resposta = gateway.client.delete(f"/api/pedidos/{id_pedido}")
    assert resposta.status_code == 409
    assert gateway.pub.publicados == publicados
    assert len(notificacoes_sse(gateway.hub)) == total_sse
    assert gateway.db.obter(id_pedido)["status"] == status


def test_cancelar_pedido_inexistente_ou_com_rabbitmq_fora(gateway):
    assert gateway.client.delete("/api/pedidos/PEDNAOEXISTE").status_code == 404
    pedido = criar_pedido(gateway)
    gateway.pub.falhar = True
    resposta = gateway.client.delete(f"/api/pedidos/{pedido['id_pedido']}")
    assert resposta.status_code == 503
    assert resposta.json()["detail"].startswith("Não foi possível cancelar o pedido")
    assert gateway.db.obter(pedido["id_pedido"]) == pedido
    assert len(notificacoes_sse(gateway.hub)) == 1


def test_registrar_interesse_publica_inscrever(gateway):
    resposta = gateway.client.post(
        "/api/interesses", json={"email": " Ana@Exemplo.com", "nome": " Ana ", "categorias": ["b", "A", "B"]}
    )
    assert resposta.status_code == 202
    corpo = resposta.json()
    assert corpo["email"] == EMAIL and corpo["categorias"] == ["B", "A"]
    assert "Periféricos" in corpo["mensagem"] and "Computadores e monitores" in corpo["mensagem"]
    assert gateway.pub.publicados == [
        (
            "Promoções",
            "interesse.promocao",
            {"acao": "INSCREVER", "email": EMAIL, "nome": "Ana", "categorias": ["B", "A"]},
        )
    ]


@pytest.mark.parametrize(
    "corpo",
    [
        {"email": EMAIL, "categorias": []},
        {"email": EMAIL, "categorias": ["Z"]},
        {"email": EMAIL},
        {"email": "sem-arroba", "categorias": ["A"]},
        {"email": "ana@exemplo.com/extra", "categorias": ["A"]},
    ],
)
def test_registrar_interesse_invalido_responde_422(gateway, corpo):
    assert gateway.client.post("/api/interesses", json=corpo).status_code == 422
    assert gateway.pub.publicados == []


def test_cancelar_interesse_publica_cancelar(gateway):
    resposta = gateway.client.delete(
        "/api/interesses/Ana@Exemplo.com", params=[("categorias", "a"), ("categorias", "C"), ("categorias", "A")]
    )
    assert resposta.status_code == 202
    assert resposta.json()["categorias"] == ["A", "C"] and resposta.json()["email"] == EMAIL
    resposta = gateway.client.delete(f"/api/interesses/{EMAIL}")
    assert resposta.status_code == 202 and resposta.json()["categorias"] == []
    assert resposta.json()["mensagem"]
    assert gateway.pub.publicados == [
        ("Promoções", "interesse.promocao", {"acao": "CANCELAR", "email": EMAIL, "nome": None, "categorias": ["A", "C"]}),
        ("Promoções", "interesse.promocao", {"acao": "CANCELAR", "email": EMAIL, "nome": None, "categorias": []}),
    ]
    assert gateway.client.delete(f"/api/interesses/{EMAIL}", params={"categorias": "Z"}).status_code == 422
    assert gateway.client.delete("/api/interesses/invalido").status_code == 422


def test_interesse_com_rabbitmq_fora_responde_503(gateway):
    gateway.pub.falhar = True
    assert gateway.client.post("/api/interesses", json={"email": EMAIL, "categorias": ["A"]}).status_code == 503
    assert gateway.client.delete(f"/api/interesses/{EMAIL}").status_code == 503


@pytest.fixture
def servicos(monkeypatch):
    estado = SimpleNamespace(requisicoes=[], respostas={})

    def responder(request):
        estado.requisicoes.append(request)
        resposta = estado.respostas.get(request.url.path)
        if isinstance(resposta, Exception):
            raise resposta
        if resposta is None:
            return httpx.Response(404, json={"detail": "nao encontrado"})
        return resposta

    monkeypatch.setattr(principal, "transporte_http", httpx.MockTransport(responder))
    return estado


def test_produtos_vem_do_ms_estoque(gateway, servicos):
    lista = [{"codigo": "B1", "nome": "Teclado Mecânico", "categoria": "B", "categoria_nome": "Periféricos",
              "preco": 350.0, "estoque": 10, "disponivel": True}]
    servicos.respostas["/produtos"] = httpx.Response(200, json=lista)

    resposta = gateway.client.get("/api/produtos")
    assert resposta.status_code == 200 and resposta.json() == lista
    requisicao = servicos.requisicoes[-1]
    assert str(requisicao.url).startswith(principal.config.URL_ESTOQUE.rstrip("/") + "/produtos")
    assert requisicao.url.params["disponiveis"] == "true"

    gateway.client.get("/api/produtos", params={"incluir_esgotados": "true"})
    assert servicos.requisicoes[-1].url.params["disponiveis"] == "false"


@pytest.mark.parametrize(
    "falha",
    [
        httpx.ConnectError("conexao recusada"),
        httpx.ReadTimeout("demorou"),
        httpx.Response(500, json={"detail": "erro"}),
        httpx.Response(200, content=b"<html>nao e json</html>"),
    ],
)
def test_produtos_com_ms_estoque_fora_responde_503(gateway, servicos, falha):
    servicos.respostas["/produtos"] = falha
    resposta = gateway.client.get("/api/produtos")
    assert resposta.status_code == 503
    assert resposta.json() == {"detail": "MS Estoque indisponível"}


def test_consultas_ao_ms_promocoes(gateway, servicos):
    servicos.respostas[f"/interesses/{EMAIL}"] = httpx.Response(200, json={"email": EMAIL, "categorias": ["A"]})
    promocoes = [{"id_promocao": "PROMO1A2B3C", "codigo": "B1", "desconto": 20}]
    servicos.respostas["/promocoes/recentes"] = httpx.Response(200, json=promocoes)

    resposta = gateway.client.get("/api/interesses/ANA@exemplo.com")
    assert resposta.status_code == 200 and resposta.json() == {"email": EMAIL, "categorias": ["A"]}
    assert str(servicos.requisicoes[-1].url).startswith(principal.config.URL_PROMOCOES.rstrip("/"))
    assert gateway.client.get("/api/promocoes/recentes").json() == promocoes

    servicos.respostas["/promocoes/recentes"] = httpx.ConnectError("fora do ar")
    resposta = gateway.client.get("/api/promocoes/recentes")
    assert resposta.status_code == 503 and resposta.json() == {"detail": "MS Promoções indisponível"}
    servicos.respostas[f"/interesses/{EMAIL}"] = httpx.ConnectError("fora do ar")
    assert gateway.client.get(f"/api/interesses/{EMAIL}").status_code == 503


@pytest.mark.parametrize(
    "email, segmento, encaminhado",
    [
        (EMAIL, "ana%40exemplo.com", b"/interesses/ana@exemplo.com"),
        ("a/b@x.com", "a%2Fb%40x.com", b"/interesses/a%2Fb@x.com"),
    ],
)
def test_interesse_com_email_codificado_no_caminho(gateway, servicos, email, segmento, encaminhado):
    # O frontend usa encodeURIComponent e "/" e valido na parte local do e-mail.
    assert gateway.client.post("/api/interesses", json={"email": email, "categorias": ["A"]}).status_code == 202
    servicos.respostas[f"/interesses/{email}"] = httpx.Response(200, json={"email": email, "categorias": ["A"]})

    resposta = gateway.client.get(f"/api/interesses/{segmento}")
    assert resposta.status_code == 200 and resposta.json() == {"email": email, "categorias": ["A"]}
    assert servicos.requisicoes[-1].url.raw_path == encaminhado

    resposta = gateway.client.delete(f"/api/interesses/{segmento}")
    assert resposta.status_code == 202 and resposta.json()["email"] == email
    assert [dados for _, _, dados in gateway.pub.publicados] == [
        {"acao": "INSCREVER", "email": email, "nome": None, "categorias": ["A"]},
        {"acao": "CANCELAR", "email": email, "nome": None, "categorias": []},
    ]


@pytest.mark.parametrize("caminho", ["/api/interesses/ana@exemplo.com/extra", "/api/interesses/ana@exemplo.com/"])
def test_interesse_com_segmento_extra_no_caminho_responde_422(gateway, servicos, caminho):
    for metodo in ("GET", "DELETE"):
        resposta = gateway.client.request(metodo, caminho)
        assert resposta.status_code == 422
        assert resposta.json()["detail"][0]["msg"] == "E-mail inválido."
    assert servicos.requisicoes == [] and gateway.pub.publicados == []


def test_categorias_saude_e_cors(gateway):
    assert gateway.client.get("/api/categorias").json() == [
        {"codigo": "A", "nome": "Computadores e monitores"},
        {"codigo": "B", "nome": "Periféricos"},
        {"codigo": "C", "nome": "Acessórios"},
    ]
    gateway.hub.inscrever(EMAIL)
    assert gateway.client.get("/api/saude").json() == {"servico": "principal", "status": "ok", "conexoes_sse": 1}
    origem = principal.config.ORIGENS_FRONTEND[0]
    resposta = gateway.client.options(
        "/api/pedidos",
        headers={"Origin": origem, "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type"},
    )
    assert resposta.status_code == 200
    assert resposta.headers["access-control-allow-origin"] == origem


def test_fluxo_aprovado_atualiza_status_e_notifica_o_dono(gateway):
    id_pedido = criar_pedido(gateway)["id_pedido"]
    criar_pedido(gateway, email="bia@exemplo.com")
    levar_ao_status(id_pedido, "ENVIADO")

    pedido = gateway.db.obter(id_pedido)
    assert pedido["status"] == "ENVIADO" and pedido["status_label"] == "Pedido Enviado"
    assert pedido["itens"] == dados_evento("pedido.estoque_ok", id_pedido)["itens"]
    assert pedido["valor_total"] == 700.0
    assert pedido["url_pagamento"] == "http://127.0.0.1:8005/checkout/COB0123456789"
    assert pedido["id_cobranca"] == "COB0123456789"
    assert (pedido["nota_fiscal"], pedido["codigo_rastreio"]) == ("NF-1A2B3C4D", "SD123456789BR")
    assert [h["status"] for h in pedido["historico"]] == [
        "CRIADO", "ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO", "PAGAMENTO_APROVADO", "ENVIADO",
    ]

    sse = notificacoes_sse(gateway.hub)
    assert [item["dados"]["tipo"] for item in sse] == [
        "pedido.criado", "pedido.estoque_ok", "pagamento.pendente", "pagamento.aprovado", "pedido.enviado",
    ]
    assert [item["dados"]["mensagem"] for item in sse[1:]] == [
        f"Estoque Confirmado para o pedido {id_pedido}.",
        f"Pedido {id_pedido} aguardando pagamento.",
        f"Pagamento Aprovado para o pedido {id_pedido}.",
        f"Pedido {id_pedido} Enviado! Nota fiscal NF-1A2B3C4D, rastreio SD123456789BR.",
    ]
    assert [item["dados"]["pedido"]["status"] for item in sse] == [
        "CRIADO", "ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO", "PAGAMENTO_APROVADO", "ENVIADO",
    ]
    assert sse[-1]["dados"]["pedido"] == pedido
    assert sse[2]["dados"]["pedido"]["url_pagamento"] == pedido["url_pagamento"]
    ids = [item["id"] for item in sse]
    assert ids == sorted(ids)
    assert all(item["dados"]["pedido"]["email"] == EMAIL for item in sse)
    assert len(notificacoes_sse(gateway.hub, "bia@exemplo.com")) == 1
    assert "pedido.excluido" not in gateway.pub.chaves()


def test_estoque_indisponivel_republica_excluido_na_reentrega(gateway):
    id_pedido = criar_pedido(gateway)["id_pedido"]
    dados = dados_evento("estoque.indisponivel", id_pedido)
    gateway.pub.falhar = True
    principal.tratar_evento("estoque.indisponivel", dados)
    pedido = gateway.db.obter(id_pedido)
    mensagem = f'Estoque Indisponível para o pedido {id_pedido}: Monitor 69" (solicitado 1, disponível 0).'
    assert pedido["status"] == "ESTOQUE_INDISPONIVEL" and pedido["motivo"] == mensagem
    assert gateway.pub.chaves() == ["pedido.criado"]

    gateway.pub.falhar = False
    principal.tratar_evento("estoque.indisponivel", dados)  # reentrega
    assert gateway.db.obter(id_pedido) == pedido
    assert gateway.pub.publicados[1:] == [
        ("eCommerce", "pedido.excluido", {"id_pedido": id_pedido, "motivo": "estoque_indisponivel"})
    ]
    sse = notificacoes_sse(gateway.hub)
    assert [item["dados"]["tipo"] for item in sse] == ["pedido.criado", "estoque.indisponivel"]
    assert sse[-1]["dados"]["mensagem"] == mensagem


def test_estoque_indisponivel_de_produto_inexistente(gateway):
    id_pedido = criar_pedido(gateway, itens=[{"codigo": "ZZ9", "quantidade": 1}])["id_pedido"]
    principal.tratar_evento(
        "estoque.indisponivel",
        {"id_pedido": id_pedido, "indisponiveis": [{"codigo": "ZZ9", "nome": None, "solicitado": 1, "disponivel": 0}]},
    )
    motivo = gateway.db.obter(id_pedido)["motivo"]
    assert motivo == f"Estoque Indisponível para o pedido {id_pedido}: produto ZZ9 inexistente."


def test_pagamento_recusado_republica_excluido_na_reentrega(gateway):
    id_pedido = criar_pedido(gateway)["id_pedido"]
    levar_ao_status(id_pedido, "AGUARDANDO_PAGAMENTO")
    dados = dados_evento("pagamento.recusado", id_pedido)
    gateway.pub.falhar = True
    principal.tratar_evento("pagamento.recusado", dados)
    pedido = gateway.db.obter(id_pedido)
    assert pedido["status"] == "PAGAMENTO_RECUSADO" and pedido["status_label"] == "Pagamento Recusado"
    assert pedido["motivo"] == "Pagamento recusado pelo cliente no provedor de pagamento"
    assert "pedido.excluido" not in gateway.pub.chaves()

    gateway.pub.falhar = False
    principal.tratar_evento("pagamento.recusado", dados)  # reentrega
    assert gateway.db.obter(id_pedido) == pedido
    excluidos = [p for p in gateway.pub.publicados if p[1] == "pedido.excluido"]
    assert excluidos == [("eCommerce", "pedido.excluido", {"id_pedido": id_pedido, "motivo": "pagamento_recusado"})]
    sse = notificacoes_sse(gateway.hub)
    assert [item["dados"]["tipo"] for item in sse].count("pagamento.recusado") == 1
    assert sse[-1]["dados"]["tipo"] == "pagamento.recusado"
    assert sse[-1]["dados"]["mensagem"] == f"Pagamento Recusado para o pedido {id_pedido}."


def test_pagamento_recusado_sem_cobranca_criada(gateway):
    id_pedido = criar_pedido(gateway)["id_pedido"]
    levar_ao_status(id_pedido, "ESTOQUE_CONFIRMADO")
    principal.tratar_evento(
        "pagamento.recusado",
        {"id_pedido": id_pedido, "id_cobranca": None, "motivo": "Falha ao criar a cobrança no provedor de pagamento"},
    )
    pedido = gateway.db.obter(id_pedido)
    assert pedido["status"] == "PAGAMENTO_RECUSADO" and pedido["id_cobranca"] is None
    assert pedido["motivo"] == "Falha ao criar a cobrança no provedor de pagamento"
    assert gateway.pub.chaves().count("pedido.excluido") == 1


@pytest.mark.parametrize(
    "status, routing_key",
    [
        ("CRIADO", "pagamento.aprovado"),
        ("CRIADO", "pedido.enviado"),
        ("ESTOQUE_CONFIRMADO", "pedido.estoque_ok"),
        ("ESTOQUE_CONFIRMADO", "estoque.indisponivel"),
        ("AGUARDANDO_PAGAMENTO", "pedido.enviado"),
        ("PAGAMENTO_APROVADO", "pagamento.recusado"),
        ("ENVIADO", "pagamento.recusado"),
        ("ESTOQUE_INDISPONIVEL", "pedido.estoque_ok"),
        ("PAGAMENTO_RECUSADO", "pagamento.aprovado"),
    ],
)
def test_transicao_invalida_e_ignorada(gateway, status, routing_key):
    id_pedido = criar_pedido(gateway)["id_pedido"]
    levar_ao_status(id_pedido, status)
    antes = gateway.db.obter(id_pedido)
    publicados = list(gateway.pub.publicados)
    total_sse = len(notificacoes_sse(gateway.hub))

    principal.tratar_evento(routing_key, dados_evento(routing_key, id_pedido))
    assert gateway.db.obter(id_pedido) == antes
    assert gateway.pub.publicados == publicados
    assert len(notificacoes_sse(gateway.hub)) == total_sse


@pytest.mark.parametrize("status", ["CRIADO", "AGUARDANDO_PAGAMENTO"])
def test_eventos_apos_cancelamento_sao_ignorados(gateway, status):
    id_pedido = criar_pedido(gateway)["id_pedido"]
    levar_ao_status(id_pedido, status)
    assert gateway.client.delete(f"/api/pedidos/{id_pedido}").status_code == 200
    total_publicados = len(gateway.pub.publicados)
    for routing_key in ("estoque.indisponivel", "pagamento.aprovado", "pagamento.recusado", "pedido.enviado"):
        principal.tratar_evento(routing_key, dados_evento(routing_key, id_pedido))
    assert gateway.db.obter(id_pedido)["status"] == "CANCELADO"
    assert len(gateway.pub.publicados) == total_publicados
    assert notificacoes_sse(gateway.hub)[-1]["dados"]["tipo"] == "pedido.cancelado"


def test_eventos_desconhecidos_ou_malformados_sao_ignorados(gateway):
    ultimo_id = gateway.hub.ultimo_id()
    principal.tratar_evento("pedido.estoque_ok", dados_evento("pedido.estoque_ok", "PEDDESCONHECIDO"))
    principal.tratar_evento("estoque.indisponivel", {"id_pedido": "PEDDESCONHECIDO", "indisponiveis": ["A2"]})
    principal.tratar_evento("pagamento.recusado", {"motivo": "sem id"})
    principal.tratar_evento("pedido.enviado", ["nao", "e", "dict"])
    principal.tratar_evento("outro.evento", {"id_pedido": "PED1"})
    assert gateway.pub.publicados == []
    assert gateway.hub.ultimo_id() == ultimo_id  # nenhuma notificacao SSE gerada


def test_eventos_sse_de_um_pedido_saem_na_ordem_dos_status(gateway, monkeypatch):
    # estoque_ok chega antes de o POST terminar; a notificacao de criacao sai antes.
    consumidores = []

    class PublicadorComEstoqueRapido(PublicadorFalso):
        def publish(self, exchange, routing_key, data):
            super().publish(exchange, routing_key, data)
            if routing_key == "pedido.criado":
                thread = threading.Thread(
                    target=principal.tratar_evento,
                    args=("pedido.estoque_ok", dados_evento("pedido.estoque_ok", data["id_pedido"])),
                )
                thread.start()
                consumidores.append(thread)
                time.sleep(0.2)

    monkeypatch.setattr(principal, "publisher", PublicadorComEstoqueRapido())
    id_pedido = criar_pedido(gateway)["id_pedido"]
    for thread in consumidores:
        thread.join(5)
    sse = notificacoes_sse(gateway.hub)
    assert [item["dados"]["tipo"] for item in sse] == ["pedido.criado", "pedido.estoque_ok"]
    assert [item["dados"]["pedido"]["status"] for item in sse] == ["CRIADO", "ESTOQUE_CONFIRMADO"]
    assert gateway.db.obter(id_pedido)["status"] == "ESTOQUE_CONFIRMADO"


def test_promocoes_recentes_sao_repassadas_a_todos_os_conectados(gateway):
    gateway.hub.inscrever(EMAIL)
    gateway.hub.inscrever("bia@exemplo.com")
    promocao = {
        "id_promocao": "PROMO1A2B3C", "codigo": "B1", "produto": "Teclado Mecânico", "categoria": "B",
        "categoria_nome": "Periféricos", "desconto": 20, "preco_original": 350.0, "preco_promocional": 280.0,
        "criado_em": datetime.now(timezone.utc).isoformat(),
    }
    principal.tratar_evento("promocao.categoria.B", promocao)
    for email in (EMAIL, "bia@exemplo.com"):
        [item] = gateway.hub.pendentes(email, 0)
        assert item["evento"] == "promocao" and item["dados"] == promocao
    assert gateway.hub.pendentes("desconectado@exemplo.com", 0) == []

    antiga = dict(promocao, criado_em=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
    principal.tratar_evento("promocao.categoria.B", antiga)
    assert len(gateway.hub.pendentes(EMAIL, 0)) == 1


def test_promocao_recente():
    agora = datetime.now(timezone.utc)
    assert principal.promocao_recente({"criado_em": agora.isoformat()})
    assert principal.promocao_recente({"criado_em": (agora - timedelta(minutes=4)).isoformat()})
    assert not principal.promocao_recente({"criado_em": (agora - timedelta(minutes=6)).isoformat()})
    assert principal.promocao_recente({"criado_em": agora.strftime("%Y-%m-%dT%H:%M:%SZ")})
    assert principal.promocao_recente({"criado_em": agora.replace(tzinfo=None).isoformat()})
    assert principal.promocao_recente({"criado_em": "ontem"})
    assert principal.promocao_recente({})


def test_configuracao_do_consumidor_e_importacao_sem_efeitos():
    assert principal.publisher is None and principal.db is None
    assert principal.FILA == "fila.principal"
    assert set(principal.BINDINGS) == {
        ("eCommerce", "pedido.estoque_ok"),
        ("eCommerce", "estoque.indisponivel"),
        ("eCommerce", "pagamento.pendente"),
        ("eCommerce", "pagamento.aprovado"),
        ("eCommerce", "pagamento.recusado"),
        ("eCommerce", "pedido.enviado"),
        ("Promoções", "promocao.categoria.*"),
    }
