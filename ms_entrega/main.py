# Microsservico Entrega emissao de notas e entrega dos produtos.
# Exchange eCommerce (tipo direct).
# Consome: pagamento.aprovado.
# Apos receber, realiza a emissao da nota e a preparacao da entrega e publica
# pedido.enviado (informando que o pedido foi enviado).
# Fila propria: fila.entrega.
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.mq import EXCHANGE_ECOMMERCE, Consumer, Publisher

SERVICE = "entrega"
KEYS = Path(__file__).resolve().parent / "keys"

publisher = None


def enviar(data):
    id_pedido = data["id_pedido"]
    nota_fiscal = uuid.uuid4().hex[:8].upper()
    print(f"[entrega] pedido {id_pedido} - nota fiscal {nota_fiscal} emitida, enviando")
    publisher.publish(
        EXCHANGE_ECOMMERCE,
        "pedido.enviado",
        {"id_pedido": id_pedido, "nota_fiscal": nota_fiscal},
    )


def handle(routing_key, data):
    if routing_key == "pagamento.aprovado":
        enviar(data)


def main():
    global publisher
    publisher = Publisher(SERVICE, KEYS)
    print("[entrega] iniciado")
    Consumer(
        SERVICE,
        KEYS,
        "fila.entrega",
        EXCHANGE_ECOMMERCE,
        ["pagamento.aprovado"],
        handle,
    ).start()


if __name__ == "__main__":
    main()
