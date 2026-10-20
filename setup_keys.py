# Geracao e distribuicao das chaves assimetricas (specs.txt, "Criptografia de
# Chave Assimetrica"): "Os microsservicos devem possuir as chaves publicas de
# todos os demais microsservicos. Criem uma pasta para cada microsservico e
# salvem as chaves publicas nessas pastas."
# Gera um par RSA por processo: a chave privada fica somente na pasta do seu
# dono (usada para assinar) e as chaves publicas de todos sao copiadas para a
# pasta de cada processo (usadas para verificar as assinaturas).
# Trab2: os consumidores C1/C2 deram lugar aos clientes cadastrados por e-mail
# (MS Promocoes), entao so os 5 microsservicos do backend possuem chaves. O Mock
# de Pagamento e um sistema externo e nao participa do RabbitMQ.
# As chaves so sao geradas quando faltam ou estao inconsistentes: assim os eventos
# que ficaram nas filas duraveis continuam validos depois de reiniciar o sistema.
# Para gerar chaves novas mesmo assim:  python setup_keys.py --force
import shutil
import sys
from pathlib import Path

from Crypto.PublicKey import RSA

from common.crypto_utils import SERVICOS, generate_keypair

BASE = Path(__file__).parent

PASTAS = {
    "principal": "ms_principal",
    "estoque": "ms_estoque",
    "pagamento": "ms_pagamento",
    "entrega": "ms_entrega",
    "promocoes": "ms_promocoes",
}


def chaves_completas(base):
    # Cada pasta precisa da propria chave privada e das publicas de todos, e cada
    # copia publica precisa corresponder a chave privada do seu dono.
    try:
        publicas = {}
        for servico in SERVICOS:
            privada = base / PASTAS[servico] / "keys" / f"{servico}_private.pem"
            publicas[servico] = RSA.import_key(privada.read_bytes()).publickey()
        for servico in SERVICOS:
            pasta = base / PASTAS[servico] / "keys"
            for outro in SERVICOS:
                copia = RSA.import_key((pasta / f"{outro}_public.pem").read_bytes())
                if (copia.n, copia.e) != (publicas[outro].n, publicas[outro].e):
                    return False
    except (OSError, ValueError, IndexError, TypeError):
        return False
    return True


def gerar_chaves(base):
    central = base / "keys"
    central.mkdir(exist_ok=True)

    for servico in SERVICOS:
        privada, publica = generate_keypair()
        (central / f"{servico}_private.pem").write_bytes(privada)
        (central / f"{servico}_public.pem").write_bytes(publica)
        print(f"chave gerada: {servico}")

    for servico in SERVICOS:
        destino = base / PASTAS[servico] / "keys"
        destino.mkdir(parents=True, exist_ok=True)
        shutil.copy(central / f"{servico}_private.pem", destino / f"{servico}_private.pem")
        for outro in SERVICOS:
            shutil.copy(
                central / f"{outro}_public.pem", destino / f"{outro}_public.pem"
            )
        print(f"pasta preparada: {PASTAS[servico]}/keys")

    shutil.rmtree(central, ignore_errors=True)
    print("\nChaves geradas e distribuidas com sucesso.")


def main():
    if "--force" not in sys.argv[1:] and chaves_completas(BASE):
        print("Chaves RSA ja existem e estao consistentes; mantidas (python setup_keys.py --force gera novas).")
        return
    gerar_chaves(BASE)


if __name__ == "__main__":
    main()
