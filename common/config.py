# Configuracao compartilhada pelos processos (Trab2).
# Enderecos/portas dos servicos HTTP, RabbitMQ, Mock de Pagamento e API externa de
# e-mail. Os valores padrao funcionam localmente sem nenhuma configuracao; cada um
# pode ser sobrescrito por variavel de ambiente ou pelo arquivo .env na raiz do
# projeto (copie .env.example para .env). Variaveis ja definidas no ambiente tem
# prioridade sobre o .env.
# As URLs internas usam 127.0.0.1 (e nao "localhost") para evitar a demora de
# conexao em IPv6 (::1) que ocorre no Windows quando o servidor escuta so em IPv4.
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _valor_env(bruto):
    # Valor entre aspas e mantido inteiro; sem aspas, " # comentario" e removido.
    valor = bruto.strip()
    if valor[:1] in ("'", '"'):
        fim = valor.find(valor[0], 1)
        if fim > 0:
            return valor[1:fim]
    for i, caractere in enumerate(valor):
        if caractere == "#" and (i == 0 or valor[i - 1] in " \t"):
            return valor[:i].rstrip()
    return valor


def _carregar_env(caminho):
    if not caminho.is_file():
        return
    for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        if linha.startswith("export "):
            linha = linha[len("export "):]
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), _valor_env(valor))


_carregar_env(RAIZ / ".env")


def _env(nome, padrao):
    valor = os.environ.get(nome, "").strip()
    return valor or padrao


# --- RabbitMQ -----------------------------------------------------------------
# O docker compose publica as portas so em 127.0.0.1 (IPv4).
RABBITMQ_HOST = _env("RABBITMQ_HOST", "127.0.0.1")

# --- Servidores HTTP (interface de escuta e portas) ---------------------------
HTTP_HOST = _env("HTTP_HOST", "127.0.0.1")
PORTA_PRINCIPAL = int(_env("PRINCIPAL_PORT", "8001"))
PORTA_ESTOQUE = int(_env("ESTOQUE_PORT", "8002"))
PORTA_PAGAMENTO = int(_env("PAGAMENTO_PORT", "8003"))
PORTA_PROMOCOES = int(_env("PROMOCOES_PORT", "8004"))
PORTA_MOCK = int(_env("MOCK_PORT", "8005"))  # o Mock (externo) le a mesma variavel

# --- URLs usadas nas chamadas REST entre processos ----------------------------
URL_ESTOQUE = _env("ESTOQUE_URL", f"http://127.0.0.1:{PORTA_ESTOQUE}")
URL_PROMOCOES = _env("PROMOCOES_URL", f"http://127.0.0.1:{PORTA_PROMOCOES}")
URL_MOCK_PAGAMENTO = _env("MOCK_PAGAMENTO_URL", f"http://127.0.0.1:{PORTA_MOCK}")
# URL de Webhook que o MS Pagamento informa ao Mock ao criar a cobranca.
URL_WEBHOOK_PAGAMENTO = _env(
    "PAGAMENTO_WEBHOOK_URL", f"http://127.0.0.1:{PORTA_PAGAMENTO}/webhooks/pagamento"
)
# Segredo compartilhado com o Mock para assinar (HMAC-SHA256) o corpo do Webhook.
WEBHOOK_SECRET = _env("WEBHOOK_SECRET", "trab2-webhook-secret")

# Endereco do frontend (usado nos links dos e-mails de promocao).
URL_FRONTEND = _env("FRONTEND_URL", "http://localhost:5173")
# Origens do frontend autorizadas (CORS) a chamar o API Gateway.
ORIGENS_FRONTEND = [
    origem.strip()
    for origem in _env(
        "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origem.strip()
]

# --- MS Promocoes / API externa de e-mail ------------------------------------
# EMAIL_PROVIDER: resend | mailpit | console. Vazio = automatico (resend se houver
# RESEND_API_KEY, senao mailpit, a caixa de e-mail local do docker compose).
EMAIL_PROVEDOR = _env("EMAIL_PROVIDER", "").lower()
RESEND_API_KEY = _env("RESEND_API_KEY", "")
EMAIL_REMETENTE = _env("EMAIL_FROM", "Loja SD <onboarding@resend.dev>")
URL_MAILPIT = _env("MAILPIT_URL", "http://127.0.0.1:8025")
# Intervalo (segundos) entre as promocoes geradas automaticamente.
PROMOCAO_INTERVALO = float(_env("PROMOCAO_INTERVALO", "30"))
