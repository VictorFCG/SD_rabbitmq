# Persistencia do MS Pagamento (SQLite, modulo sqlite3 da biblioteca padrao).
# Tabela cobrancas: cada cobranca criada no Mock de Pagamento, com o status atual.
# Tabela pedidos_excluidos: pedidos recebidos em pedido.excluido (a cobranca deles
#   nao volta a ficar pendente nem pode ser aprovada).
# O main.py trabalha com o estado em memoria e grava aqui cada mudanca; ao iniciar,
# carregar() devolve esse estado, para que um reinicio nao perca as cobrancas.
# Uma unica conexao e compartilhada pela thread consumidora do RabbitMQ e pelas
# threads do FastAPI, protegida por um lock.
import sqlite3
import threading
from pathlib import Path

CAMPOS = ("id_cobranca", "id_pedido", "valor", "status", "url_pagamento", "criado_em", "atualizado_em")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS cobrancas (
    id_cobranca TEXT PRIMARY KEY,
    id_pedido TEXT NOT NULL,
    valor REAL NOT NULL,
    status TEXT NOT NULL,
    url_pagamento TEXT,
    criado_em TEXT,
    atualizado_em TEXT
);
CREATE TABLE IF NOT EXISTS pedidos_excluidos (
    id_pedido TEXT PRIMARY KEY
);
"""


class PagamentosDB:
    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.caminho), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(ESQUEMA)

    def carregar(self):
        # Retorna (cobrancas por id_cobranca, da mais antiga para a mais recente;
        # conjunto dos pedidos excluidos).
        with self._lock:
            linhas = self._conn.execute("SELECT * FROM cobrancas ORDER BY criado_em, id_cobranca").fetchall()
            excluidos = self._conn.execute("SELECT id_pedido FROM pedidos_excluidos").fetchall()
        cobrancas = {linha["id_cobranca"]: {campo: linha[campo] for campo in CAMPOS} for linha in linhas}
        return cobrancas, {linha["id_pedido"] for linha in excluidos}

    def salvar_cobranca(self, cobranca):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO cobrancas (id_cobranca, id_pedido, valor, status, "
                "url_pagamento, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?)",
                tuple(cobranca[campo] for campo in CAMPOS),
            )

    def marcar_excluido(self, id_pedido):
        with self._lock, self._conn:
            self._conn.execute("INSERT OR IGNORE INTO pedidos_excluidos (id_pedido) VALUES (?)", (id_pedido,))

    def fechar(self):
        with self._lock:
            self._conn.close()
