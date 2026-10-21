# Confere os arquivos do frontend (frontend/) sem precisar do Node. Se houver
# Node e node_modules, roda tambem o tsc e o build do Vite.
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
SRC = FRONTEND / "src"

DEPENDENCIAS = {"react": "19.3.0", "react-dom": "19.3.0"}
DEPENDENCIAS_DEV = {
    "vite": "8.3.3",
    "@vitejs/plugin-react": "6.1.1",
    "typescript": "5.9.3",
    "@types/react": "19.3.0",
    "@types/react-dom": "19.3.0",
    "@types/node": "24.19.1",
}

ROTULOS = {
    "CRIADO": "Pedido Criado",
    "ESTOQUE_CONFIRMADO": "Estoque Confirmado",
    "AGUARDANDO_PAGAMENTO": "Aguardando Pagamento",
    "PAGAMENTO_APROVADO": "Pagamento Aprovado",
    "ENVIADO": "Pedido Enviado",
    "ESTOQUE_INDISPONIVEL": "Estoque Indisponível",
    "PAGAMENTO_RECUSADO": "Pagamento Recusado",
    "CANCELADO": "Pedido Cancelado",
}

URLS_PERMITIDAS = {
    "http://127.0.0.1:8001",  # API Gateway padrao
    "http://localhost:8025",  # link para o Mailpit
    "http://www.w3.org/2000/svg",  # namespace do favicon SVG inline
}


def ler(caminho):
    return (FRONTEND / caminho).read_text(encoding="utf-8")


def fontes():
    return sorted(p for p in SRC.rglob("*") if p.suffix in {".ts", ".tsx", ".css"})


def test_package_json_fixa_versoes_exatas():
    pacote = json.loads(ler("package.json"))
    assert pacote["dependencies"] == DEPENDENCIAS
    assert pacote["devDependencies"] == DEPENDENCIAS_DEV
    assert pacote["scripts"]["dev"] == "vite"
    assert pacote["scripts"]["build"] in ("tsc -b && vite build", "tsc --noEmit && vite build")
    assert pacote["scripts"]["preview"] == "vite preview"


def test_package_lock_acompanha_package_json():
    trava = json.loads(ler("package-lock.json"))
    raiz = trava["packages"][""]
    assert raiz["dependencies"] == DEPENDENCIAS
    assert raiz["devDependencies"] == DEPENDENCIAS_DEV
    for nome, versao in {**DEPENDENCIAS, **DEPENDENCIAS_DEV}.items():
        assert trava["packages"][f"node_modules/{nome}"]["version"] == versao
    origens = {
        pacote["resolved"].split("/")[2]
        for pacote in trava["packages"].values()
        if "resolved" in pacote
    }
    assert origens == {"registry.npmjs.org"}


def test_vite_config_portas():
    config = ler("vite.config.ts")
    servidor = re.search(r"server:\s*\{(.*?)\}", config, re.S).group(1)
    assert re.search(r'host:\s*"127\.0\.0\.1"', servidor)
    assert re.search(r"port:\s*5173\b", servidor)
    assert re.search(r"strictPort:\s*true", servidor)
    previa = re.search(r"preview:\s*\{(.*?)\}", config, re.S).group(1)
    assert re.search(r"port:\s*4173\b", previa)


def test_index_html_pt_br_com_favicon_inline():
    html = ler("index.html")
    assert '<html lang="pt-BR">' in html
    assert 'rel="icon"' in html and 'href="data:image/svg+xml,' in html
    assert '<script type="module" src="/src/main.tsx">' in html
    assert "<title>Loja SD</title>" in html


def test_env_example_aponta_para_o_gateway():
    linhas = [linha.strip() for linha in ler(".env.example").splitlines()]
    assert "VITE_API_URL=http://127.0.0.1:8001" in linhas


def test_sem_recursos_externos():
    for arquivo in [FRONTEND / "index.html", *fontes()]:
        texto = arquivo.read_text(encoding="utf-8")
        for url in re.findall(r"https?://[^\s\"'`)<>]+", texto):
            assert url.rstrip(".,;") in URLS_PERMITIDAS, f"{arquivo.name}: {url}"
        assert "@import" not in texto, arquivo.name
        assert "fonts.googleapis" not in texto, arquivo.name


def test_api_padrao_e_mensagem_de_falha_de_rede():
    api = (SRC / "api.ts").read_text(encoding="utf-8")
    assert 'import.meta.env.VITE_API_URL || "http://127.0.0.1:8001"' in api
    assert '"Não foi possível conectar ao API Gateway (MS Principal)"' in api


def test_rotulos_de_status_do_contrato():
    status = (SRC / "status.ts").read_text(encoding="utf-8")
    bloco = re.search(r"ROTULOS_STATUS[^=]*=\s*\{(.*?)\};", status, re.S).group(1)
    assert dict(re.findall(r'(\w+):\s*"([^"]+)"', bloco)) == ROTULOS


def test_endpoints_rest_e_sse_do_contrato():
    api = (SRC / "api.ts").read_text(encoding="utf-8")
    for trecho in (
        '"/api/produtos?incluir_esgotados=true"',
        '"/api/categorias"',
        '"/api/pedidos"',
        "`/api/pedidos?email=${",
        "`/api/pedidos/${",
        '"/api/interesses"',
        "`/api/interesses/${",
        "categorias=${",
        '"/api/promocoes/recentes"',
        "/api/eventos?email=${encodeURIComponent(email)}",
        "&ultimo_id=",
    ):
        assert trecho in api, trecho
    assert 'method: "DELETE"' in api
    assert 'method: "POST"' in api


def test_eventos_sse_tratados():
    eventos = (SRC / "hooks" / "useEventos.ts").read_text(encoding="utf-8")
    assert "new EventSource(" in eventos
    for nome in ("conectado", "pedido", "promocao"):
        assert f'addEventListener("{nome}"' in eventos
    assert "EventSource.CLOSED" in eventos
    assert "lastEventId" in eventos


def test_aba_de_pagamento_aberta_antes_do_primeiro_await():
    loja = (SRC / "components" / "Loja.tsx").read_text(encoding="utf-8")
    corpo = loja[loja.index("const finalizarPedido = async") :]
    assert corpo.index("abasPagamento.abrir()") < corpo.index("await ")


def test_textos_da_aba_de_pagamento():
    abas = (SRC / "hooks" / "useAbasPagamento.ts").read_text(encoding="utf-8")
    assert 'window.open("", "_blank")' in abas
    assert '"Pagamento - aguardando"' in abas
    assert '"Aguardando a confirmação do estoque e o link de pagamento..."' in abas
    assert "location.href = url" in abas


def test_pagar_agora_abre_nova_aba_sem_opener():
    cartao = (SRC / "components" / "CartaoPedido.tsx").read_text(encoding="utf-8")
    assert 'target="_blank"' in cartao and 'rel="noopener noreferrer"' in cartao
    assert "Pagar agora" in cartao and "Cancelar pedido" in cartao


def _ferramentas_node():
    node = shutil.which("node")
    tsc = FRONTEND / "node_modules" / "typescript" / "bin" / "tsc"
    vite = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    if not node or not tsc.is_file() or not vite.is_file():
        pytest.skip("Node ou frontend/node_modules ausente (rode npm install em frontend/)")
    return node, tsc, vite


def test_tipos_e_build_do_vite(tmp_path):
    node, tsc, vite = _ferramentas_node()
    tipos = subprocess.run(
        [node, str(tsc), "-b"], cwd=FRONTEND, capture_output=True, text=True, timeout=180
    )
    assert tipos.returncode == 0, tipos.stdout + tipos.stderr
    saida = tmp_path / "dist"
    build = subprocess.run(
        [node, str(vite), "build", "--outDir", str(saida), "--emptyOutDir", "--logLevel", "error"],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert (saida / "index.html").is_file()
    assert list((saida / "assets").glob("*.js"))
