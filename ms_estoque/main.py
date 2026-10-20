# Microsservico Estoque: dono dos dados dos produtos (SQLite em data/estoque.db).
# Exchange eCommerce (tipo direct). Fila propria: fila.estoque.
# Consome: pedido.criado (reserva os itens) e pedido.excluido (devolve a reserva).
# Publica: pedido.estoque_ok ou estoque.indisponivel.
# REST para o API Gateway: GET /produtos, GET /produtos/{codigo} e GET /saude.
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pika.exceptions import AMQPError
from pydantic import BaseModel

from common import config
from common.console import desativar_edicao_rapida
from common.mq import EXCHANGE_ECOMMERCE, Consumer, Publisher
from estoque_db import EstoqueDB

SERVICE = "estoque"
KEYS = Path(__file__).resolve().parent / "keys"
DB_PATH = Path(__file__).resolve().parent / "data" / "estoque.db"

db = None
publisher = None


def log(mensagem):
    # Somente ASCII nos logs (consoles do Windows).
    print(f"[{SERVICE}] {mensagem}".encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def _id_valido(id_pedido):
    return isinstance(id_pedido, str) and bool(id_pedido.strip())


def publicar(routing_key, data):
    try:
        publisher.publish(EXCHANGE_ECOMMERCE, routing_key, data)
    except (AMQPError, OSError) as exc:
        # Mantem a reserva (idempotente); se o pedido for excluido o estoque volta.
        log(f"falha ao publicar {routing_key} do pedido {data['id_pedido']} ({exc.__class__.__name__})")


def criar_pedido(data):
    id_pedido = data.get("id_pedido")
    itens = data.get("itens")
    if not _id_valido(id_pedido) or not isinstance(itens, list) or not itens:
        log(f"pedido.criado malformado ignorado (id_pedido={id_pedido!r})")
        return

    resultado = db.reservar(id_pedido, itens)
    if not resultado["ok"]:
        codigos = [item["codigo"] for item in resultado["indisponiveis"]]
        log(f"pedido {id_pedido} indisponivel: {codigos}")
        publicar(
            "estoque.indisponivel",
            {"id_pedido": id_pedido, "indisponiveis": resultado["indisponiveis"]},
        )
        return

    log(f"pedido {id_pedido} reservado. estoque atual={db.estoque_atual()}")
    publicar(
        "pedido.estoque_ok",
        {
            "id_pedido": id_pedido,
            "cliente": data.get("cliente"),
            "itens": resultado["itens"],
            "valor_total": resultado["valor_total"],
        },
    )


def excluir_pedido(data):
    id_pedido = data.get("id_pedido")
    if not _id_valido(id_pedido):
        log(f"pedido.excluido malformado ignorado (id_pedido={id_pedido!r})")
        return
    if db.devolver(id_pedido) is None:
        log(f"pedido {id_pedido} excluido ({data.get('motivo')}) sem reserva: nada a devolver")
        return
    log(f"pedido {id_pedido} devolvido. estoque atual={db.estoque_atual()}")


def handle(routing_key, data):
    if not isinstance(data, dict):
        log(f"evento {routing_key} malformado ignorado")
        return
    if routing_key == "pedido.criado":
        criar_pedido(data)
    elif routing_key == "pedido.excluido":
        excluir_pedido(data)


app = FastAPI(
    title="MS Estoque",
    description="Consulta dos produtos e das quantidades em estoque (usada pelo API Gateway).",
    version="2.0.0",
)


class Produto(BaseModel):
    codigo: str
    nome: str
    categoria: str
    categoria_nome: str
    preco: float
    estoque: int
    disponivel: bool


# PDF (0,1): endpoint para o API Gateway consultar os produtos disponiveis.
@app.get("/produtos", response_model=list[Produto], summary="Lista os produtos do estoque")
def listar_produtos(
    disponiveis: Annotated[
        bool, Query(description="Se verdadeiro, lista apenas os produtos com estoque disponível.")
    ] = False,
):
    return db.listar(apenas_disponiveis=disponiveis)


@app.get(
    "/produtos/{codigo}",
    response_model=Produto,
    summary="Consulta um produto",
    responses={404: {"description": "Produto não encontrado"}},
)
def obter_produto(codigo: str):
    produto = db.obter(codigo)
    if produto is None:
        raise HTTPException(status_code=404, detail=f"Produto {codigo} não encontrado.")
    return produto


@app.get("/saude", summary="Verifica se o serviço está no ar")
def saude():
    return {"servico": SERVICE, "status": "ok"}


def main():
    global db, publisher
    desativar_edicao_rapida()
    db = EstoqueDB(DB_PATH)
    publisher = Publisher(SERVICE, KEYS)
    log(f"iniciado. estoque atual={db.estoque_atual()}")
    Consumer(
        SERVICE,
        KEYS,
        "fila.estoque",
        [(EXCHANGE_ECOMMERCE, "pedido.criado"), (EXCHANGE_ECOMMERCE, "pedido.excluido")],
        handle,
    ).start_in_thread()
    log(f"API REST em http://{config.HTTP_HOST}:{config.PORTA_ESTOQUE} (documentacao em /docs)")
    try:
        uvicorn.run(app, host=config.HTTP_HOST, port=config.PORTA_ESTOQUE)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
