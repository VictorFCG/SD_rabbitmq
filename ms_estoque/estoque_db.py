# Persistencia do MS Estoque (SQLite).
# Tabela produtos: populada com o catalogo so na primeira execucao.
# Tabela reservas: itens baixados por pedido, devolvidos quando ele e excluido.
# Uma unica conexao compartilhada entre as threads, protegida por um lock.
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from common.catalogo import CATEGORIAS, PRODUTOS

ESQUEMA = """
CREATE TABLE IF NOT EXISTS produtos (
    codigo TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    categoria TEXT NOT NULL,
    preco REAL NOT NULL,
    estoque INTEGER NOT NULL CHECK (estoque >= 0)
);
CREATE TABLE IF NOT EXISTS reservas (
    id_pedido TEXT PRIMARY KEY,
    itens TEXT NOT NULL,
    valor_total REAL NOT NULL,
    criado_em TEXT NOT NULL
);
"""


def _agora():
    return datetime.now(timezone.utc).isoformat()


def _normalizar_codigo(codigo):
    return codigo.strip().upper() if isinstance(codigo, str) else None


def _quantidade_valida(quantidade):
    return isinstance(quantidade, int) and not isinstance(quantidade, bool) and quantidade >= 1


def _consolidar(itens):
    # Soma codigos repetidos; uma quantidade invalida torna o codigo todo indisponivel.
    consolidados = {}
    for item in itens:
        item = item if isinstance(item, dict) else {}
        codigo = _normalizar_codigo(item.get("codigo"))
        quantidade = item.get("quantidade")
        atual = consolidados.setdefault(codigo, {"codigo": codigo, "quantidade": 0, "valido": True})
        if not atual["valido"]:
            continue
        if _quantidade_valida(quantidade):
            atual["quantidade"] += quantidade
        else:
            atual["valido"] = False
            atual["quantidade"] = quantidade
    return list(consolidados.values())


def _buscar(conn, codigo):
    if codigo is None:
        return None
    return conn.execute("SELECT * FROM produtos WHERE codigo = ?", (codigo,)).fetchone()


def _produto(linha):
    return {
        "codigo": linha["codigo"],
        "nome": linha["nome"],
        "categoria": linha["categoria"],
        "categoria_nome": CATEGORIAS.get(linha["categoria"], linha["categoria"]),
        "preco": round(linha["preco"], 2),
        "estoque": linha["estoque"],
        "disponivel": linha["estoque"] > 0,
    }


def _item_reservado(linha, quantidade):
    preco = round(linha["preco"], 2)
    return {
        "codigo": linha["codigo"],
        "nome": linha["nome"],
        "categoria": linha["categoria"],
        "quantidade": quantidade,
        "preco_unitario": preco,
        "subtotal": round(preco * quantidade, 2),
    }


def _item_indisponivel(pedido, linha):
    return {
        "codigo": pedido["codigo"],
        "nome": linha["nome"] if linha is not None else None,
        "solicitado": pedido["quantidade"],
        "disponivel": linha["estoque"] if linha is not None else 0,
    }


# PDF (0,1): persiste os dados dos produtos em estoque.
class EstoqueDB:
    def __init__(self, caminho):
        caminho = Path(caminho)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # isolation_level=None: as transacoes sao abertas explicitamente (BEGIN IMMEDIATE).
        self._conn = sqlite3.connect(str(caminho), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(ESQUEMA)
        with self._transacao() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO produtos (codigo, nome, categoria, preco, estoque) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (codigo, p["nome"], p["categoria"], p["preco"], p["estoque"])
                    for codigo, p in PRODUTOS.items()
                ],
            )

    @contextmanager
    def _transacao(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def listar(self, apenas_disponiveis=False):
        filtro = " WHERE estoque > 0" if apenas_disponiveis else ""
        with self._lock:
            linhas = self._conn.execute(f"SELECT * FROM produtos{filtro} ORDER BY codigo").fetchall()
        return [_produto(linha) for linha in linhas]

    def obter(self, codigo):
        with self._lock:
            linha = _buscar(self._conn, _normalizar_codigo(codigo))
        return _produto(linha) if linha is not None else None

    def estoque_atual(self):
        with self._lock:
            linhas = self._conn.execute("SELECT codigo, estoque FROM produtos ORDER BY codigo").fetchall()
        return {linha["codigo"]: linha["estoque"] for linha in linhas}

    def obter_reserva(self, id_pedido):
        with self._lock:
            linha = self._conn.execute(
                "SELECT * FROM reservas WHERE id_pedido = ?", (id_pedido,)
            ).fetchone()
        if linha is None:
            return None
        return {
            "id_pedido": linha["id_pedido"],
            "itens": json.loads(linha["itens"]),
            "valor_total": linha["valor_total"],
            "criado_em": linha["criado_em"],
        }

    def reservar(self, id_pedido, itens):
        # Tudo ou nada: se algum item estiver indisponivel nada e alterado.
        if not isinstance(itens, list) or not itens:
            raise ValueError("o pedido precisa ter ao menos um item")
        pedidos = _consolidar(itens)
        with self._transacao() as conn:
            existente = conn.execute(
                "SELECT itens, valor_total FROM reservas WHERE id_pedido = ?", (id_pedido,)
            ).fetchone()
            if existente is not None:
                return {
                    "ok": True,
                    "itens": json.loads(existente["itens"]),
                    "valor_total": existente["valor_total"],
                }

            reservados, indisponiveis = [], []
            for pedido in pedidos:
                linha = _buscar(conn, pedido["codigo"])
                if linha is None or not pedido["valido"] or pedido["quantidade"] > linha["estoque"]:
                    indisponiveis.append(_item_indisponivel(pedido, linha))
                else:
                    reservados.append(_item_reservado(linha, pedido["quantidade"]))
            if indisponiveis:
                return {"ok": False, "indisponiveis": indisponiveis}

            for item in reservados:
                conn.execute(
                    "UPDATE produtos SET estoque = estoque - ? WHERE codigo = ?",
                    (item["quantidade"], item["codigo"]),
                )
            valor_total = round(sum(item["subtotal"] for item in reservados), 2)
            conn.execute(
                "INSERT INTO reservas (id_pedido, itens, valor_total, criado_em) VALUES (?, ?, ?, ?)",
                (id_pedido, json.dumps(reservados, ensure_ascii=False), valor_total, _agora()),
            )
            return {"ok": True, "itens": reservados, "valor_total": valor_total}

    def devolver(self, id_pedido):
        with self._transacao() as conn:
            reserva = conn.execute(
                "SELECT itens FROM reservas WHERE id_pedido = ?", (id_pedido,)
            ).fetchone()
            if reserva is None:
                return None
            itens = json.loads(reserva["itens"])
            for item in itens:
                conn.execute(
                    "UPDATE produtos SET estoque = estoque + ? WHERE codigo = ?",
                    (item["quantidade"], item["codigo"]),
                )
            conn.execute("DELETE FROM reservas WHERE id_pedido = ?", (id_pedido,))
            return itens

    def fechar(self):
        with self._lock:
            self._conn.close()
