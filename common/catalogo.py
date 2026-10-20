# Catalogo de produtos (dados de apoio aos processos).
# Usado pelo MS Estoque para popular o seu banco na primeira execucao e pelo
# MS Promocoes para sortear os produtos das promocoes (promocao.categoria.<A|B|C>).
CATEGORIAS = {
    "A": "Computadores e monitores",
    "B": "Periféricos",
    "C": "Acessórios",
}

PRODUTOS = {
    "A1": {"nome": "Notebook Gamer", "categoria": "A", "preco": 4500.00, "estoque": 3},
    "A2": {"nome": "Monitor 69\"", "categoria": "A", "preco": 1500.00, "estoque": 0},
    "B1": {"nome": "Teclado Mecânico", "categoria": "B", "preco": 350.00, "estoque": 10},
    "B2": {"nome": "Mouse sem Fio", "categoria": "B", "preco": 120.00, "estoque": 8},
    "C1": {"nome": "Pendrive 420GB", "categoria": "C", "preco": 45.00, "estoque": 5},
    "C2": {"nome": "Cabo HDMI", "categoria": "C", "preco": 30.00, "estoque": 7},
}
