# Repositorio (SQLite) dos interesses dos clientes em promocoes (MS Promocoes).
# Tabela interesses: uma linha por (email, categoria) em que o cliente se
# inscreveu, com o nome informado e a data da inscricao. Os e-mails sao
# normalizados (sem espacos nas pontas e em minusculas).
# A conexao e usada pela thread consumidora (pika), pela thread geradora de
# promocoes e pelas threads do FastAPI, por isso todo acesso passa pelo lock.
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def normalizar_email(email):
    return str(email or "").strip().lower()


def _limpar_nome(nome):
    if nome is None:
        return None
    return str(nome).strip() or None


class InteressesDB:
    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.caminho), check_same_thread=False)
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS interesses ("
                " email TEXT NOT NULL,"
                " categoria TEXT NOT NULL,"
                " nome TEXT,"
                " criado_em TEXT NOT NULL,"
                " PRIMARY KEY (email, categoria))"
            )

    def _categorias(self, email):
        linhas = self._conn.execute(
            "SELECT categoria FROM interesses WHERE email = ? ORDER BY categoria", (email,)
        ).fetchall()
        return [categoria for (categoria,) in linhas]

    def _nome(self, email):
        linha = self._conn.execute(
            "SELECT nome FROM interesses WHERE email = ? AND nome IS NOT NULL LIMIT 1",
            (email,),
        ).fetchone()
        return linha[0] if linha else None

    def inscrever(self, email, nome, categorias):
        # Soma as categorias as que o e-mail ja possui; devolve todas as atuais.
        email = normalizar_email(email)
        nome = _limpar_nome(nome)
        agora = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            if nome:
                self._conn.execute("UPDATE interesses SET nome = ? WHERE email = ?", (nome, email))
            else:
                nome = self._nome(email)
            self._conn.executemany(
                "INSERT OR IGNORE INTO interesses (email, categoria, nome, criado_em)"
                " VALUES (?, ?, ?, ?)",
                [(email, categoria, nome, agora) for categoria in dict.fromkeys(categorias)],
            )
            return self._categorias(email)

    def cancelar(self, email, categorias=None):
        # Remove as categorias informadas (ou todas, se nenhuma); devolve as restantes.
        email = normalizar_email(email)
        with self._lock, self._conn:
            if categorias:
                self._conn.executemany(
                    "DELETE FROM interesses WHERE email = ? AND categoria = ?",
                    [(email, categoria) for categoria in dict.fromkeys(categorias)],
                )
            else:
                self._conn.execute("DELETE FROM interesses WHERE email = ?", (email,))
            return self._categorias(email)

    def categorias_de(self, email):
        with self._lock:
            return self._categorias(normalizar_email(email))

    def nome_de(self, email):
        with self._lock:
            return self._nome(normalizar_email(email))

    def interessados(self, categoria):
        with self._lock:
            linhas = self._conn.execute(
                "SELECT email, nome FROM interesses WHERE categoria = ? ORDER BY email",
                (categoria,),
            ).fetchall()
        return [{"email": email, "nome": nome} for email, nome in linhas]

    def fechar(self):
        with self._lock:
            self._conn.close()
