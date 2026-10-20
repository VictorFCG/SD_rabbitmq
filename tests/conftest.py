# Configuracao do pytest: deixa importaveis o pacote common e os modulos de cada
# processo, e oferece carregar_main(pasta) para importar o main.py de um processo
# com um nome de modulo unico (todos se chamam main.py).
# Os testes unitarios nao dependem de RabbitMQ, Docker nem de outros processos.
import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ["ms_principal", "ms_estoque", "ms_pagamento", "ms_entrega", "ms_promocoes", "mock_pagamento"]

for pasta in PASTAS:
    sys.path.insert(0, str(RAIZ / pasta))
sys.path.insert(0, str(RAIZ))


def carregar_main(pasta):
    nome = f"{pasta}_main"
    if nome not in sys.modules:
        spec = importlib.util.spec_from_file_location(nome, RAIZ / pasta / "main.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[nome] = modulo
        spec.loader.exec_module(modulo)
    return sys.modules[nome]
