# Hub das notificacoes SSE do MS Principal (API Gateway).
# Recebe eventos de qualquer thread e os entrega as filas asyncio das conexoes
# SSE de cada e-mail. Guarda um historico por e-mail para reenvio quando o
# navegador reconecta (Last-Event-ID); os ids comecam no instante atual em ms
# para continuarem crescendo apos um reinicio.
import asyncio
import threading
import time
from collections import deque

TAMANHO_FILA = 1000
TAMANHO_HISTORICO = 100


def _colocar(fila, item):
    try:
        fila.put_nowait(item)
    except asyncio.QueueFull:
        print(f"[principal] fila SSE cheia, evento {item['id']} descartado")


class HubNotificacoes:
    def __init__(self, tamanho_fila=TAMANHO_FILA, tamanho_historico=TAMANHO_HISTORICO):
        self._tamanho_fila = tamanho_fila
        self._tamanho_historico = tamanho_historico
        self._lock = threading.Lock()
        self._loop = None
        self._filas = {}
        self._historico = {}
        self._ultimo_id = int(time.time() * 1000)
        self.encerrado = False

    def configurar(self, loop):
        with self._lock:
            self._loop = loop

    def inscrever(self, email):
        fila = asyncio.Queue(maxsize=self._tamanho_fila)
        with self._lock:
            self._filas.setdefault(email, []).append(fila)
        return fila

    def cancelar(self, email, fila):
        with self._lock:
            filas = self._filas.get(email)
            if filas is None:
                return
            if fila in filas:
                filas.remove(fila)
            if not filas:
                del self._filas[email]

    def _registrar(self, email, item):
        historico = self._historico.get(email)
        if historico is None:
            historico = self._historico[email] = deque(maxlen=self._tamanho_historico)
        historico.append(item)

    def _entregar(self, filas, item):
        # Chamado com o lock adquirido: os eventos chegam as filas na ordem dos ids.
        if self._loop is None:
            return
        for fila in filas:
            try:
                self._loop.call_soon_threadsafe(_colocar, fila, item)
            except RuntimeError:
                return  # loop ja encerrado

    def _novo_item(self, evento, dados):
        self._ultimo_id += 1
        return {"id": self._ultimo_id, "evento": evento, "dados": dados}

    def enviar(self, email, evento, dados):
        with self._lock:
            item = self._novo_item(evento, dados)
            self._registrar(email, item)
            self._entregar(list(self._filas.get(email, ())), item)
        return item

    def difundir(self, evento, dados):
        with self._lock:
            item = self._novo_item(evento, dados)
            for email, filas in self._filas.items():
                self._registrar(email, item)
                self._entregar(list(filas), item)
        return item

    def pendentes(self, email, ultimo_id):
        with self._lock:
            return [item for item in self._historico.get(email, ()) if item["id"] > ultimo_id]

    def ultimo_id(self):
        with self._lock:
            return self._ultimo_id

    def encerrar(self):
        # Roda no tratador de sinal: nao pode pegar o lock (risco de deadlock).
        self.encerrado = True
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._liberar_filas)
            except RuntimeError:
                pass

    def _liberar_filas(self):
        with self._lock:
            filas = [fila for filas in self._filas.values() for fila in filas]
        for fila in filas:
            if fila.full():
                fila.get_nowait()
            fila.put_nowait(None)

    def conexoes(self):
        with self._lock:
            return sum(len(filas) for filas in self._filas.values())
