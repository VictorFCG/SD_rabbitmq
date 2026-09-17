# Camada de mensageria (RabbitMQ) compartilhada pelos processos.
# specs.txt: comunicacao exclusivamente por eventos publicados/consumidos no
# broker, sem chamadas diretas entre processos; routing keys hierarquicas;
# cada consumidor possui a sua propria fila.
# Exchanges:
#   eCommerce (tipo direct) - eventos do fluxo de pedido.
#   Promocoes (tipo topic)  - promocoes de produtos por categoria.
# Nao e permitido o uso de exchange fanout.
# Publica todo evento em um envelope assinado (campo Signature) e o consumidor
# verifica a assinatura antes de processar (assinatura invalida e descartada).
import json
from pathlib import Path

import pika

from common.crypto_utils import build_envelope, load_private, verify_envelope

EXCHANGE_ECOMMERCE = "eCommerce"
EXCHANGE_PROMOCOES = "Promoções"


def _connect(host):
    return pika.BlockingConnection(pika.ConnectionParameters(host=host, heartbeat=60))


class Publisher:
    def __init__(self, service, key_dir, host="localhost"):
        self.service = service
        self.key_dir = Path(key_dir)
        self.private_key = load_private(self.key_dir / f"{service}_private.pem")
        self.connection = _connect(host)
        self.channel = self.connection.channel()
        self.channel.exchange_declare(
            exchange=EXCHANGE_ECOMMERCE, exchange_type="direct", durable=True
        )
        self.channel.exchange_declare(
            exchange=EXCHANGE_PROMOCOES, exchange_type="topic", durable=True
        )

    def publish(self, exchange, routing_key, data):
        envelope = build_envelope(self.service, routing_key, data, self.private_key)
        self.channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=json.dumps(envelope).encode("utf-8"),
            properties=pika.BasicProperties(delivery_mode=2),
        )

    def close(self):
        if self.connection.is_open:
            self.connection.close()


class Consumer:
    def __init__(self, service, key_dir, queue, exchange, bindings, handler,
                 exchange_type="direct", host="localhost"):
        self.service = service
        self.key_dir = Path(key_dir)
        self.queue = queue
        self.exchange = exchange
        self.bindings = bindings
        self.handler = handler
        self.exchange_type = exchange_type
        self.host = host

    def _on_message(self, channel, method, properties, body):
        try:
            envelope = json.loads(body)
        except json.JSONDecodeError:
            print(f"[{self.service}] mensagem invalida descartada")
            channel.basic_ack(method.delivery_tag)
            return

        try:
            valido = verify_envelope(envelope, self.key_dir)
        except Exception:  # noqa: BLE001 - envelope malformado nao pode derrubar o consumidor
            valido = False

        if not valido:
            origem = envelope.get("service") if isinstance(envelope, dict) else "desconhecido"
            print(
                f"[{self.service}] assinatura invalida de "
                f"'{origem}' -> evento descartado"
            )
            channel.basic_ack(method.delivery_tag)
            return

        try:
            self.handler(envelope["routing_key"], envelope["data"])
        except Exception as exc:  # noqa: BLE001 - mantem o consumidor vivo
            print(f"[{self.service}] erro ao processar evento: {exc}")
        channel.basic_ack(method.delivery_tag)

    def start(self):
        connection = _connect(self.host)
        channel = connection.channel()
        channel.exchange_declare(
            exchange=self.exchange, exchange_type=self.exchange_type, durable=True
        )
        channel.queue_declare(queue=self.queue, durable=True)
        for binding in self.bindings:
            channel.queue_bind(queue=self.queue, exchange=self.exchange, routing_key=binding)
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(queue=self.queue, on_message_callback=self._on_message)
        print(f"[{self.service}] consumindo fila '{self.queue}' ({', '.join(self.bindings)})")
        channel.start_consuming()
