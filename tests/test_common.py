# Testes de common/ (envelopes assinados, leitura do .env) e de setup_keys.py.
import os

import pytest
from Crypto.PublicKey import RSA

import setup_keys
from common import config
from common.console import desativar_edicao_rapida
from common.crypto_utils import build_envelope, generate_keypair, load_private, verify_envelope


@pytest.fixture
def chaves(tmp_path):
    pasta = tmp_path / "keys"
    pasta.mkdir()
    privada, publica = generate_keypair()
    (pasta / "estoque_private.pem").write_bytes(privada)
    (pasta / "estoque_public.pem").write_bytes(publica)
    return pasta


def test_envelope_assinado_por_produtor_conhecido_e_valido(chaves):
    envelope = build_envelope("estoque", "pedido.estoque_ok", {"id_pedido": "P1"},
                              load_private(chaves / "estoque_private.pem"))
    assert verify_envelope(envelope, chaves)


def test_envelope_adulterado_e_descartado(chaves):
    envelope = build_envelope("estoque", "pedido.estoque_ok", {"id_pedido": "P1"},
                              load_private(chaves / "estoque_private.pem"))
    envelope["data"]["id_pedido"] = "P2"
    assert not verify_envelope(envelope, chaves)


@pytest.mark.parametrize("servico", ["c1", "../estoque", "intruso", None, 123, ""])
def test_produtor_desconhecido_e_descartado(chaves, servico):
    envelope = {"service": servico, "routing_key": "x", "data": {}, "Signature": "abc"}
    assert not verify_envelope(envelope, chaves)


def test_campo_service_nao_vira_caminho_de_arquivo(chaves, tmp_path):
    privada, publica = generate_keypair()
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "evil_public.pem").write_bytes(publica)
    chave_atacante = RSA.import_key(privada)
    for servico in (str(fora / "evil"), "../fora/evil"):
        envelope = build_envelope(servico, "pagamento.aprovado", {"id_pedido": "P1"}, chave_atacante)
        assert not verify_envelope(envelope, chaves)


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("30", "30"),
        ("30   # segundos", "30"),
        ("30\t# segundos", "30"),
        ("30# grudado", "30# grudado"),
        ('"abc #def"  # comentario', "abc #def"),
        ("'Loja SD <a@b.com>'", "Loja SD <a@b.com>"),
        ("Loja SD <onboarding@resend.dev>", "Loja SD <onboarding@resend.dev>"),
        ("#tudo comentario", ""),
        ("   ", ""),
    ],
)
def test_valor_do_env(bruto, esperado):
    assert config._valor_env(bruto) == esperado


def test_carregar_env_nao_sobrescreve_o_ambiente(tmp_path, monkeypatch):
    arquivo = tmp_path / ".env"
    arquivo.write_text(
        "# comentario\nexport TRAB2_TESTE_A=1  # fim\nTRAB2_TESTE_B=\"x # y\"\nTRAB2_TESTE_C=do_arquivo\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TRAB2_TESTE_A", raising=False)
    monkeypatch.delenv("TRAB2_TESTE_B", raising=False)
    monkeypatch.setenv("TRAB2_TESTE_C", "do_ambiente")
    config._carregar_env(arquivo)
    assert os.environ["TRAB2_TESTE_A"] == "1"
    assert os.environ["TRAB2_TESTE_B"] == "x # y"
    assert os.environ["TRAB2_TESTE_C"] == "do_ambiente"
    monkeypatch.delenv("TRAB2_TESTE_A")
    monkeypatch.delenv("TRAB2_TESTE_B")


def test_chaves_geradas_uma_vez_e_mantidas(tmp_path, capsys):
    assert not setup_keys.chaves_completas(tmp_path)
    setup_keys.gerar_chaves(tmp_path)
    assert setup_keys.chaves_completas(tmp_path)
    for servico, pasta in setup_keys.PASTAS.items():
        arquivos = sorted(p.name for p in (tmp_path / pasta / "keys").iterdir())
        publicas = sorted(f"{s}_public.pem" for s in setup_keys.PASTAS)
        assert arquivos == sorted(publicas + [f"{servico}_private.pem"])
    assert not (tmp_path / "keys").exists()


def test_chaves_incompletas_ou_inconsistentes_sao_detectadas(tmp_path):
    setup_keys.gerar_chaves(tmp_path)
    publica = tmp_path / "ms_entrega" / "keys" / "estoque_public.pem"
    original = publica.read_bytes()

    publica.unlink()
    assert not setup_keys.chaves_completas(tmp_path)

    publica.write_bytes((tmp_path / "ms_entrega" / "keys" / "pagamento_public.pem").read_bytes())
    assert not setup_keys.chaves_completas(tmp_path)

    publica.write_bytes(original)
    assert setup_keys.chaves_completas(tmp_path)


def test_main_so_gera_quando_falta(tmp_path, monkeypatch):
    geradas = []
    monkeypatch.setattr(setup_keys, "BASE", tmp_path)
    monkeypatch.setattr(setup_keys, "gerar_chaves", lambda base: geradas.append(base))
    monkeypatch.setattr(setup_keys, "chaves_completas", lambda base: True)
    monkeypatch.setattr("sys.argv", ["setup_keys.py"])
    setup_keys.main()
    assert geradas == []
    monkeypatch.setattr("sys.argv", ["setup_keys.py", "--force"])
    setup_keys.main()
    assert geradas == [tmp_path]


def test_desativar_edicao_rapida_e_inofensivo_fora_do_windows():
    desativar_edicao_rapida()
