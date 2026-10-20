# Pedidos do MS Principal (API Gateway): status, rotulos exibidos ao cliente,
# transicoes de estado permitidas e persistencia em SQLite (ms_principal/data).
# O banco e acessado pela thread consumidora do RabbitMQ e pelas threads das
# requisicoes HTTP, entao toda operacao acontece sob um lock e a transicao de
# status (le o status atual, valida e grava) e atomica.
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

CRIADO = "CRIADO"
ESTOQUE_CONFIRMADO = "ESTOQUE_CONFIRMADO"
AGUARDANDO_PAGAMENTO = "AGUARDANDO_PAGAMENTO"
PAGAMENTO_APROVADO = "PAGAMENTO_APROVADO"
ENVIADO = "ENVIADO"
ESTOQUE_INDISPONIVEL = "ESTOQUE_INDISPONIVEL"
PAGAMENTO_RECUSADO = "PAGAMENTO_RECUSADO"
CANCELADO = "CANCELADO"

ROTULOS = {
    CRIADO: "Pedido Criado",
    ESTOQUE_CONFIRMADO: "Estoque Confirmado",
    AGUARDANDO_PAGAMENTO: "Aguardando Pagamento",
    PAGAMENTO_APROVADO: "Pagamento Aprovado",
    ENVIADO: "Pedido Enviado",
    ESTOQUE_INDISPONIVEL: "Estoque Indisponível",
    PAGAMENTO_RECUSADO: "Pagamento Recusado",
    CANCELADO: "Pedido Cancelado",
}

TRANSICOES = {
    CRIADO: {ESTOQUE_CONFIRMADO, ESTOQUE_INDISPONIVEL, CANCELADO},
    ESTOQUE_CONFIRMADO: {AGUARDANDO_PAGAMENTO, PAGAMENTO_RECUSADO, CANCELADO},
    AGUARDANDO_PAGAMENTO: {PAGAMENTO_APROVADO, PAGAMENTO_RECUSADO, CANCELADO},
    PAGAMENTO_APROVADO: {ENVIADO},
    ENVIADO: set(),
    ESTOQUE_INDISPONIVEL: set(),
    PAGAMENTO_RECUSADO: set(),
    CANCELADO: set(),
}

CANCELAVEIS = {CRIADO, ESTOQUE_CONFIRMADO, AGUARDANDO_PAGAMENTO}

CAMPOS_ATUALIZAVEIS = (
    "itens",
    "valor_total",
    "url_pagamento",
    "id_cobranca",
    "nota_fiscal",
    "codigo_rastreio",
    "motivo",
)

MENSAGEM_CRIACAO = "Pedido criado. Aguardando confirmação de estoque."

_TABELA = """
CREATE TABLE IF NOT EXISTS pedidos (
    id_pedido TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    nome TEXT,
    itens TEXT NOT NULL,
    status TEXT NOT NULL,
    valor_total REAL,
    url_pagamento TEXT,
    id_cobranca TEXT,
    nota_fiscal TEXT,
    codigo_rastreio TEXT,
    motivo TEXT,
    historico TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
)
"""


def agora():
    return datetime.now(timezone.utc).isoformat()


def transicao_permitida(atual, novo):
    return novo in TRANSICOES.get(atual, set())


def _entrada_historico(status, mensagem, instante):
    return {
        "status": status,
        "status_label": ROTULOS.get(status, status),
        "mensagem": mensagem,
        "timestamp": instante,
    }


def _json(valor):
    return json.dumps(valor, ensure_ascii=False)


def _para_pedido(linha):
    return {
        "id_pedido": linha["id_pedido"],
        "email": linha["email"],
        "nome": linha["nome"],
        "itens": json.loads(linha["itens"]),
        "valor_total": linha["valor_total"],
        "status": linha["status"],
        "status_label": ROTULOS.get(linha["status"], linha["status"]),
        "url_pagamento": linha["url_pagamento"],
        "id_cobranca": linha["id_cobranca"],
        "nota_fiscal": linha["nota_fiscal"],
        "codigo_rastreio": linha["codigo_rastreio"],
        "motivo": linha["motivo"],
        "historico": json.loads(linha["historico"]),
        "criado_em": linha["criado_em"],
        "atualizado_em": linha["atualizado_em"],
    }


class PedidosDB:
    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.caminho), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            with self._conn:
                self._conn.execute(_TABELA)
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pedidos_email ON pedidos (email)"
                )

    def _buscar(self, id_pedido):
        linha = self._conn.execute(
            "SELECT * FROM pedidos WHERE id_pedido = ?", (id_pedido,)
        ).fetchone()
        return _para_pedido(linha) if linha else None

    def criar(self, email, nome, itens):
        instante = agora()
        with self._lock:
            for _ in range(5):
                id_pedido = "PED" + uuid.uuid4().hex[:8].upper()
                try:
                    with self._conn:
                        self._conn.execute(
                            "INSERT INTO pedidos (id_pedido, email, nome, itens, status, "
                            "historico, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                id_pedido,
                                email,
                                nome,
                                _json(itens),
                                CRIADO,
                                _json([_entrada_historico(CRIADO, MENSAGEM_CRIACAO, instante)]),
                                instante,
                                instante,
                            ),
                        )
                    return self._buscar(id_pedido)
                except sqlite3.IntegrityError:
                    continue
        raise RuntimeError("nao foi possivel gerar um id de pedido unico")

    def obter(self, id_pedido):
        with self._lock:
            return self._buscar(id_pedido)

    def listar_por_email(self, email):
        with self._lock:
            linhas = self._conn.execute(
                "SELECT * FROM pedidos WHERE email = ? ORDER BY criado_em DESC, rowid DESC",
                (email,),
            ).fetchall()
        return [_para_pedido(linha) for linha in linhas]

    def remover(self, id_pedido):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM pedidos WHERE id_pedido = ?", (id_pedido,))

    def transicionar(self, id_pedido, novo_status, mensagem, **campos):
        # Retorna (pedido, aplicado). Transicao nao permitida -> (pedido atual,
        # False); pedido desconhecido -> (None, False).
        desconhecidos = set(campos) - set(CAMPOS_ATUALIZAVEIS)
        if desconhecidos:
            raise ValueError(f"campos nao atualizaveis: {sorted(desconhecidos)}")
        with self._lock:
            pedido = self._buscar(id_pedido)
            if pedido is None:
                return None, False
            if not transicao_permitida(pedido["status"], novo_status):
                return pedido, False
            instante = agora()
            pedido.update(campos)
            pedido["status"] = novo_status
            pedido["status_label"] = ROTULOS[novo_status]
            pedido["historico"].append(_entrada_historico(novo_status, mensagem, instante))
            pedido["atualizado_em"] = instante
            with self._conn:
                self._conn.execute(
                    "UPDATE pedidos SET itens = ?, valor_total = ?, status = ?, url_pagamento = ?, "
                    "id_cobranca = ?, nota_fiscal = ?, codigo_rastreio = ?, motivo = ?, "
                    "historico = ?, atualizado_em = ? WHERE id_pedido = ?",
                    (
                        _json(pedido["itens"]),
                        pedido["valor_total"],
                        novo_status,
                        pedido["url_pagamento"],
                        pedido["id_cobranca"],
                        pedido["nota_fiscal"],
                        pedido["codigo_rastreio"],
                        pedido["motivo"],
                        _json(pedido["historico"]),
                        instante,
                        id_pedido,
                    ),
                )
            return pedido, True

    def fechar(self):
        with self._lock:
            self._conn.close()
