import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.mq import EXCHANGE_PROMOCOES, Consumer

SERVICE = "c2"
KEYS = Path(__file__).resolve().parent / "keys"


def handle(routing_key, data):
    print(
        f"[C2] promocao categoria {data['categoria']}: "
        f"{data['produto']} com {data['desconto']}% de desconto"
    )


def main():
    print("[C2] interessado em todas as categorias")
    Consumer(
        SERVICE,
        KEYS,
        "fila.C2",
        EXCHANGE_PROMOCOES,
        ["promocao.categoria.*"],
        handle,
        exchange_type="topic",
    ).start()


if __name__ == "__main__":
    main()
