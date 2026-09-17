# Catalogo de produtos compartilhado (dados de apoio aos processos).
# Usado pelo MS Principal (visualizar produtos), pelo MS Estoque (verificar
# disponibilidade e reservar) e pelo MS Promocoes (categoria do produto nas
# routing keys promocao.categoria.<A|B|C>).
PRODUTOS = {
    "A1": {"nome": "Notebook Gamer", "categoria": "A", "preco": 4500.00, "estoque": 3},
    "A2": {"nome": "Monitor 27\"", "categoria": "A", "preco": 1500.00, "estoque": 0},
    "B1": {"nome": "Teclado Mecanico", "categoria": "B", "preco": 350.00, "estoque": 10},
    "B2": {"nome": "Mouse Sem Fio", "categoria": "B", "preco": 120.00, "estoque": 8},
    "C1": {"nome": "Pendrive 64GB", "categoria": "C", "preco": 45.00, "estoque": 5},
    "C2": {"nome": "Cabo HDMI", "categoria": "C", "preco": 30.00, "estoque": 7},
}


def estoque_inicial():
    return {codigo: p["estoque"] for codigo, p in PRODUTOS.items()}
