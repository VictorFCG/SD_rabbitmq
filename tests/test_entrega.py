# Testes unitarios do MS Entrega: emissao da nota fiscal/rastreio, publicacao de
# pedido.enviado e descarte de eventos repetidos. Nao dependem de RabbitMQ.
import re
import threading
from datetime import datetime

import pytest
from pika.exceptions import AMQPConnectionError

from conftest import carregar_main

entrega = carregar_main("ms_entrega")

APROVADO = {"id_pedido": "PED1A2B3C4D", "id_cobranca": "COB0000000001", "valor": 4500.0}


class PublisherFalso:
    def __init__(self):
        self.publicados = []
        self.falhas = 0

    def publish(self, exchange, routing_key, data):
        if self.falhas > 0:
            self.falhas -= 1
            raise AMQPConnectionError("RabbitMQ fora do ar")
        self.publicados.append((exchange, routing_key, data))


@pytest.fixture
def publicador(monkeypatch):
    monkeypatch.setattr(entrega, "pedidos_enviados", set())
    falso = PublisherFalso()
    monkeypatch.setattr(entrega, "publisher", falso)
    monkeypatch.setenv("ENTREGA_DELAY", "0")
    return falso


def test_importar_nao_conecta_nem_inicia_threads():
    assert entrega.publisher is None
    assert "keepalive-entrega" not in {thread.name for thread in threading.enumerate()}


def test_pagamento_aprovado_publica_pedido_enviado(publicador):
    entrega.handle("pagamento.aprovado", APROVADO)

    assert len(publicador.publicados) == 1
    exchange, routing_key, dados = publicador.publicados[0]
    assert (exchange, routing_key) == ("eCommerce", "pedido.enviado")
    assert set(dados) == {"id_pedido", "nota_fiscal", "codigo_rastreio", "enviado_em"}
    assert dados["id_pedido"] == "PED1A2B3C4D"
    assert re.fullmatch(r"NF-[0-9A-F]{8}", dados["nota_fiscal"])
    assert re.fullmatch(r"SD\d{9}BR", dados["codigo_rastreio"])
    assert datetime.fromisoformat(dados["enviado_em"]).tzinfo is not None
    assert "PED1A2B3C4D" in entrega.pedidos_enviados


def test_cada_pedido_recebe_nota_e_rastreio_proprios(publicador):
    entrega.handle("pagamento.aprovado", APROVADO)
    entrega.handle("pagamento.aprovado", {**APROVADO, "id_pedido": "PED2"})

    primeiro, segundo = (dados for _, _, dados in publicador.publicados)
    assert primeiro["nota_fiscal"] != segundo["nota_fiscal"]
    assert segundo["id_pedido"] == "PED2"


def test_evento_repetido_e_ignorado(publicador, capsys):
    entrega.handle("pagamento.aprovado", APROVADO)
    entrega.handle("pagamento.aprovado", APROVADO)

    assert len(publicador.publicados) == 1
    assert "evento repetido ignorado" in capsys.readouterr().out


def test_pausas_somam_entrega_delay_e_logs(publicador, monkeypatch, capsys):
    pausas = []
    monkeypatch.setattr(entrega.time, "sleep", pausas.append)
    monkeypatch.setenv("ENTREGA_DELAY", "3")
    entrega.handle("pagamento.aprovado", APROVADO)

    assert pausas == [1.5, 1.5]
    saida = capsys.readouterr().out
    dados = publicador.publicados[0][2]
    assert f"[entrega] pedido PED1A2B3C4D - emitindo nota fiscal {dados['nota_fiscal']}" in saida
    assert f"[entrega] pedido PED1A2B3C4D - despachando, rastreio {dados['codigo_rastreio']}" in saida
    assert saida.isascii()


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(None, 2.0), ("", 2.0), ("abc", 2.0), ("-1", 2.0), ("nan", 2.0), ("inf", 2.0), ("0", 0.0), ("0.5", 0.5)],
)
def test_entrega_delay(monkeypatch, valor, esperado):
    if valor is None:
        monkeypatch.delenv("ENTREGA_DELAY", raising=False)
    else:
        monkeypatch.setenv("ENTREGA_DELAY", valor)
    assert entrega.atraso_despacho() == esperado


def test_falha_ao_publicar_nao_marca_o_pedido_como_enviado(publicador):
    publicador.falhas = 1
    with pytest.raises(AMQPConnectionError):
        entrega.handle("pagamento.aprovado", APROVADO)
    assert "PED1A2B3C4D" not in entrega.pedidos_enviados

    entrega.handle("pagamento.aprovado", APROVADO)
    assert [chave for _, chave, _ in publicador.publicados] == ["pedido.enviado"]


def test_outros_eventos_sao_ignorados(publicador):
    entrega.handle("pagamento.recusado", APROVADO)
    entrega.handle("pedido.estoque_ok", APROVADO)
    assert publicador.publicados == []
