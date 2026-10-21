# Camada de mensageria (RabbitMQ) compartilhada pelos processos.
#   eCommerce (tipo direct) - eventos do fluxo de pedido.
#   Promocoes (tipo topic)  - interesse.promocao e promocao.categoria.<A|B|C>.
# Cada consumidor possui a sua propria fila; nao se usa exchange fanout.
# Todo evento vai em um envelope assinado (campo Signature) e o consumidor
# descarta os que tiverem assinatura invalida.
import json
import threading
import time
from pathlib import Path

import pika
from pika.exceptions import AMQPError

from common import config
from common.crypto_utils import build_envelope, load_private, verify_envelope

EXCHANGE_ECOMMERCE = "eCommerce"
EXCHANGE_PROMOCOES = "Promoções"
TIPOS_EXCHANGE = {EXCHANGE_ECOMMERCE: "direct", EXCHANGE_PROMOCOES: "topic"}

TENTATIVAS_PUBLICACAO = 3
INTERVALO_KEEPALIVE = 10  # segundos
INTERVALO_RECONEXAO = 3  # segundos


def _connect(host):
    return pika.BlockingConnection(pika.ConnectionParameters(host=host, heartbeat=60))


def _declarar_exchanges(channel):
    for exchange, tipo in TIPOS_EXCHANGE.items():
        channel.exchange_declare(exchange=exchange, exchange_type=tipo, durable=True)


class Publisher:
    def __init__(self, service, key_dir, host=None):
        self.service = service
        self.key_dir = Path(key_dir)
        self.private_key = load_private(self.key_dir / f"{service}_private.pem")
        self.host = host or config.RABBITMQ_HOST
        self._lock = threading.Lock()
        self._connection = None
        self._channel = None
        threading.Thread(target=self._keepalive, name=f"keepalive-{service}", daemon=True).start()

    def _abrir(self):
        self._connection = _connect(self.host)
        self._channel = self._connection.channel()
        self._channel.confirm_delivery()
        _declarar_exchanges(self._channel)

    def _descartar(self):
        try:
            if self._connection is not None and self._connection.is_open:
                self._connection.close()
        except Exception:  # noqa: BLE001 - conexao ja quebrada
            pass
        self._connection = None
        self._channel = None

    def publish(self, exchange, routing_key, data):
        envelope = build_envelope(self.service, routing_key, data, self.private_key)
        body = json.dumps(envelope).encode("utf-8")
        with self._lock:
            for tentativa in range(1, TENTATIVAS_PUBLICACAO + 1):
                try:
                    if self._channel is None or not self._channel.is_open:
                        self._descartar()
                        self._abrir()
                    self._channel.basic_publish(
                        exchange=exchange,
                        routing_key=routing_key,
                        body=body,
                        properties=pika.BasicProperties(
                            delivery_mode=2, content_type="application/json"
                        ),
                    )
                    return
                except AMQPError as exc:
                    self._descartar()
                    if tentativa == TENTATIVAS_PUBLICACAO:
                        raise
                    print(
                        f"[{self.service}] falha ao publicar {routing_key} "
                        f"({exc.__class__.__name__}), tentando de novo"
                    )
                    time.sleep(0.5 * tentativa)

    def _keepalive(self):
        # Mantem a conexao ociosa viva: a BlockingConnection so envia heartbeat com I/O.
        while True:
            time.sleep(INTERVALO_KEEPALIVE)
            with self._lock:
                if self._connection is None:
                    continue
                try:
                    self._connection.process_data_events(time_limit=0)
                except AMQPError:
                    self._descartar()

    def close(self):
        with self._lock:
            self._descartar()


class Consumer:
    # bindings: lista de pares (exchange, routing_key).
    def __init__(self, service, key_dir, queue, bindings, handler, host=None):
        self.service = service
        self.key_dir = Path(key_dir)
        self.queue = queue
        self.bindings = bindings
        self.handler = handler
        self.host = host or config.RABBITMQ_HOST

    def _on_message(self, channel, method, properties, body):
        try:
            envelope = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
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
            print(f"[{self.service}] erro ao processar evento {envelope.get('routing_key')}: {exc!r}")
        channel.basic_ack(method.delivery_tag)

    def _consumir(self):
        connection = _connect(self.host)
        try:
            channel = connection.channel()
            _declarar_exchanges(channel)
            channel.queue_declare(queue=self.queue, durable=True)
            for exchange, routing_key in self.bindings:
                channel.queue_bind(queue=self.queue, exchange=exchange, routing_key=routing_key)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=self.queue, on_message_callback=self._on_message)
            chaves = ", ".join(routing_key for _, routing_key in self.bindings)
            print(f"[{self.service}] consumindo fila '{self.queue}' ({chaves})")
            channel.start_consuming()
        finally:
            try:
                if connection.is_open:
                    connection.close()
            except AMQPError:
                pass

    def start(self):
        while True:
            try:
                self._consumir()
            except AMQPError as exc:
                print(
                    f"[{self.service}] RabbitMQ indisponivel ({exc.__class__.__name__}); "
                    f"reconectando em {INTERVALO_RECONEXAO}s"
                )
            except Exception as exc:  # noqa: BLE001 - a thread consumidora nao pode morrer
                print(f"[{self.service}] erro no consumidor ({exc!r}); reconectando")
            time.sleep(INTERVALO_RECONEXAO)

    def start_in_thread(self):
        thread = threading.Thread(target=self.start, name=f"consumer-{self.queue}", daemon=True)
        thread.start()
        return thread
