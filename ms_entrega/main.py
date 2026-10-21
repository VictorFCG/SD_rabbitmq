# Microsservico Entrega: emissao da nota fiscal e despacho dos produtos.
# Exchange eCommerce (tipo direct). Fila propria: fila.entrega.
# Consome: pagamento.aprovado.
# Simula a emissao da nota e o despacho (ENTREGA_DELAY segundos, padrao 2) e
# publica pedido.enviado com a nota fiscal e o codigo de rastreio.
import math
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.console import desativar_edicao_rapida
from common.mq import EXCHANGE_ECOMMERCE, Consumer, Publisher

SERVICE = "entrega"
KEYS = Path(__file__).resolve().parent / "keys"
ATRASO_PADRAO = 2.0

pedidos_enviados = set()
publisher = None


def atraso_despacho():
    # Lido do ambiente (ou do .env, carregado por common.config).
    try:
        atraso = float(os.environ.get("ENTREGA_DELAY", ATRASO_PADRAO))
    except ValueError:
        return ATRASO_PADRAO
    return atraso if math.isfinite(atraso) and atraso >= 0 else ATRASO_PADRAO


def gerar_nota_fiscal():
    return "NF-" + uuid.uuid4().hex[:8].upper()


def gerar_codigo_rastreio():
    return f"SD{random.randrange(10**9):09d}BR"


def enviar_pedido(dados):
    id_pedido = dados["id_pedido"]
    if id_pedido in pedidos_enviados:
        print(f"[entrega] pedido {id_pedido} ja foi enviado; evento repetido ignorado")
        return

    nota_fiscal = gerar_nota_fiscal()
    codigo_rastreio = gerar_codigo_rastreio()
    pausa = atraso_despacho() / 2
    print(f"[entrega] pedido {id_pedido} - emitindo nota fiscal {nota_fiscal}")
    time.sleep(pausa)
    print(f"[entrega] pedido {id_pedido} - despachando, rastreio {codigo_rastreio}")
    time.sleep(pausa)

    publisher.publish(
        EXCHANGE_ECOMMERCE,
        "pedido.enviado",
        {
            "id_pedido": id_pedido,
            "nota_fiscal": nota_fiscal,
            "codigo_rastreio": codigo_rastreio,
            "enviado_em": datetime.now(timezone.utc).isoformat(),
        },
    )
    # So marca depois de publicar: se a publicacao falhar, uma reentrega tenta de novo.
    pedidos_enviados.add(id_pedido)
    print(f"[entrega] pedido {id_pedido} enviado (pedido.enviado publicado)")


def handle(routing_key, data):
    if routing_key == "pagamento.aprovado":
        enviar_pedido(data)


def main():
    global publisher
    desativar_edicao_rapida()
    publisher = Publisher(SERVICE, KEYS)
    print(f"[entrega] iniciado (despacho simulado em {atraso_despacho():g}s). Ctrl+C para sair")
    try:
        Consumer(
            SERVICE,
            KEYS,
            "fila.entrega",
            [(EXCHANGE_ECOMMERCE, "pagamento.aprovado")],
            handle,
        ).start()
    except KeyboardInterrupt:
        print("[entrega] encerrado")
    finally:
        publisher.close()


if __name__ == "__main__":
    main()
