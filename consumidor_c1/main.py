import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.mq import EXCHANGE_PROMOCOES, Consumer

SERVICE = "c1"
KEYS = Path(__file__).resolve().parent / "keys"


def handle(routing_key, data):
    print(
        f"[C1] promocao categoria {data['categoria']}: "
        f"{data['produto']} com {data['desconto']}% de desconto"
    )


def main():
    print("[C1] interessado nas categorias A e B")
    Consumer(
        SERVICE,
        KEYS,
        "fila.C1",
        EXCHANGE_PROMOCOES,
        ["promocao.categoria.A", "promocao.categoria.B"],
        handle,
        exchange_type="topic",
    ).start()


if __name__ == "__main__":
    main()
