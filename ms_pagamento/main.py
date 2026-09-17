import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.mq import EXCHANGE_ECOMMERCE, Consumer, Publisher

SERVICE = "pagamento"
KEYS = Path(__file__).resolve().parent / "keys"

publisher = None


def processar(data):
    id_pedido = data["id_pedido"]
    aprovado = random.random() < 0.8
    if aprovado:
        print(f"[pagamento] pedido {id_pedido} APROVADO")
        publisher.publish(
            EXCHANGE_ECOMMERCE, "pagamento.aprovado", {"id_pedido": id_pedido}
        )
    else:
        print(f"[pagamento] pedido {id_pedido} RECUSADO")
        publisher.publish(
            EXCHANGE_ECOMMERCE, "pagamento.recusado", {"id_pedido": id_pedido}
        )


def handle(routing_key, data):
    if routing_key == "pedido.estoque_ok":
        processar(data)


def main():
    global publisher
    publisher = Publisher(SERVICE, KEYS)
    print("[pagamento] iniciado")
    Consumer(
        SERVICE,
        KEYS,
        "fila.pagamento",
        EXCHANGE_ECOMMERCE,
        ["pedido.estoque_ok"],
        handle,
    ).start()


if __name__ == "__main__":
    main()
