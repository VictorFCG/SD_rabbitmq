# Microsservico Principal (specs.txt: "Backend do sistema de E-commerce").
# Interacao com o usuario por meio do terminal: visualizar produtos, realizar
# pedidos, excluir pedidos e consultar seus pedidos e respectivos status.
# Exchange eCommerce (tipo direct).
# Publica:
#   pedido.criado  - cada novo pedido recebido.
#   pedido.excluido - quando um produto nao esta disponivel em estoque ou
#                     quando o pagamento de um pedido e recusado.
# Consome: pagamento.aprovado, pagamento.recusado, pedido.enviado,
#   pedido.estoque_ok, estoque.indisponivel (atualiza o status dos pedidos).
# Fila propria: fila.principal.
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.catalogo import PRODUTOS
from common.mq import EXCHANGE_ECOMMERCE, Consumer, Publisher

SERVICE = "principal"
KEYS = Path(__file__).resolve().parent / "keys"

CRIADO = "CRIADO"
ESTOQUE_OK = "ESTOQUE OK"
PAGO = "PAGO"
ENVIADO = "ENVIADO"
INDISPONIVEL = "INDISPONIVEL"
RECUSADO = "PAGAMENTO RECUSADO"
EXCLUIDO = "EXCLUIDO"

pedidos = {}
lock = threading.Lock()
pub_ui = None
pub_eventos = None


def atualizar(id_pedido, status):
    with lock:
        pedido = pedidos.get(id_pedido)
        if pedido is None:
            return None
        pedido["status"] = status
        return pedido


def publish(routing_key, data):
    pub_eventos.publish(EXCHANGE_ECOMMERCE, routing_key, data)


def handle(routing_key, data):
    id_pedido = data.get("id_pedido")

    if routing_key == "pedido.estoque_ok":
        atualizar(id_pedido, ESTOQUE_OK)
        print(f"\n[principal] pedido {id_pedido}: estoque confirmado")
    elif routing_key == "pagamento.aprovado":
        atualizar(id_pedido, PAGO)
        print(f"\n[principal] pedido {id_pedido}: pagamento aprovado")
    elif routing_key == "pagamento.recusado":
        atualizar(id_pedido, RECUSADO)
        print(f"\n[principal] pedido {id_pedido}: pagamento recusado -> excluindo")
        publish("pedido.excluido", {"id_pedido": id_pedido})
    elif routing_key == "estoque.indisponivel":
        atualizar(id_pedido, INDISPONIVEL)
        print(
            f"\n[principal] pedido {id_pedido}: indisponivel "
            f"{data.get('indisponiveis')} -> excluindo"
        )
        publish("pedido.excluido", {"id_pedido": id_pedido})
    elif routing_key == "pedido.enviado":
        pedido = atualizar(id_pedido, ENVIADO)
        if pedido is not None:
            pedido["nota_fiscal"] = data.get("nota_fiscal")
        print(f"\n[principal] pedido {id_pedido}: enviado")


def consumir_eventos():
    global pub_eventos
    pub_eventos = Publisher(SERVICE, KEYS)
    Consumer(
        SERVICE,
        KEYS,
        "fila.principal",
        EXCHANGE_ECOMMERCE,
        [
            "pagamento.aprovado",
            "pagamento.recusado",
            "pedido.enviado",
            "pedido.estoque_ok",
            "estoque.indisponivel",
        ],
        handle,
    ).start()


def listar_produtos():
    print("\n--- PRODUTOS ---")
    for codigo, p in PRODUTOS.items():
        print(f"  {codigo:>3} | {p['nome']:<20} | cat {p['categoria']} | R$ {p['preco']:.2f}")


def fazer_pedido():
    listar_produtos()
    itens = []
    print("Digite 'codigo quantidade' (ENTER vazio para finalizar):")
    while True:
        entrada = input("  item> ").strip()
        if not entrada:
            break
        partes = entrada.split()
        if len(partes) != 2 or partes[0].upper() not in PRODUTOS:
            print("  item invalido")
            continue
        try:
            quantidade = int(partes[1])
        except ValueError:
            print("  quantidade invalida")
            continue
        if quantidade <= 0:
            print("  quantidade invalida")
            continue
        itens.append({"codigo": partes[0].upper(), "quantidade": quantidade})

    if not itens:
        print("Pedido sem itens.")
        return

    id_pedido = "PED" + uuid.uuid4().hex[:6].upper()
    with lock:
        pedidos[id_pedido] = {"itens": itens, "status": CRIADO, "nota_fiscal": None}
    pub_ui.publish(
        EXCHANGE_ECOMMERCE,
        "pedido.criado",
        {"id_pedido": id_pedido, "itens": itens},
    )
    print(f"Pedido {id_pedido} criado: {itens}")


def excluir_pedido():
    id_pedido = input("ID do pedido: ").strip().upper()
    with lock:
        pedido = pedidos.get(id_pedido)
    if pedido is None:
        print("Pedido nao encontrado.")
        return
    if pedido["status"] == ENVIADO:
        print("Pedido ja enviado, nao pode ser excluido.")
        return
    pub_ui.publish(EXCHANGE_ECOMMERCE, "pedido.excluido", {"id_pedido": id_pedido})
    atualizar(id_pedido, EXCLUIDO)
    print(f"Pedido {id_pedido} excluido.")


def consultar_pedidos():
    with lock:
        if not pedidos:
            print("\nNenhum pedido.")
            return
        print("\n--- PEDIDOS ---")
        for id_pedido, pedido in pedidos.items():
            nota = f" | NF {pedido['nota_fiscal']}" if pedido.get("nota_fiscal") else ""
            itens = ", ".join(
                f"{i['codigo']}x{i['quantidade']}" for i in pedido["itens"]
            )
            print(f"  {id_pedido} | {pedido['status']:<18} | {itens}{nota}")


def menu():
    opcoes = {
        "1": listar_produtos,
        "2": fazer_pedido,
        "3": excluir_pedido,
        "4": consultar_pedidos,
    }
    while True:
        print("\n===== E-COMMERCE =====")
        print("1 - Ver produtos")
        print("2 - Fazer pedido")
        print("3 - Excluir pedido")
        print("4 - Consultar pedidos")
        print("0 - Sair")
        opcao = input("Opcao: ").strip()
        if opcao == "0":
            break
        acao = opcoes.get(opcao)
        if acao is None:
            print("Opcao invalida.")
        else:
            acao()


def main():
    global pub_ui
    pub_ui = Publisher(SERVICE, KEYS)
    threading.Thread(target=consumir_eventos, daemon=True).start()
    print("[principal] iniciado")
    try:
        menu()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
