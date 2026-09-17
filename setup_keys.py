# Geracao e distribuicao das chaves assimetricas (specs.txt, "Criptografia de
# Chave Assimetrica"): "Os microsservicos devem possuir as chaves publicas de
# todos os demais microsservicos. Criem uma pasta para cada microsservico e
# salvem as chaves publicas nessas pastas."
# Gera um par RSA por processo: a chave privada fica somente na pasta do seu
# dono (usada para assinar) e as chaves publicas de todos sao copiadas para a
# pasta de cada processo (usadas para verificar as assinaturas).
import shutil
from pathlib import Path

from common.crypto_utils import generate_keypair
BASE = Path(__file__).parent

SERVICOS = ["principal", "estoque", "pagamento", "entrega", "promocoes", "c1", "c2"]

PASTAS = {
    "principal": "ms_principal",
    "estoque": "ms_estoque",
    "pagamento": "ms_pagamento",
    "entrega": "ms_entrega",
    "promocoes": "ms_promocoes",
    "c1": "consumidor_c1",
    "c2": "consumidor_c2",
}


def main():
    central = BASE / "keys"
    central.mkdir(exist_ok=True)

    for servico in SERVICOS:
        privada, publica = generate_keypair()
        (central / f"{servico}_private.pem").write_bytes(privada)
        (central / f"{servico}_public.pem").write_bytes(publica)
        print(f"chave gerada: {servico}")

    for servico in SERVICOS:
        destino = BASE / PASTAS[servico] / "keys"
        destino.mkdir(parents=True, exist_ok=True)
        shutil.copy(central / f"{servico}_private.pem", destino / f"{servico}_private.pem")
        for outro in SERVICOS:
            shutil.copy(
                central / f"{outro}_public.pem", destino / f"{outro}_public.pem"
            )
        print(f"pasta preparada: {PASTAS[servico]}/keys")

    shutil.rmtree(central, ignore_errors=True)
    print("\nChaves geradas e distribuidas com sucesso.")


if __name__ == "__main__":
    main()
