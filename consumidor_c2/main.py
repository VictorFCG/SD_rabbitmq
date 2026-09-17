# Consumidor de Promocoes C2 (specs.txt): recebe apenas notificacoes sobre
# promocoes de produtos e comunica-se exclusivamente com o RabbitMQ (nao pode
# realizar chamadas para nenhum dos microsservicos).
# Exchange Promocoes (tipo topic).
# Registra interesse em todas as categorias, com o padrao de binding:
#   promocao.categoria.*
# Fila propria: fila.C2.
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
