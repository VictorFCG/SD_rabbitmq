# Testes unitarios do MS Estoque (sem RabbitMQ, Docker ou outros processos):
# persistencia SQLite (reserva, devolucao, idempotencia, concorrencia), tratamento
# dos eventos pedido.criado/pedido.excluido com um publisher falso, verificacao da
# assinatura do envelope e API REST consultada pelo API Gateway.
import json
import threading
from types import SimpleNamespace

import estoque_db as modulo_db
import pytest
from fastapi.testclient import TestClient
from pika.exceptions import AMQPError

from common.catalogo import CATEGORIAS, PRODUTOS
from common.crypto_utils import build_envelope, generate_keypair
from common.crypto_utils import load_private as carregar_privada
from common.mq import EXCHANGE_ECOMMERCE, Consumer
from conftest import carregar_main
from estoque_db import EstoqueDB

estoque = carregar_main("ms_estoque")

CLIENTE = {"email": "ana@exemplo.com", "nome": "Ana"}


class PublisherFalso:
    def __init__(self):
        self.publicados = []
        self.erro = None

    def publish(self, exchange, routing_key, data):
        if self.erro is not None:
            raise self.erro
        # ida e volta em JSON: garante que o payload e serializavel como no broker
        self.publicados.append((exchange, routing_key, json.loads(json.dumps(data))))


def produto_esperado(codigo, estoque_atual=None):
    p = PRODUTOS[codigo]
    quantidade = p["estoque"] if estoque_atual is None else estoque_atual
    return {
        "codigo": codigo,
        "nome": p["nome"],
        "categoria": p["categoria"],
        "categoria_nome": CATEGORIAS[p["categoria"]],
        "preco": p["preco"],
        "estoque": quantidade,
        "disponivel": quantidade > 0,
    }


def pedido_criado(id_pedido, itens, cliente=CLIENTE):
    return {"id_pedido": id_pedido, "cliente": cliente, "itens": itens, "criado_em": "2026-10-19T12:00:00+00:00"}


@pytest.fixture
def db(tmp_path):
    banco = EstoqueDB(tmp_path / "data" / "estoque.db")
    yield banco
    banco.fechar()


@pytest.fixture
def publisher(db, monkeypatch):
    falso = PublisherFalso()
    monkeypatch.setattr(estoque, "db", db)
    monkeypatch.setattr(estoque, "publisher", falso)
    return falso


@pytest.fixture
def cliente_http(publisher):
    return TestClient(estoque.app)


# --- Persistencia (EstoqueDB) --------------------------------------------------


def test_cria_pasta_e_popula_com_o_catalogo(tmp_path):
    caminho = tmp_path / "nova" / "pasta" / "estoque.db"
    banco = EstoqueDB(caminho)
    try:
        assert caminho.is_file()
        assert banco.listar() == [produto_esperado(codigo) for codigo in sorted(PRODUTOS)]
        assert banco.estoque_atual() == {codigo: p["estoque"] for codigo, p in sorted(PRODUTOS.items())}
    finally:
        banco.fechar()


def test_listar_apenas_disponiveis(db):
    disponiveis = db.listar(apenas_disponiveis=True)
    assert [p["codigo"] for p in disponiveis] == sorted(c for c, p in PRODUTOS.items() if p["estoque"] > 0)
    assert "A2" not in {p["codigo"] for p in disponiveis}
    assert all(p["disponivel"] and p["estoque"] > 0 for p in disponiveis)


def test_obter_produto(db):
    assert db.obter("B1") == produto_esperado("B1")
    assert db.obter(" b1 ") == produto_esperado("B1")
    assert db.obter("ZZ9") is None
    assert db.obter(None) is None


def test_reservar_baixa_o_estoque_e_enriquece_os_itens(db):
    resultado = db.reservar("PED1", [{"codigo": "A1", "quantidade": 2}, {"codigo": "C2", "quantidade": 1}])
    assert resultado == {
        "ok": True,
        "itens": [
            {"codigo": "A1", "nome": "Notebook Gamer", "categoria": "A", "quantidade": 2,
             "preco_unitario": 4500.0, "subtotal": 9000.0},
            {"codigo": "C2", "nome": "Cabo HDMI", "categoria": "C", "quantidade": 1,
             "preco_unitario": 30.0, "subtotal": 30.0},
        ],
        "valor_total": 9030.0,
    }
    assert db.estoque_atual()["A1"] == 1
    assert db.estoque_atual()["C2"] == 6
    reserva = db.obter_reserva("PED1")
    assert reserva["itens"] == resultado["itens"]
    assert reserva["valor_total"] == 9030.0
    assert reserva["criado_em"].endswith("+00:00")


def test_reserva_idempotente_nao_baixa_de_novo(db):
    primeira = db.reservar("PED1", [{"codigo": "B1", "quantidade": 4}])
    segunda = db.reservar("PED1", [{"codigo": "B1", "quantidade": 4}])
    assert primeira == segunda
    assert db.estoque_atual()["B1"] == 6


def test_reserva_repetida_devolve_os_itens_gravados(db):
    primeira = db.reservar("PED1", [{"codigo": "B2", "quantidade": 1}])
    assert db.reservar("PED1", [{"codigo": "A1", "quantidade": 3}]) == primeira
    assert db.estoque_atual()["A1"] == 3
    assert db.estoque_atual()["B2"] == 7


def test_consolida_codigos_repetidos(db):
    resultado = db.reservar("PED1", [{"codigo": "B1", "quantidade": 2}, {"codigo": " b1", "quantidade": 3}])
    assert resultado["ok"] is True
    assert [(i["codigo"], i["quantidade"], i["subtotal"]) for i in resultado["itens"]] == [("B1", 5, 1750.0)]
    assert resultado["valor_total"] == 1750.0
    assert db.estoque_atual()["B1"] == 5


def test_codigos_repetidos_acima_do_estoque_ficam_indisponiveis(db):
    resultado = db.reservar("PED1", [{"codigo": "A1", "quantidade": 2}, {"codigo": "A1", "quantidade": 2}])
    assert resultado == {
        "ok": False,
        "indisponiveis": [{"codigo": "A1", "nome": "Notebook Gamer", "solicitado": 4, "disponivel": 3}],
    }
    assert db.estoque_atual()["A1"] == 3
    assert db.obter_reserva("PED1") is None


def test_produto_inexistente_fica_indisponivel(db):
    resultado = db.reservar("PED1", [{"codigo": "ZZ9", "quantidade": 1}])
    assert resultado == {
        "ok": False,
        "indisponiveis": [{"codigo": "ZZ9", "nome": None, "solicitado": 1, "disponivel": 0}],
    }


def test_produto_esgotado_fica_indisponivel(db):
    resultado = db.reservar("PED1", [{"codigo": "A2", "quantidade": 1}])
    assert resultado == {
        "ok": False,
        "indisponiveis": [{"codigo": "A2", "nome": "Monitor 69\"", "solicitado": 1, "disponivel": 0}],
    }


@pytest.mark.parametrize("quantidade", [0, -1, "2", 1.5, 2.0, True, None, [1]])
def test_quantidade_invalida_fica_indisponivel(db, quantidade):
    resultado = db.reservar("PED1", [{"codigo": "B1", "quantidade": quantidade}])
    assert resultado == {
        "ok": False,
        "indisponiveis": [
            {"codigo": "B1", "nome": "Teclado Mecânico", "solicitado": quantidade, "disponivel": 10}
        ],
    }
    assert db.estoque_atual()["B1"] == 10


def test_quantidade_invalida_em_codigo_repetido_invalida_o_codigo(db):
    resultado = db.reservar("PED1", [{"codigo": "B1", "quantidade": 2}, {"codigo": "B1", "quantidade": 0}])
    assert resultado["ok"] is False
    assert resultado["indisponiveis"] == [
        {"codigo": "B1", "nome": "Teclado Mecânico", "solicitado": 0, "disponivel": 10}
    ]


def test_item_que_nao_e_objeto_fica_indisponivel(db):
    resultado = db.reservar("PED1", ["A1", {"codigo": 7, "quantidade": 1}])
    assert resultado == {
        "ok": False,
        "indisponiveis": [{"codigo": None, "nome": None, "solicitado": None, "disponivel": 0}],
    }
    assert db.estoque_atual()["A1"] == 3


def test_tudo_ou_nada_quando_algum_item_falta(db):
    antes = db.estoque_atual()
    resultado = db.reservar(
        "PED1", [{"codigo": "A1", "quantidade": 1}, {"codigo": "A2", "quantidade": 1}, {"codigo": "C1", "quantidade": 9}]
    )
    assert resultado["ok"] is False
    assert [i["codigo"] for i in resultado["indisponiveis"]] == ["A2", "C1"]
    assert db.estoque_atual() == antes
    assert db.obter_reserva("PED1") is None


def test_erro_no_meio_da_reserva_desfaz_a_transacao(db, monkeypatch):
    def falhar():
        raise RuntimeError("disco cheio")

    antes = db.estoque_atual()
    monkeypatch.setattr(modulo_db, "_agora", falhar)  # falha no INSERT, depois dos UPDATEs
    with pytest.raises(RuntimeError):
        db.reservar("PED1", [{"codigo": "A1", "quantidade": 1}, {"codigo": "B1", "quantidade": 2}])
    assert db.estoque_atual() == antes
    assert db.obter_reserva("PED1") is None
    monkeypatch.undo()
    assert db.reservar("PED1", [{"codigo": "A1", "quantidade": 1}])["ok"] is True  # conexao segue utilizavel
    assert db.estoque_atual()["A1"] == antes["A1"] - 1


def test_reservar_sem_itens_e_erro(db):
    with pytest.raises(ValueError):
        db.reservar("PED1", [])
    with pytest.raises(ValueError):
        db.reservar("PED1", None)


def test_devolver_repoe_o_estoque_e_apaga_a_reserva(db):
    reservado = db.reservar("PED1", [{"codigo": "A1", "quantidade": 3}, {"codigo": "B2", "quantidade": 2}])
    assert db.estoque_atual()["A1"] == 0
    assert db.devolver("PED1") == reservado["itens"]
    assert db.estoque_atual()["A1"] == 3
    assert db.estoque_atual()["B2"] == 8
    assert db.obter_reserva("PED1") is None
    assert db.devolver("PED1") is None
    assert db.estoque_atual()["A1"] == 3


def test_devolver_pedido_sem_reserva(db):
    antes = db.estoque_atual()
    assert db.devolver("PEDNAOEXISTE") is None
    assert db.estoque_atual() == antes


def test_estoque_e_reservas_persistem_ao_reabrir(tmp_path):
    caminho = tmp_path / "estoque.db"
    banco = EstoqueDB(caminho)
    banco.reservar("PED1", [{"codigo": "A1", "quantidade": 2}])
    banco.fechar()

    reaberto = EstoqueDB(caminho)  # o catalogo nao sobrescreve o estoque gravado
    try:
        assert reaberto.estoque_atual()["A1"] == 1
        assert reaberto.obter_reserva("PED1")["valor_total"] == 9000.0
        assert reaberto.devolver("PED1")[0]["quantidade"] == 2
        assert reaberto.estoque_atual()["A1"] == 3
    finally:
        reaberto.fechar()


def test_reservas_concorrentes_nao_vendem_alem_do_estoque(db):
    resultados = []
    barreira = threading.Barrier(12)

    def comprar(indice):
        barreira.wait()
        resultados.append(db.reservar(f"PED{indice}", [{"codigo": "A1", "quantidade": 1}]))

    threads = [threading.Thread(target=comprar, args=(i,)) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(1 for r in resultados if r["ok"]) == PRODUTOS["A1"]["estoque"]
    assert db.estoque_atual()["A1"] == 0


# --- Eventos (handler do consumidor) ------------------------------------------


def test_importar_o_main_nao_tem_efeitos_colaterais():
    assert estoque.db is None
    assert estoque.publisher is None
    assert estoque.SERVICE == "estoque"


def test_main_liga_consumidor_e_servidor_http(monkeypatch, tmp_path):
    chamadas = {}

    class PublisherDeTeste:
        def __init__(self, service, key_dir):
            chamadas["publisher"] = (service, key_dir)

    class ConsumerDeTeste:
        def __init__(self, service, key_dir, queue, bindings, handler):
            chamadas["consumer"] = (service, key_dir, queue, bindings, handler)

        def start_in_thread(self):
            chamadas["thread"] = True

    def run_de_teste(app, host, port):
        chamadas["uvicorn"] = (app, host, port)
        raise KeyboardInterrupt  # Ctrl+C encerra sem traceback

    monkeypatch.setattr(estoque, "db", None)
    monkeypatch.setattr(estoque, "publisher", None)
    monkeypatch.setattr(estoque, "DB_PATH", tmp_path / "data" / "estoque.db")
    monkeypatch.setattr(estoque, "Publisher", PublisherDeTeste)
    monkeypatch.setattr(estoque, "Consumer", ConsumerDeTeste)
    monkeypatch.setattr(estoque.uvicorn, "run", run_de_teste)

    estoque.main()
    try:
        assert (tmp_path / "data" / "estoque.db").is_file()
        assert isinstance(estoque.publisher, PublisherDeTeste)
        assert chamadas["publisher"] == ("estoque", estoque.KEYS)
        assert chamadas["consumer"] == (
            "estoque",
            estoque.KEYS,
            "fila.estoque",
            [(EXCHANGE_ECOMMERCE, "pedido.criado"), (EXCHANGE_ECOMMERCE, "pedido.excluido")],
            estoque.handle,
        )
        assert chamadas["thread"] is True
        assert chamadas["uvicorn"] == (estoque.app, estoque.config.HTTP_HOST, estoque.config.PORTA_ESTOQUE)
    finally:
        estoque.db.fechar()


def test_pedido_criado_publica_estoque_ok(publisher, db):
    estoque.handle("pedido.criado", pedido_criado("PED1", [{"codigo": "A1", "quantidade": 1}]))
    assert publisher.publicados == [
        (
            EXCHANGE_ECOMMERCE,
            "pedido.estoque_ok",
            {
                "id_pedido": "PED1",
                "cliente": CLIENTE,
                "itens": [
                    {"codigo": "A1", "nome": "Notebook Gamer", "categoria": "A", "quantidade": 1,
                     "preco_unitario": 4500.0, "subtotal": 4500.0}
                ],
                "valor_total": 4500.0,
            },
        )
    ]
    assert db.estoque_atual()["A1"] == 2


def test_pedido_criado_sem_estoque_publica_estoque_indisponivel(publisher, db):
    antes = db.estoque_atual()
    estoque.handle(
        "pedido.criado",
        pedido_criado("PED2", [{"codigo": "A2", "quantidade": 1}, {"codigo": "B1", "quantidade": 1}]),
    )
    assert publisher.publicados == [
        (
            EXCHANGE_ECOMMERCE,
            "estoque.indisponivel",
            {
                "id_pedido": "PED2",
                "indisponiveis": [{"codigo": "A2", "nome": "Monitor 69\"", "solicitado": 1, "disponivel": 0}],
            },
        )
    ]
    assert db.estoque_atual() == antes


def test_pedido_criado_sem_cliente_publica_cliente_nulo(publisher):
    estoque.handle("pedido.criado", {"id_pedido": "PED1", "itens": [{"codigo": "C1", "quantidade": 1}]})
    assert publisher.publicados[0][2]["cliente"] is None


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"itens": [{"codigo": "A1", "quantidade": 1}]},
        {"id_pedido": "", "itens": [{"codigo": "A1", "quantidade": 1}]},
        {"id_pedido": "   ", "itens": [{"codigo": "A1", "quantidade": 1}]},
        {"id_pedido": 123, "itens": [{"codigo": "A1", "quantidade": 1}]},
        {"id_pedido": "PED1"},
        {"id_pedido": "PED1", "itens": []},
        {"id_pedido": "PED1", "itens": {"codigo": "A1", "quantidade": 1}},
        ["PED1"],
        None,
    ],
)
def test_pedido_criado_malformado_e_ignorado(publisher, db, data):
    antes = db.estoque_atual()
    estoque.handle("pedido.criado", data)
    assert publisher.publicados == []
    assert db.estoque_atual() == antes


def test_pedido_excluido_devolve_o_estoque(publisher, db):
    estoque.handle("pedido.criado", pedido_criado("PED1", [{"codigo": "B2", "quantidade": 3}]))
    assert db.estoque_atual()["B2"] == 5
    estoque.handle("pedido.excluido", {"id_pedido": "PED1", "motivo": "pagamento_recusado"})
    assert db.estoque_atual()["B2"] == 8
    assert db.obter_reserva("PED1") is None
    assert [rk for _, rk, _ in publisher.publicados] == ["pedido.estoque_ok"]


def test_pedido_excluido_sem_reserva_nao_altera_nada(publisher, db):
    antes = db.estoque_atual()
    estoque.handle("pedido.excluido", {"id_pedido": "PED9", "motivo": "estoque_indisponivel"})
    estoque.handle("pedido.excluido", {"motivo": "estoque_indisponivel"})
    assert db.estoque_atual() == antes
    assert publisher.publicados == []


def test_fluxo_estoque_indisponivel_seguido_de_pedido_excluido(publisher, db):
    antes = db.estoque_atual()
    estoque.handle("pedido.criado", pedido_criado("PED1", [{"codigo": "A1", "quantidade": 50}]))
    estoque.handle("pedido.excluido", {"id_pedido": "PED1", "motivo": "estoque_indisponivel"})
    assert db.estoque_atual() == antes
    assert [rk for _, rk, _ in publisher.publicados] == ["estoque.indisponivel"]


def test_evento_reentregue_republica_sem_nova_baixa(publisher, db):
    evento = pedido_criado("PED1", [{"codigo": "C1", "quantidade": 2}])
    estoque.handle("pedido.criado", evento)
    estoque.handle("pedido.criado", evento)
    assert len(publisher.publicados) == 2
    assert publisher.publicados[0] == publisher.publicados[1]
    assert db.estoque_atual()["C1"] == 3


@pytest.mark.parametrize("erro", [AMQPError("RabbitMQ fora do ar"), OSError("rede fora do ar")])
def test_falha_ao_publicar_mantem_a_reserva(publisher, db, capsys, erro):
    evento = pedido_criado("PED1", [{"codigo": "A1", "quantidade": 1}])
    publisher.erro = erro
    estoque.handle("pedido.criado", evento)  # nao propaga a excecao
    assert "falha ao publicar pedido.estoque_ok do pedido PED1" in capsys.readouterr().out
    assert db.estoque_atual()["A1"] == 2

    publisher.erro = None  # reentrega do evento: publica sem baixar de novo
    estoque.handle("pedido.criado", evento)
    assert [rk for _, rk, _ in publisher.publicados] == ["pedido.estoque_ok"]
    assert db.estoque_atual()["A1"] == 2


def test_routing_key_desconhecida_e_ignorada(publisher, db):
    antes = db.estoque_atual()
    estoque.handle("pagamento.aprovado", {"id_pedido": "PED1"})
    assert publisher.publicados == []
    assert db.estoque_atual() == antes


def test_logs_somente_em_ascii(publisher, capsys):
    estoque.handle("pedido.criado", pedido_criado("PEDÇ1", [{"codigo": "ÁÇ1", "quantidade": 1}]))
    estoque.handle("pedido.criado", pedido_criado("PED2", [{"codigo": "B1", "quantidade": 1}]))
    estoque.handle("pedido.excluido", {"id_pedido": "PED2", "motivo": "cancelado_pelo_cliente"})
    saida = capsys.readouterr().out
    assert saida.isascii()
    assert "[estoque] pedido PED2 reservado. estoque atual=" in saida
    assert "[estoque] pedido PED2 devolvido. estoque atual=" in saida


def test_evento_assinado_e_verificado_antes_do_handler(publisher, db, tmp_path):
    # Chaves temporarias: o MS Estoque so processa eventos com assinatura valida
    # do produtor (aqui, o MS Principal).
    privada, publica = generate_keypair()
    (tmp_path / "principal_private.pem").write_bytes(privada)
    (tmp_path / "principal_public.pem").write_bytes(publica)
    chave = carregar_privada(tmp_path / "principal_private.pem")
    consumidor = Consumer(
        "estoque",
        tmp_path,
        "fila.estoque",
        [(EXCHANGE_ECOMMERCE, "pedido.criado"), (EXCHANGE_ECOMMERCE, "pedido.excluido")],
        estoque.handle,
    )
    canal = SimpleNamespace(confirmados=[])
    canal.basic_ack = canal.confirmados.append

    adulterado = build_envelope("principal", "pedido.criado", pedido_criado("PED1", [{"codigo": "A1", "quantidade": 1}]), chave)
    adulterado["data"]["itens"][0]["quantidade"] = 3
    consumidor._on_message(canal, SimpleNamespace(delivery_tag=1), None, json.dumps(adulterado).encode())
    assert publisher.publicados == []
    assert db.estoque_atual()["A1"] == 3

    valido = build_envelope("principal", "pedido.criado", pedido_criado("PED1", [{"codigo": "A1", "quantidade": 1}]), chave)
    consumidor._on_message(canal, SimpleNamespace(delivery_tag=2), None, json.dumps(valido).encode())
    assert [rk for _, rk, _ in publisher.publicados] == ["pedido.estoque_ok"]
    assert db.estoque_atual()["A1"] == 2
    assert canal.confirmados == [1, 2]


# --- API REST -------------------------------------------------------------------


def test_get_produtos_lista_todos(cliente_http):
    resposta = cliente_http.get("/produtos")
    assert resposta.status_code == 200
    assert resposta.json() == [produto_esperado(codigo) for codigo in sorted(PRODUTOS)]


def test_get_produtos_disponiveis(cliente_http):
    resposta = cliente_http.get("/produtos", params={"disponiveis": "true"})
    assert resposta.status_code == 200
    assert resposta.json() == [produto_esperado(c) for c in sorted(PRODUTOS) if PRODUTOS[c]["estoque"] > 0]
    assert cliente_http.get("/produtos", params={"disponiveis": "false"}).json() == cliente_http.get("/produtos").json()


def test_get_produtos_reflete_reservas_e_esgotados(cliente_http):
    estoque.handle("pedido.criado", pedido_criado("PED1", [{"codigo": "A1", "quantidade": 3}]))
    todos = {p["codigo"]: p for p in cliente_http.get("/produtos").json()}
    assert todos["A1"] == produto_esperado("A1", estoque_atual=0)
    disponiveis = cliente_http.get("/produtos", params={"disponiveis": "true"}).json()
    assert "A1" not in {p["codigo"] for p in disponiveis}


def test_get_produto_por_codigo(cliente_http):
    assert cliente_http.get("/produtos/B2").json() == produto_esperado("B2")
    assert cliente_http.get("/produtos/b2").json() == produto_esperado("B2")
    resposta = cliente_http.get("/produtos/ZZ9")
    assert resposta.status_code == 404
    assert resposta.json() == {"detail": "Produto ZZ9 não encontrado."}


def test_get_produtos_parametro_invalido(cliente_http):
    assert cliente_http.get("/produtos", params={"disponiveis": "talvez"}).status_code == 422


def test_saude(cliente_http):
    resposta = cliente_http.get("/saude")
    assert resposta.status_code == 200
    assert resposta.json() == {"servico": "estoque", "status": "ok"}


def test_documentacao_openapi(cliente_http):
    esquema = cliente_http.get("/openapi.json").json()
    assert esquema["info"]["title"] == "MS Estoque"
    assert {"/produtos", "/produtos/{codigo}", "/saude"} <= set(esquema["paths"])
