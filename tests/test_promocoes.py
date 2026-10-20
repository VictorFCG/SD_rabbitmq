# Testes unitarios do MS Promocoes: repositorio de interesses (SQLite), API de
# e-mail (Resend/Mailpit/console via httpx.MockTransport), tratamento do evento
# interesse.promocao, gerador de promocoes e endpoints REST.
# Nao dependem de RabbitMQ, Docker nem de outros processos.
import json
import re
import threading
from collections import deque
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pika.exceptions import AMQPConnectionError

from conftest import carregar_main

promocoes = carregar_main("ms_promocoes")

import email_api  # noqa: E402 - o mesmo modulo usado pelo main.py
from common import config  # noqa: E402
from common.catalogo import CATEGORIAS, PRODUTOS  # noqa: E402
from common.mq import EXCHANGE_PROMOCOES  # noqa: E402
from interesses_db import InteressesDB  # noqa: E402

CHAVES_PROMOCAO = {
    "id_promocao", "codigo", "produto", "categoria", "categoria_nome",
    "desconto", "preco_original", "preco_promocional", "criado_em",
}
TECLADO = {"B1": {"nome": "Teclado Mecânico", "categoria": "B", "preco": 350.0, "estoque": 10}}
URL_LOJA = "http://loja.teste:5173"


def interesse(acao, email, categorias, nome=None):
    return {"acao": acao, "email": email, "nome": nome, "categorias": categorias}


class EmailFalso:
    def __init__(self):
        self.enviados = []
        self.falhar_para = set()

    def __call__(self, para_email, para_nome, assunto, html, texto):
        self.enviados.append(
            {"para_email": para_email, "para_nome": para_nome, "assunto": assunto,
             "html": html, "texto": texto}
        )
        if para_email in self.falhar_para:
            return False, "HTTP 403: You can only send testing emails to your own email address"
        return True, "id-teste"


class PublisherFalso:
    def __init__(self, erro=None):
        self.publicados = []
        self.erro = erro

    def publish(self, exchange, routing_key, data):
        if self.erro is not None:
            raise self.erro
        self.publicados.append((exchange, routing_key, data))


@pytest.fixture
def db(tmp_path):
    banco = InteressesDB(tmp_path / "data" / "promocoes.db")
    yield banco
    banco.fechar()


@pytest.fixture
def emails(monkeypatch):
    falso = EmailFalso()
    monkeypatch.setattr(email_api, "enviar_email", falso)
    return falso


@pytest.fixture
def servico(monkeypatch, db, emails):
    publisher = PublisherFalso()
    monkeypatch.setattr(promocoes, "db", db)
    monkeypatch.setattr(promocoes, "publisher", publisher)
    monkeypatch.setattr(promocoes, "recentes", deque(maxlen=promocoes.MAX_RECENTES))
    monkeypatch.setattr(config, "URL_FRONTEND", URL_LOJA)
    return publisher


# --- Repositorio de interesses ------------------------------------------------
def test_db_inscrever_normaliza_email_e_soma_categorias(db):
    assert db.inscrever(" Ana@Exemplo.COM ", "Ana", ["B", "A"]) == ["A", "B"]
    assert db.inscrever("ana@exemplo.com", None, ["C", "A"]) == ["A", "B", "C"]
    assert db.categorias_de("ANA@exemplo.com") == ["A", "B", "C"]
    assert db.nome_de("ana@exemplo.com") == "Ana"
    assert db.interessados("C") == [{"email": "ana@exemplo.com", "nome": "Ana"}]
    assert db.categorias_de("ninguem@exemplo.com") == []


def test_db_atualiza_o_nome_somente_quando_informado(db):
    db.inscrever("ana@exemplo.com", "Ana", ["A"])
    db.inscrever("ana@exemplo.com", "Ana Maria", ["B"])
    assert db.interessados("A") == [{"email": "ana@exemplo.com", "nome": "Ana Maria"}]
    db.inscrever("ana@exemplo.com", "   ", ["C"])
    assert db.interessados("C") == [{"email": "ana@exemplo.com", "nome": "Ana Maria"}]


def test_db_cancelar_categorias_ou_todas(db):
    db.inscrever("ana@exemplo.com", "Ana", ["A", "B", "C"])
    db.inscrever("bia@exemplo.com", "Bia", ["A"])
    assert db.cancelar("ANA@exemplo.com", ["B", "Z"]) == ["A", "C"]
    assert db.cancelar("ana@exemplo.com", []) == []
    assert db.cancelar("ana@exemplo.com") == []
    assert db.categorias_de("bia@exemplo.com") == ["A"]
    assert db.interessados("A") == [{"email": "bia@exemplo.com", "nome": "Bia"}]


def test_db_persiste_no_arquivo(tmp_path):
    caminho = tmp_path / "pasta" / "interesses.db"
    banco = InteressesDB(caminho)
    banco.inscrever("ana@exemplo.com", "Ana", ["A"])
    banco.fechar()
    reaberto = InteressesDB(caminho)
    try:
        assert reaberto.categorias_de("ana@exemplo.com") == ["A"]
        assert reaberto.nome_de("ana@exemplo.com") == "Ana"
    finally:
        reaberto.fechar()


def test_db_suporta_varias_threads(db):
    def inscrever_varios(indice):
        for n in range(20):
            db.inscrever(f"cliente{indice}-{n}@exemplo.com", None, ["A", "B"])
            db.categorias_de(f"cliente{indice}-{n}@exemplo.com")

    threads = [threading.Thread(target=inscrever_varios, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(db.interessados("A")) == 160
    assert len(db.interessados("B")) == 160


# --- API de e-mail ------------------------------------------------------------
def responder(status, corpo=None, cabecalhos=None):
    return lambda: httpx.Response(status, json=corpo, headers=cabecalhos)


def falhar(excecao):
    def passo():
        raise excecao
    return passo


def usar_transporte(monkeypatch, *passos):
    # Cada requisicao recebe o proximo passo (o ultimo se repete).
    requisicoes = []

    def tratar(request):
        requisicoes.append(request)
        return passos[min(len(requisicoes), len(passos)) - 1]()

    monkeypatch.setattr(email_api, "transporte", httpx.MockTransport(tratar))
    return requisicoes


@pytest.fixture
def esperas(monkeypatch):
    registradas = []
    monkeypatch.setattr(email_api, "_esperar", registradas.append)
    monkeypatch.setattr(email_api, "_ultima_chamada_resend", float("-inf"))
    return registradas


@pytest.fixture
def resend(monkeypatch, esperas):
    monkeypatch.setattr(config, "EMAIL_PROVEDOR", "resend")
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_teste_123")
    monkeypatch.setattr(config, "EMAIL_REMETENTE", "Loja SD <onboarding@resend.dev>")
    return esperas


def enviar_teste():
    return email_api.enviar_email("ana@exemplo.com", "Ana", "Promoção", "<p>oi</p>", "oi")


@pytest.mark.parametrize(
    "escolhido, chave, esperado",
    [
        ("", "", "mailpit"),
        ("", "re_x", "resend"),
        ("console", "re_x", "console"),
        ("mailpit", "re_x", "mailpit"),
        ("resend", "", "resend"),
        (" Console ", "", "console"),
        ("sendgrid", "", "mailpit"),
        (None, "", "mailpit"),
    ],
)
def test_provedor(monkeypatch, escolhido, chave, esperado):
    monkeypatch.setattr(config, "EMAIL_PROVEDOR", escolhido)
    monkeypatch.setattr(config, "RESEND_API_KEY", chave)
    assert email_api.provedor() == esperado


def test_resend_envia_com_token_e_user_agent(monkeypatch, resend):
    requisicoes = usar_transporte(monkeypatch, responder(200, {"id": "49a3999c"}))
    assert enviar_teste() == (True, "49a3999c")
    (req,) = requisicoes
    assert req.method == "POST"
    assert str(req.url) == "https://api.resend.com/emails"
    assert req.headers["authorization"] == "Bearer re_teste_123"
    assert req.headers["user-agent"] == "trab2-sd-ecommerce/1.0"
    assert req.headers["content-type"] == "application/json"
    assert json.loads(req.content) == {
        "from": "Loja SD <onboarding@resend.dev>",
        "to": ["ana@exemplo.com"],
        "subject": "Promoção",
        "html": "<p>oi</p>",
        "text": "oi",
    }


def test_resend_espaca_as_chamadas(monkeypatch, resend):
    usar_transporte(monkeypatch, responder(200, {"id": "x"}))
    enviar_teste()
    enviar_teste()
    assert len(resend) == 1
    assert 0 < resend[0] <= email_api.INTERVALO_RESEND


def test_resend_repete_apos_429_respeitando_retry_after(monkeypatch, resend):
    requisicoes = usar_transporte(
        monkeypatch,
        responder(429, {"name": "rate_limit_exceeded", "message": "Too many requests"},
                  {"retry-after": "2"}),
        responder(200, {"id": "abc"}),
    )
    assert enviar_teste() == (True, "abc")
    assert len(requisicoes) == 2
    assert 2.0 in resend


def test_resend_desiste_apos_duas_repeticoes(monkeypatch, resend):
    requisicoes = usar_transporte(monkeypatch, responder(429, {"message": "Too many requests"}))
    ok, detalhe = enviar_teste()
    assert not ok
    assert detalhe == "HTTP 429: Too many requests"
    assert len(requisicoes) == 3
    assert resend.count(1.0) == 2  # sem Retry-After espera 1 s


def test_resend_nao_repete_quando_a_cota_acabou(monkeypatch, resend):
    requisicoes = usar_transporte(
        monkeypatch,
        responder(429, {"name": "daily_quota_exceeded", "message": "Daily quota reached"}),
    )
    assert enviar_teste() == (False, "HTTP 429: Daily quota reached")
    assert len(requisicoes) == 1


def test_resend_devolve_a_mensagem_de_erro(monkeypatch, resend):
    mensagem = "You can only send testing emails to your own email address (dono@exemplo.com)."
    usar_transporte(
        monkeypatch,
        responder(403, {"statusCode": 403, "name": "validation_error", "message": mensagem}),
    )
    assert enviar_teste() == (False, f"HTTP 403: {mensagem}")


def test_resend_sem_chave_nao_chama_a_api(monkeypatch, resend):
    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    requisicoes = usar_transporte(monkeypatch, responder(200, {"id": "x"}))
    ok, detalhe = enviar_teste()
    assert not ok and "RESEND_API_KEY" in detalhe
    assert requisicoes == []


@pytest.mark.parametrize(
    "excecao, trecho",
    [
        (httpx.ConnectError("conexao recusada"), "ConnectError"),
        (httpx.ReadTimeout("tempo esgotado"), "ReadTimeout"),
        (RuntimeError("inesperado"), "erro inesperado"),
    ],
)
def test_erros_no_envio_nunca_lancam_excecao(monkeypatch, resend, excecao, trecho):
    usar_transporte(monkeypatch, falhar(excecao))
    ok, detalhe = enviar_teste()
    assert not ok and trecho in detalhe


def test_mailpit_envia_pela_api_http(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVEDOR", "mailpit")
    monkeypatch.setattr(config, "URL_MAILPIT", "http://mailpit.teste:8025/")
    monkeypatch.setattr(config, "EMAIL_REMETENTE", "Loja SD <onboarding@resend.dev>")
    requisicoes = usar_transporte(monkeypatch, responder(200, {"ID": "m1"}))
    assert enviar_teste() == (True, "m1")
    assert email_api.enviar_email("bia@exemplo.com", None, "A", "<p>a</p>", "a")[0]
    primeira, segunda = requisicoes
    assert str(primeira.url) == "http://mailpit.teste:8025/api/v1/send"
    assert json.loads(primeira.content) == {
        "From": {"Email": "onboarding@resend.dev", "Name": "Loja SD"},
        "To": [{"Email": "ana@exemplo.com", "Name": "Ana"}],
        "Subject": "Promoção",
        "HTML": "<p>oi</p>",
        "Text": "oi",
    }
    assert json.loads(segunda.content)["To"] == [{"Email": "bia@exemplo.com", "Name": ""}]


def test_mailpit_devolve_a_mensagem_de_erro(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVEDOR", "mailpit")
    usar_transporte(monkeypatch, responder(400, {"Error": "error building message: from not set"}))
    assert enviar_teste() == (False, "HTTP 400: error building message: from not set")


def test_console_imprime_o_email_em_ascii(monkeypatch, capsys):
    monkeypatch.setattr(config, "EMAIL_PROVEDOR", "console")
    ok, _ = email_api.enviar_email(
        "ana@exemplo.com", "Ana", "Promoção: Teclado Mecânico", "<p>x</p>",
        "Olá!\nPeriféricos por R$ 280,00",
    )
    saida = capsys.readouterr().out
    assert ok
    assert saida.isascii()
    assert "Promocao: Teclado Mecanico" in saida
    assert "Perifericos por R$ 280,00" in saida


def test_texto_ascii_remove_acentos():
    assert email_api.texto_ascii("Promoção Periféricos Acessórios") == "Promocao Perifericos Acessorios"


# --- interesse.promocao -------------------------------------------------------
def test_importar_o_main_nao_tem_efeitos_colaterais():
    assert promocoes.publisher is None
    assert promocoes.db is None
    assert promocoes.app.title == "MS Promoções"
    nomes = {thread.name for thread in threading.enumerate()}
    assert "gerador-promocoes" not in nomes
    assert "consumer-fila.promocoes" not in nomes
    assert "keepalive-promocoes" not in nomes


def test_inscrever_grava_e_envia_confirmacao(servico, db, emails, capsys):
    promocoes.handle(
        "interesse.promocao",
        interesse("INSCREVER", " Ana@Exemplo.COM ", ["b", "A", "Z", "A"], "Ana"),
    )
    assert db.categorias_de("ana@exemplo.com") == ["A", "B"]
    (enviado,) = emails.enviados
    assert enviado["para_email"] == "ana@exemplo.com"
    assert enviado["para_nome"] == "Ana"
    assert enviado["assunto"].startswith("Inscrição confirmada")
    for corpo in (enviado["texto"], enviado["html"]):
        assert "Computadores e monitores (A)" in corpo
        assert "Periféricos (B)" in corpo
        assert "Acessórios" not in corpo
        assert URL_LOJA in corpo
        assert "aba Promoções" in corpo
    assert "Olá, Ana!" in enviado["texto"]
    assert capsys.readouterr().out.isascii()


def test_nova_inscricao_soma_categorias_e_lista_todas(servico, db, emails):
    promocoes.handle("interesse.promocao", interesse("INSCREVER", "ana@exemplo.com", ["A"], "Ana"))
    promocoes.handle("interesse.promocao", interesse("INSCREVER", "ana@exemplo.com", ["C"]))
    assert db.categorias_de("ana@exemplo.com") == ["A", "C"]
    ultimo = emails.enviados[-1]
    assert "Computadores e monitores (A)" in ultimo["texto"]
    assert "Acessórios (C)" in ultimo["texto"]
    assert ultimo["para_nome"] == "Ana"


@pytest.mark.parametrize(
    "data",
    [
        interesse("INSCREVER", "ana@exemplo.com", []),
        interesse("INSCREVER", "ana@exemplo.com", ["Z", "1"]),
        interesse("INSCREVER", "ana@exemplo.com", None),
        interesse("INSCREVER", "sem-arroba", ["A"]),
        interesse("INSCREVER", "ana@exemplo", ["A"]),
        interesse("INSCREVER", "ana @exemplo.com", ["A"]),
        interesse("INSCREVER", "", ["A"]),
        interesse("INSCREVER", "a" * 250 + "@exemplo.com", ["A"]),
        interesse("ASSINAR", "ana@exemplo.com", ["A"]),
        {"acao": "INSCREVER", "categorias": ["A"]},
        ["nao", "e", "objeto"],
        None,
    ],
)
def test_interesse_invalido_e_ignorado(servico, db, emails, data, capsys):
    promocoes.handle("interesse.promocao", data)
    assert db.categorias_de("ana@exemplo.com") == []
    assert db.interessados("A") == []
    assert emails.enviados == []
    assert capsys.readouterr().out.isascii()


def test_outras_routing_keys_sao_ignoradas(servico, db, emails):
    promocoes.handle("promocao.categoria.A", interesse("INSCREVER", "ana@exemplo.com", ["A"]))
    assert db.categorias_de("ana@exemplo.com") == []
    assert emails.enviados == []


def test_cancelar_parte_das_categorias_envia_inscricao_atualizada(servico, db, emails):
    db.inscrever("ana@exemplo.com", "Ana", ["A", "B", "C"])
    promocoes.handle("interesse.promocao", interesse("CANCELAR", "ana@exemplo.com", ["B"]))
    assert db.categorias_de("ana@exemplo.com") == ["A", "C"]
    (enviado,) = emails.enviados
    assert enviado["assunto"].startswith("Inscrição atualizada")
    assert enviado["para_nome"] == "Ana"
    removidas, restantes = enviado["texto"].split("Continua recebendo")
    assert "Periféricos (B)" in removidas
    assert "Computadores e monitores (A)" in restantes and "Acessórios (C)" in restantes
    assert "Periféricos" not in restantes


@pytest.mark.parametrize("categorias", [[], None])
def test_cancelar_sem_categorias_remove_todas(servico, db, emails, categorias):
    db.inscrever("ana@exemplo.com", "Ana", ["A", "B"])
    promocoes.handle("interesse.promocao", interesse("CANCELAR", "ANA@exemplo.com", categorias))
    assert db.categorias_de("ana@exemplo.com") == []
    (enviado,) = emails.enviados
    assert enviado["para_email"] == "ana@exemplo.com"
    assert enviado["assunto"].startswith("Inscrição cancelada")
    assert "Olá, Ana!" in enviado["texto"]


def test_cancelar_a_ultima_categoria_envia_inscricao_cancelada(servico, db, emails):
    db.inscrever("ana@exemplo.com", None, ["B"])
    promocoes.handle("interesse.promocao", interesse("CANCELAR", "ana@exemplo.com", ["B"]))
    (enviado,) = emails.enviados
    assert enviado["assunto"].startswith("Inscrição cancelada")
    assert enviado["texto"].startswith("Olá!")


def test_cancelar_so_com_categorias_invalidas_nao_remove_nada(servico, db, emails):
    db.inscrever("ana@exemplo.com", "Ana", ["A", "B"])
    promocoes.handle("interesse.promocao", interesse("CANCELAR", "ana@exemplo.com", ["Z"]))
    assert db.categorias_de("ana@exemplo.com") == ["A", "B"]
    assert emails.enviados == []


def test_cancelar_sem_inscricao_nao_envia_email(servico, db, emails):
    db.inscrever("ana@exemplo.com", "Ana", ["A"])
    promocoes.handle("interesse.promocao", interesse("CANCELAR", "ninguem@exemplo.com", []))
    promocoes.handle("interesse.promocao", interesse("CANCELAR", "ana@exemplo.com", ["C"]))
    assert emails.enviados == []
    assert db.categorias_de("ana@exemplo.com") == ["A"]


def test_falha_no_envio_da_confirmacao_vai_para_o_log(servico, db, emails, capsys):
    emails.falhar_para.add("ana@exemplo.com")
    promocoes.handle("interesse.promocao", interesse("INSCREVER", "ana@exemplo.com", ["A"]))
    assert db.categorias_de("ana@exemplo.com") == ["A"]
    saida = capsys.readouterr().out
    assert "falha ao enviar" in saida and "403" in saida
    assert saida.isascii()


def test_confirmacao_pelo_provedor_console(monkeypatch, db, capsys):
    monkeypatch.setattr(promocoes, "db", db)
    monkeypatch.setattr(config, "EMAIL_PROVEDOR", "console")
    promocoes.handle("interesse.promocao", interesse("INSCREVER", "ana@exemplo.com", ["A"], "Ana"))
    saida = capsys.readouterr().out
    assert "Inscricao confirmada" in saida
    assert "Computadores e monitores (A)" in saida
    assert saida.isascii()


# --- Gerador de promocoes -----------------------------------------------------
def test_gerar_promocao_publica_e_avisa_os_inscritos(servico, db, emails, monkeypatch, capsys):
    monkeypatch.setattr(promocoes, "PRODUTOS", TECLADO)
    monkeypatch.setattr(promocoes, "DESCONTOS", [20])
    db.inscrever("ana@exemplo.com", "Ana", ["B"])
    db.inscrever("bia@exemplo.com", None, ["A", "B"])
    db.inscrever("caio@exemplo.com", "Caio", ["A", "C"])

    promocao = promocoes.gerar_promocao()

    assert servico.publicados == [(EXCHANGE_PROMOCOES, "promocao.categoria.B", promocao)]
    assert set(promocao) == CHAVES_PROMOCAO
    assert re.fullmatch(r"PROMO[0-9A-F]{6}", promocao["id_promocao"])
    assert promocao["codigo"] == "B1"
    assert promocao["produto"] == "Teclado Mecânico"
    assert promocao["categoria"] == "B"
    assert promocao["categoria_nome"] == "Periféricos"
    assert promocao["desconto"] == 20
    assert promocao["preco_original"] == 350.0
    assert promocao["preco_promocional"] == 280.0
    assert datetime.fromisoformat(promocao["criado_em"]).tzinfo is not None

    assert sorted(e["para_email"] for e in emails.enviados) == ["ana@exemplo.com", "bia@exemplo.com"]
    enviado = next(e for e in emails.enviados if e["para_email"] == "ana@exemplo.com")
    assert enviado["assunto"] == "Promoção: Teclado Mecânico com 20% OFF"
    for trecho in ("Teclado Mecânico", "Periféricos", "R$ 350,00", "R$ 280,00", URL_LOJA,
                   "aba Promoções"):
        assert trecho in enviado["texto"]
        assert trecho in enviado["html"]
    assert promocoes.listar_recentes() == [promocao]
    saida = capsys.readouterr().out
    assert "promocao.categoria.B: Teclado Mecanico 20%" in saida
    assert "2 e-mail(s) enviado(s)" in saida
    assert saida.isascii()


def test_sortear_promocao_usa_o_catalogo():
    for _ in range(60):
        promocao = promocoes.sortear_promocao()
        produto = PRODUTOS[promocao["codigo"]]
        assert promocao["produto"] == produto["nome"]
        assert promocao["categoria"] == produto["categoria"]
        assert promocao["categoria_nome"] == CATEGORIAS[produto["categoria"]]
        assert promocao["desconto"] in [5, 10, 15, 20, 30, 50]
        esperado = round(produto["preco"] * (1 - promocao["desconto"] / 100), 2)
        assert promocao["preco_promocional"] == esperado


def test_falha_ao_publicar_nao_impede_os_emails(servico, db, emails, monkeypatch, capsys):
    monkeypatch.setattr(promocoes, "publisher", PublisherFalso(AMQPConnectionError("fora")))
    monkeypatch.setattr(promocoes, "PRODUTOS", TECLADO)
    db.inscrever("ana@exemplo.com", "Ana", ["B"])
    promocao = promocoes.gerar_promocao()
    assert [e["para_email"] for e in emails.enviados] == ["ana@exemplo.com"]
    assert promocoes.listar_recentes() == [promocao]
    saida = capsys.readouterr().out
    assert "falha ao publicar promocao.categoria.B" in saida
    assert saida.isascii()


def test_falhas_de_envio_sao_contadas(servico, db, emails, monkeypatch, capsys):
    monkeypatch.setattr(promocoes, "PRODUTOS", TECLADO)
    db.inscrever("ana@exemplo.com", "Ana", ["B"])
    db.inscrever("bia@exemplo.com", "Bia", ["B"])
    emails.falhar_para.add("bia@exemplo.com")
    promocoes.gerar_promocao()
    saida = capsys.readouterr().out
    assert "1 e-mail(s) enviado(s), 1 falha(s)" in saida
    assert "falha ao enviar" in saida and "bia@exemplo.com" in saida


def test_html_do_email_escapa_os_dados(servico, db, emails, monkeypatch):
    monitor = {"A2": {"nome": 'Monitor 69"', "categoria": "A", "preco": 1500.0, "estoque": 0}}
    monkeypatch.setattr(promocoes, "PRODUTOS", monitor)
    monkeypatch.setattr(promocoes, "DESCONTOS", [10])
    db.inscrever("ana@exemplo.com", "<b>Ana</b>", ["A"])
    promocoes.gerar_promocao()
    (enviado,) = emails.enviados
    assert "&lt;b&gt;Ana&lt;/b&gt;" in enviado["html"]
    assert "<b>Ana</b>" not in enviado["html"]
    assert "Monitor 69&quot;" in enviado["html"]
    assert 'Monitor 69"' in enviado["texto"]
    assert "R$ 1.500,00" in enviado["texto"] and "R$ 1.350,00" in enviado["texto"]


def test_recentes_guarda_as_ultimas_20_mais_nova_primeiro(servico):
    geradas = [promocoes.gerar_promocao() for _ in range(25)]
    assert promocoes.listar_recentes() == list(reversed(geradas[-20:]))


def test_gerador_continua_apos_erros(monkeypatch, capsys):
    parar = threading.Event()
    chamadas = []

    def gerar():
        chamadas.append(1)
        if len(chamadas) < 3:
            raise RuntimeError("falha simulada")
        parar.set()

    monkeypatch.setattr(promocoes, "gerar_promocao", gerar)
    monkeypatch.setattr(promocoes, "ATRASO_PRIMEIRA_PROMOCAO", 0)
    monkeypatch.setattr(promocoes, "INTERVALO_MINIMO", 0.01)
    monkeypatch.setattr(config, "PROMOCAO_INTERVALO", 0)
    thread = threading.Thread(target=promocoes.gerar_promocoes, args=(parar,), daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(chamadas) == 3
    assert "erro ao gerar promocao" in capsys.readouterr().out


@pytest.mark.parametrize(
    "configurado, esperado",
    [(30.0, 30.0), (2.5, 2.5), (0.0, 1.0), (-5.0, 1.0), (float("nan"), 1.0),
     (float("inf"), threading.TIMEOUT_MAX)],
)
def test_intervalo_entre_promocoes_e_sempre_valido(monkeypatch, configurado, esperado):
    monkeypatch.setattr(config, "PROMOCAO_INTERVALO", configurado)
    assert promocoes.intervalo_promocoes() == esperado


def test_gerador_para_quando_sinalizado(monkeypatch):
    parar = threading.Event()
    parar.set()
    monkeypatch.setattr(promocoes, "gerar_promocao", lambda: pytest.fail("nao deveria gerar"))
    promocoes.gerar_promocoes(parar)


@pytest.mark.parametrize(
    "valor, esperado",
    [
        (1500, "R$ 1.500,00"),
        (4500.0, "R$ 4.500,00"),
        (45, "R$ 45,00"),
        (38.25, "R$ 38,25"),
        (1234567.891, "R$ 1.234.567,89"),
        (0, "R$ 0,00"),
    ],
)
def test_formatar_reais(valor, esperado):
    assert promocoes.formatar_reais(valor) == esperado


# --- REST ---------------------------------------------------------------------
def test_rest_interesses_normaliza_o_email(servico, db):
    cliente = TestClient(promocoes.app)
    db.inscrever("ana@exemplo.com", "Ana", ["C", "A"])
    resposta = cliente.get("/interesses/ANA@Exemplo.com")
    assert resposta.status_code == 200
    assert resposta.json() == {"email": "ana@exemplo.com", "categorias": ["A", "C"]}
    resposta = cliente.get("/interesses/%20ana%40exemplo.com%20")
    assert resposta.json() == {"email": "ana@exemplo.com", "categorias": ["A", "C"]}
    resposta = cliente.get("/interesses/ninguem@exemplo.com")
    assert resposta.json() == {"email": "ninguem@exemplo.com", "categorias": []}


def test_rest_promocoes_recentes(servico):
    cliente = TestClient(promocoes.app)
    assert cliente.get("/promocoes/recentes").json() == []
    primeira = promocoes.gerar_promocao()
    segunda = promocoes.gerar_promocao()
    resposta = cliente.get("/promocoes/recentes")
    assert resposta.status_code == 200
    assert resposta.json() == [segunda, primeira]


@pytest.mark.parametrize("escolhido, chave, esperado", [("", "", "mailpit"), ("", "re_x", "resend"),
                                                        ("console", "", "console")])
def test_rest_saude(monkeypatch, escolhido, chave, esperado):
    monkeypatch.setattr(config, "EMAIL_PROVEDOR", escolhido)
    monkeypatch.setattr(config, "RESEND_API_KEY", chave)
    resposta = TestClient(promocoes.app).get("/saude")
    assert resposta.status_code == 200
    assert resposta.json() == {"servico": "promocoes", "status": "ok", "provedor_email": esperado}
