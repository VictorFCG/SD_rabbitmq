import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.catalogo import PRODUTOS
from common.mq import EXCHANGE_PROMOCOES, Publisher

SERVICE = "promocoes"
KEYS = Path(__file__).resolve().parent / "keys"


def main():
    publisher = Publisher(SERVICE, KEYS)
    print("[promocoes] iniciado (apenas publica). Ctrl+C para sair")
    try:
        while True:
            codigo, produto = random.choice(list(PRODUTOS.items()))
            desconto = random.choice([5, 10, 15, 20, 30, 50])
            routing_key = f"promocao.categoria.{produto['categoria']}"
            publisher.publish(
                EXCHANGE_PROMOCOES,
                routing_key,
                {
                    "codigo": codigo,
                    "produto": produto["nome"],
                    "categoria": produto["categoria"],
                    "desconto": desconto,
                },
            )
            print(f"[promocoes] {routing_key}: {produto['nome']} com {desconto}% de desconto")
            time.sleep(random.randint(3, 6))
    except KeyboardInterrupt:
        publisher.close()


if __name__ == "__main__":
    main()
