# Teste ponta a ponta do Trab2. Exige o sistema completo no ar (./run.sh, run.ps1
# ou execucao manual) e exercita tudo pelo API Gateway, como o frontend faria:
#   - REST (produtos, pedidos, cancelamento, interesses) e SSE (/api/eventos);
#   - pedido aprovado ate "Pedido Enviado", pedido recusado (estoque devolvido),
#     estoque indisponivel, cancelamento com webhook rejeitado;
#   - clique do usuario na pagina do Mock de Pagamento (POST do formulario);
#   - evento adulterado (assinatura invalida) descartado pelo gateway;
#   - reenvio de eventos SSE perdidos (Last-Event-ID);
#   - inscricao em promocoes e e-mails conferidos no Mailpit.
# Uso (na raiz do projeto):  python tests/e2e_fluxo.py
# Dica: suba o MS Promocoes com PROMOCAO_INTERVALO=5 para nao esperar 30 s.
import json
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import pika

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import config  # noqa: E402
from common.crypto_utils import build_envelope, load_private  # noqa: E402
from common.mq import EXCHANGE_ECOMMERCE  # noqa: E402

GATEWAY = f"http://127.0.0.1:{config.PORTA_PRINCIPAL}"
MAILPIT = config.URL_MAILPIT
CHAVE_PAGAMENTO = config.RAIZ / "ms_pagamento" / "keys" / "pagamento_private.pem"
# trust_env=False: no Windows o httpx usaria o proxy do sistema ate para 127.0.0.1.
http = httpx.Client(timeout=10, trust_env=False)

resultados = []


def checar(nome, condicao, detalhe=""):
    resultados.append((nome, bool(condicao)))
    status = "OK  " if condicao else "FALHOU"
    print(f"[{status}] {nome}" + (f" -> {detalhe}" if detalhe and not condicao else ""))
    return bool(condicao)


class ClienteSSE:
    # Le o stream SSE em uma thread e guarda todos os eventos recebidos.
    def __init__(self, email, ultimo_id=None):
        self.email = email
        self.eventos = []
        self._lock = threading.Lock()
        self._resposta = None
        cabecalhos = {"Last-Event-ID": str(ultimo_id)} if ultimo_id else {}
        threading.Thread(target=self._ler, args=(cabecalhos,), daemon=True).start()

    def _ler(self, cabecalhos):
        try:
            with httpx.Client(timeout=httpx.Timeout(5.0, read=None), trust_env=False) as cliente:
                with cliente.stream(
                    "GET", f"{GATEWAY}/api/eventos", params={"email": self.email}, headers=cabecalhos
                ) as resposta:
                    self._resposta = resposta
                    evento = {}
                    for linha in resposta.iter_lines():
                        if not linha:
                            if "data" in evento:
                                evento["data"] = json.loads(evento["data"])
                                with self._lock:
                                    self.eventos.append(evento)
                            evento = {}
                            continue
                        if linha.startswith(":"):
                            continue
                        campo, _, valor = linha.partition(":")
                        valor = valor[1:] if valor.startswith(" ") else valor
                        if campo == "data":
                            evento["data"] = evento["data"] + "\n" + valor if "data" in evento else valor
                        elif campo in ("event", "id"):
                            evento[campo] = valor
        except Exception:  # noqa: BLE001 - fim do stream (fechar() ou queda do gateway)
            pass

    def esperar(self, condicao, timeout=15):
        limite = time.time() + timeout
        while time.time() < limite:
            with self._lock:
                for evento in self.eventos:
                    if condicao(evento):
                        return evento
            time.sleep(0.05)
        return None

    def esperar_pedido(self, id_pedido, tipo, timeout=15):
        return self.esperar(
            lambda e: e.get("event") == "pedido"
            and e["data"].get("tipo") == tipo
            and e["data"].get("pedido", {}).get("id_pedido") == id_pedido,
            timeout,
        )

    def fechar(self):
        if self._resposta is not None:
            try:
                self._resposta.close()
            except Exception:  # noqa: BLE001
                pass


def esperar_gateway():
    for _ in range(60):
        try:
            if http.get(f"{GATEWAY}/api/saude", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def produtos():
    resposta = http.get(f"{GATEWAY}/api/produtos", params={"incluir_esgotados": "true"}, timeout=5)
    resposta.raise_for_status()
    return {p["codigo"]: p for p in resposta.json()}


def estoque(codigo):
    return produtos()[codigo]["estoque"]


def esperar_estoque(codigo, esperado, timeout=10):
    limite = time.time() + timeout
    while time.time() < limite:
        atual = estoque(codigo)
        if atual == esperado:
            return atual
        time.sleep(0.2)
    return estoque(codigo)


def criar_pedido(email, itens):
    return http.post(
        f"{GATEWAY}/api/pedidos", json={"email": email, "nome": "Teste E2E", "itens": itens}, timeout=10
    )


def decidir_pagamento(url_pagamento, decisao):
    # Equivale ao clique do usuario em "Pagamento Aprovado"/"Pagamento Recusado".
    return http.post(f"{url_pagamento}/decisao", data={"decisao": decisao}, timeout=20)


def emails_para(email):
    resposta = http.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{email}"}, timeout=5)
    return [m["Subject"] for m in resposta.json().get("messages", [])]


def esperar_email(email, trecho, timeout=20):
    limite = time.time() + timeout
    while time.time() < limite:
        assuntos = emails_para(email)
        if any(trecho in assunto for assunto in assuntos):
            return assuntos
        time.sleep(0.5)
    return None


def fluxo_aprovado(sse, email, codigo):
    print("\n--- Pedido aprovado ate o envio ---")
    antes = estoque(codigo)
    resposta = criar_pedido(email, [{"codigo": codigo, "quantidade": 1}])
    if not checar("POST /api/pedidos -> 201", resposta.status_code == 201, resposta.text):
        return
    pedido = resposta.json()
    id_pedido = pedido["id_pedido"]
    checar("pedido nasce CRIADO", pedido["status"] == "CRIADO", pedido["status"])
    checar("SSE pedido.criado", sse.esperar_pedido(id_pedido, "pedido.criado"))
    ok = sse.esperar_pedido(id_pedido, "pedido.estoque_ok")
    checar("SSE Estoque Confirmado", ok and ok["data"]["pedido"]["status"] == "ESTOQUE_CONFIRMADO")
    checar("estoque reservado (baixa de 1)", esperar_estoque(codigo, antes - 1) == antes - 1)
    pendente = sse.esperar_pedido(id_pedido, "pagamento.pendente")
    if not checar("SSE pagamento.pendente com URL do Mock", pendente and pendente["data"]["pedido"]["url_pagamento"]):
        return
    url = pendente["data"]["pedido"]["url_pagamento"]
    pagina = http.get(url, timeout=5)
    checar(
        "pagina do Mock com os botoes",
        pagina.status_code == 200 and "Pagamento Aprovado" in pagina.text and "Pagamento Recusado" in pagina.text,
    )
    decisao = decidir_pagamento(url, "APROVADO")
    checar("clique em Pagamento Aprovado -> 303", decisao.status_code == 303, decisao.status_code)
    aprovado = sse.esperar_pedido(id_pedido, "pagamento.aprovado")
    checar("SSE Pagamento Aprovado", aprovado and aprovado["data"]["pedido"]["status"] == "PAGAMENTO_APROVADO")
    enviado = sse.esperar_pedido(id_pedido, "pedido.enviado", timeout=20)
    checar(
        "SSE Pedido Enviado com nota fiscal",
        enviado and enviado["data"]["pedido"]["status"] == "ENVIADO" and enviado["data"]["pedido"]["nota_fiscal"],
    )
    final = http.get(f"{GATEWAY}/api/pedidos/{id_pedido}", timeout=5).json()
    checar("GET /api/pedidos/{id} -> ENVIADO", final["status"] == "ENVIADO", final["status"])
    checar("cancelar pedido enviado -> 409", http.delete(f"{GATEWAY}/api/pedidos/{id_pedido}").status_code == 409)


def fluxo_recusado(sse, email, codigo):
    print("\n--- Pagamento recusado (estoque devolvido) ---")
    antes = estoque(codigo)
    resposta = criar_pedido(email, [{"codigo": codigo, "quantidade": 2}])
    if not checar("POST /api/pedidos -> 201", resposta.status_code == 201, resposta.text):
        return
    id_pedido = resposta.json()["id_pedido"]
    pendente = sse.esperar_pedido(id_pedido, "pagamento.pendente")
    if not checar("SSE pagamento.pendente", pendente):
        return
    decidir_pagamento(pendente["data"]["pedido"]["url_pagamento"], "RECUSADO")
    recusado = sse.esperar_pedido(id_pedido, "pagamento.recusado")
    checar("SSE Pagamento Recusado", recusado and recusado["data"]["pedido"]["status"] == "PAGAMENTO_RECUSADO")
    checar("estoque devolvido apos pedido.excluido", esperar_estoque(codigo, antes) == antes)
    tarde = sse.esperar_pedido(id_pedido, "pagamento.aprovado", timeout=2)
    checar("nenhum Pagamento Aprovado depois da recusa", tarde is None)


def fluxo_indisponivel(sse, email, catalogo):
    print("\n--- Estoque indisponivel ---")
    codigo, produto = max(catalogo.items(), key=lambda par: par[1]["estoque"])
    quantidade = min(produto["estoque"] + 1, 99)
    resposta = criar_pedido(email, [{"codigo": codigo, "quantidade": quantidade}])
    if checar("POST pedido acima do estoque -> 201", resposta.status_code == 201, resposta.text):
        id_pedido = resposta.json()["id_pedido"]
        evento = sse.esperar_pedido(id_pedido, "estoque.indisponivel")
        checar(
            "SSE Estoque Indisponivel",
            evento and evento["data"]["pedido"]["status"] == "ESTOQUE_INDISPONIVEL" and evento["data"]["pedido"]["motivo"],
        )
        checar("estoque inalterado", estoque(codigo) == produto["estoque"])
    resposta = criar_pedido(email, [{"codigo": "ZZ9", "quantidade": 1}])
    if checar("POST produto inexistente -> 201 (Estoque decide)", resposta.status_code == 201, resposta.text):
        evento = sse.esperar_pedido(resposta.json()["id_pedido"], "estoque.indisponivel")
        checar("produto inexistente -> Estoque Indisponivel", evento)


def fluxo_cancelado(sse, email, codigo):
    print("\n--- Cancelamento pelo cliente (webhook posterior ignorado) ---")
    antes = estoque(codigo)
    resposta = criar_pedido(email, [{"codigo": codigo, "quantidade": 1}])
    if not checar("POST /api/pedidos -> 201", resposta.status_code == 201, resposta.text):
        return
    id_pedido = resposta.json()["id_pedido"]
    pendente = sse.esperar_pedido(id_pedido, "pagamento.pendente")
    if not checar("SSE pagamento.pendente", pendente):
        return
    url = pendente["data"]["pedido"]["url_pagamento"]

    # Evento adulterado: assinado pelo MS Pagamento para outro pedido e depois
    # alterado -> assinatura invalida -> o gateway deve descartar.
    envelope = build_envelope(
        "pagamento", "pagamento.aprovado", {"id_pedido": "PEDFALSO", "id_cobranca": "X", "valor": 1.0},
        load_private(CHAVE_PAGAMENTO),
    )
    envelope["data"]["id_pedido"] = id_pedido
    conexao = pika.BlockingConnection(pika.ConnectionParameters(host=config.RABBITMQ_HOST))
    conexao.channel().basic_publish(EXCHANGE_ECOMMERCE, "pagamento.aprovado", json.dumps(envelope).encode())
    conexao.close()
    checar("evento adulterado descartado", sse.esperar_pedido(id_pedido, "pagamento.aprovado", timeout=2) is None)

    cancelamento = http.delete(f"{GATEWAY}/api/pedidos/{id_pedido}", timeout=10)
    checar("DELETE /api/pedidos/{id} -> 200 CANCELADO",
           cancelamento.status_code == 200 and cancelamento.json()["status"] == "CANCELADO", cancelamento.text)
    checar("SSE pedido.cancelado", sse.esperar_pedido(id_pedido, "pedido.cancelado"))
    checar("estoque devolvido apos cancelamento", esperar_estoque(codigo, antes) == antes)
    time.sleep(1)
    decidir_pagamento(url, "APROVADO")
    checar("pagamento apos cancelamento nao aprova o pedido",
           sse.esperar_pedido(id_pedido, "pagamento.aprovado", timeout=3) is None)
    final = http.get(f"{GATEWAY}/api/pedidos/{id_pedido}", timeout=5).json()
    checar("pedido continua CANCELADO", final["status"] == "CANCELADO", final["status"])


def fluxo_reconexao(email, codigo):
    print("\n--- Reenvio de eventos SSE perdidos (Last-Event-ID) ---")
    sse = ClienteSSE(email)
    if not checar("SSE conectado", sse.esperar(lambda e: e.get("event") == "conectado")):
        return
    resposta = criar_pedido(email, [{"codigo": codigo, "quantidade": 1}])
    id_pedido = resposta.json()["id_pedido"]
    criado = sse.esperar_pedido(id_pedido, "pedido.criado")
    sse.fechar()
    if not checar("SSE pedido.criado", criado):
        return
    ultimo = int(criado["id"])  # o que veio depois dele (estoque_ok...) deve ser reenviado
    time.sleep(1.5)  # enquanto desconectado o pedido avanca (estoque_ok, pendente)
    novo = ClienteSSE(email, ultimo_id=ultimo)
    reenviado = novo.esperar_pedido(id_pedido, "pedido.estoque_ok", timeout=5)
    checar("evento perdido reenviado na reconexao", reenviado)
    checar("evento ja visto nao e reenviado", novo.esperar_pedido(id_pedido, "pedido.criado", timeout=1) is None)
    novo.fechar()
    http.delete(f"{GATEWAY}/api/pedidos/{id_pedido}", timeout=10)


def fluxo_promocoes(sse, email):
    print("\n--- Promocoes por e-mail ---")
    resposta = http.post(
        f"{GATEWAY}/api/interesses", json={"email": email, "nome": "Teste E2E", "categorias": ["A", "B", "C"]}
    )
    checar("POST /api/interesses -> 202", resposta.status_code == 202, resposta.text)
    limite = time.time() + 10
    categorias = []
    while time.time() < limite:
        categorias = http.get(f"{GATEWAY}/api/interesses/{email}", timeout=5).json().get("categorias", [])
        if categorias == ["A", "B", "C"]:
            break
        time.sleep(0.3)
    checar("MS Promocoes registrou o interesse", categorias == ["A", "B", "C"], categorias)
    checar("e-mail de confirmacao no Mailpit", esperar_email(email, "Inscri"))

    # So valem as promocoes geradas depois da inscricao; o e-mail de cada uma
    # traz o nome do produto no assunto.
    with sse._lock:
        marca = len(sse.eventos)
    limite = time.time() + max(config.PROMOCAO_INTERVALO * 3 + 10, 25)
    promo_recebida = email_recebido = False
    while time.time() < limite and not email_recebido:
        with sse._lock:
            novas = [e["data"] for e in sse.eventos[marca:] if e.get("event") == "promocao"]
        promo_recebida = promo_recebida or bool(novas)
        assuntos = emails_para(email) if novas else []
        email_recebido = any(p["produto"] in assunto for p in novas for assunto in assuntos)
        time.sleep(0.5)
    checar("SSE promocao recebida", promo_recebida)
    checar("e-mail da promocao no Mailpit", email_recebido)
    resposta = http.delete(f"{GATEWAY}/api/interesses/{email}")
    checar("DELETE /api/interesses/{email} -> 202", resposta.status_code == 202, resposta.text)
    limite = time.time() + 10
    while time.time() < limite:
        categorias = http.get(f"{GATEWAY}/api/interesses/{email}", timeout=5).json().get("categorias", [])
        if not categorias:
            break
        time.sleep(0.3)
    checar("interesse cancelado", categorias == [], categorias)


def validacoes():
    print("\n--- Validacoes da API ---")
    checar("pedido com e-mail invalido -> 422",
           criar_pedido("invalido", [{"codigo": "B1", "quantidade": 1}]).status_code == 422)
    checar("pedido com quantidade 0 -> 422",
           criar_pedido("a@b.com", [{"codigo": "B1", "quantidade": 0}]).status_code == 422)
    checar("pedido sem itens -> 422", criar_pedido("a@b.com", []).status_code == 422)
    checar("interesse em categoria invalida -> 422",
           http.post(f"{GATEWAY}/api/interesses", json={"email": "a@b.com", "categorias": ["Z"]}).status_code == 422)
    with http.stream("GET", f"{GATEWAY}/api/eventos", params={"email": "x"}, timeout=5) as resposta:
        checar("SSE com e-mail invalido -> 422", resposta.status_code == 422, resposta.status_code)
    checar("pedido inexistente -> 404", http.get(f"{GATEWAY}/api/pedidos/PEDNAOEXISTE").status_code == 404)


def main():
    if not esperar_gateway():
        print(f"API Gateway nao responde em {GATEWAY}. Suba o sistema antes (./run.sh).")
        sys.exit(2)
    email = f"e2e-{uuid.uuid4().hex[:8]}@exemplo.com"
    print(f"Cliente de teste: {email}")
    sse = ClienteSSE(email)
    checar("SSE conectado (evento 'conectado')", sse.esperar(lambda e: e.get("event") == "conectado"))

    catalogo = produtos()
    checar("GET /api/produtos (via MS Estoque)", len(catalogo) > 0)
    disponiveis = http.get(f"{GATEWAY}/api/produtos", timeout=5).json()
    checar("listagem padrao so com produtos em estoque", all(p["estoque"] > 0 for p in disponiveis))
    em_estoque = sorted((p for p in catalogo.values() if p["estoque"] >= 2), key=lambda p: -p["estoque"])
    if len(em_estoque) < 2:
        print("Estoque insuficiente para o teste: apague ms_estoque/data e reinicie o MS Estoque.")
        sys.exit(2)

    validacoes()
    fluxo_aprovado(sse, email, em_estoque[0]["codigo"])
    fluxo_recusado(sse, email, em_estoque[1]["codigo"])
    fluxo_indisponivel(sse, email, produtos())
    fluxo_cancelado(sse, email, em_estoque[1]["codigo"])
    fluxo_reconexao(email, em_estoque[1]["codigo"])
    fluxo_promocoes(sse, email)
    sse.fechar()
    http.request("DELETE", f"{MAILPIT}/api/v1/search", params={"query": f"to:{email}"})

    falhas = [nome for nome, ok in resultados if not ok]
    print(f"\n{len(resultados) - len(falhas)}/{len(resultados)} verificacoes OK")
    if falhas:
        print("Falharam: " + "; ".join(falhas))
        sys.exit(1)


if __name__ == "__main__":
    main()
