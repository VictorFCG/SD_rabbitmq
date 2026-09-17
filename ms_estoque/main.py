# Microsservico Estoque (specs.txt): gerenciamento do estoque dos produtos.
# Exchange eCommerce (tipo direct).
# Consome: pedido.criado e pedido.excluido.
# Ao receber pedido.criado verifica a disponibilidade dos produtos:
#   - todos disponiveis -> realiza a reserva/baixa e publica pedido.estoque_ok;
#   - algum indisponivel -> publica estoque.indisponivel.
# Ao receber pedido.excluido devolve ao estoque os produtos que haviam sido
# reservados para o pedido.
# Fila propria: fila.estoque.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.catalogo import estoque_inicial
from common.mq import EXCHANGE_ECOMMERCE, Consumer, Publisher

SERVICE = "estoque"
KEYS = Path(__file__).resolve().parent / "keys"

estoque = estoque_inicial()
reservas = {}
publisher = None


def criar_pedido(data):
    id_pedido = data["id_pedido"]
    itens = data["itens"]

    indisponiveis = [
        item["codigo"] for item in itens if estoque.get(item["codigo"], 0) < item["quantidade"]
    ]
    if indisponiveis:
        print(f"[estoque] pedido {id_pedido} indisponivel: {indisponiveis}")
        publisher.publish(
            EXCHANGE_ECOMMERCE,
            "estoque.indisponivel",
            {"id_pedido": id_pedido, "indisponiveis": indisponiveis},
        )
        return

    for item in itens:
        estoque[item["codigo"]] -= item["quantidade"]
    reservas[id_pedido] = itens
    print(f"[estoque] pedido {id_pedido} reservado. estoque atual={estoque}")
    publisher.publish(
        EXCHANGE_ECOMMERCE, "pedido.estoque_ok", {"id_pedido": id_pedido, "itens": itens}
    )


def excluir_pedido(data):
    id_pedido = data["id_pedido"]
    itens = reservas.pop(id_pedido, None)
    if itens is None:
        return
    for item in itens:
        estoque[item["codigo"]] += item["quantidade"]
    print(f"[estoque] pedido {id_pedido} devolvido. estoque atual={estoque}")


def handle(routing_key, data):
    if routing_key == "pedido.criado":
        criar_pedido(data)
    elif routing_key == "pedido.excluido":
        excluir_pedido(data)


def main():
    global publisher
    publisher = Publisher(SERVICE, KEYS)
    print(f"[estoque] iniciado. estoque inicial={estoque}")
    Consumer(
        SERVICE,
        KEYS,
        "fila.estoque",
        EXCHANGE_ECOMMERCE,
        ["pedido.criado", "pedido.excluido"],
        handle,
    ).start()


if __name__ == "__main__":
    main()
