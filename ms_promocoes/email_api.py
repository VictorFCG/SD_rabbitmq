# Envio de e-mails do MS Promocoes pela API externa (EMAIL_PROVIDER no .env):
#   resend  - API REST do Resend (requer RESEND_API_KEY);
#   mailpit - caixa de e-mail local do docker compose (padrao sem RESEND_API_KEY);
#   console - apenas imprime o e-mail no terminal.
# enviar_email nunca lanca excecao: devolve (ok, detalhe).
import math
import threading
import time
import unicodedata
from email.utils import parseaddr

import httpx

from common import config

PROVEDORES = ("resend", "mailpit", "console")
URL_RESEND = "https://api.resend.com/emails"
USER_AGENT = "trab2-sd-ecommerce/1.0"
TIMEOUT = 10.0
INTERVALO_RESEND = 0.6  # segundos entre chamadas (limite de requisicoes da API)
TENTATIVAS_429 = 2
ESPERA_MAXIMA_429 = 10.0
ERROS_DE_COTA = ("daily_quota_exceeded", "monthly_quota_exceeded")

transporte = None  # os testes trocam por httpx.MockTransport

_lock_resend = threading.Lock()
_ultima_chamada_resend = float("-inf")


def texto_ascii(texto):
    # Logs sem acentos (console do Windows).
    normalizado = unicodedata.normalize("NFKD", str(texto))
    return normalizado.encode("ascii", "ignore").decode("ascii")


def provedor():
    escolhido = str(config.EMAIL_PROVEDOR or "").strip().lower()
    if escolhido in PROVEDORES:
        return escolhido
    return "resend" if config.RESEND_API_KEY else "mailpit"


def enviar_email(para_email, para_nome, assunto, html, texto):
    nome_provedor = provedor()
    try:
        if nome_provedor == "resend":
            return _enviar_resend(para_email, assunto, html, texto)
        if nome_provedor == "mailpit":
            return _enviar_mailpit(para_email, para_nome, assunto, html, texto)
        return _imprimir(para_email, para_nome, assunto, texto)
    except httpx.HTTPError as exc:
        return False, f"falha de comunicacao com o {nome_provedor} ({exc.__class__.__name__}: {exc})"
    except Exception as exc:  # noqa: BLE001 - o envio nunca derruba quem chamou
        return False, f"erro inesperado no envio ({exc!r})"


def _esperar(segundos):
    time.sleep(segundos)


def _aguardar_vez_resend():
    global _ultima_chamada_resend
    with _lock_resend:
        espera = _ultima_chamada_resend + INTERVALO_RESEND - time.monotonic()
        if espera > 0:
            _esperar(espera)
        _ultima_chamada_resend = time.monotonic()


def _cliente(trust_env=True):
    # trust_env=False no Mailpit (local) para nao passar pelo proxy do sistema.
    return httpx.Client(timeout=TIMEOUT, transport=transporte, trust_env=trust_env)


def _json(resposta):
    try:
        dados = resposta.json()
    except ValueError:
        return {}
    return dados if isinstance(dados, dict) else {}


def _mensagem_erro(resposta):
    dados = _json(resposta)
    mensagem = dados.get("message") or dados.get("Error") or dados.get("error")
    if not mensagem:
        mensagem = resposta.text.strip()[:300] or resposta.reason_phrase
    return f"HTTP {resposta.status_code}: {mensagem}"


def _espera_429(resposta):
    try:
        segundos = float(resposta.headers.get("retry-after", "1"))
    except ValueError:
        segundos = 1.0
    if not math.isfinite(segundos):
        segundos = 1.0
    return min(max(segundos, 0.0), ESPERA_MAXIMA_429)


def _enviar_resend(para_email, assunto, html, texto):
    if not config.RESEND_API_KEY:
        return False, "RESEND_API_KEY nao configurada"
    corpo = {
        "from": config.EMAIL_REMETENTE,
        "to": [para_email],
        "subject": assunto,
        "html": html,
        "text": texto,
    }
    cabecalhos = {
        "Authorization": f"Bearer {config.RESEND_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    with _cliente() as cliente:
        for tentativa in range(TENTATIVAS_429 + 1):
            _aguardar_vez_resend()
            resposta = cliente.post(URL_RESEND, json=corpo, headers=cabecalhos)
            if resposta.status_code != 429 or tentativa == TENTATIVAS_429:
                break
            if _json(resposta).get("name") in ERROS_DE_COTA:
                break
            _esperar(_espera_429(resposta))
    if resposta.is_success:
        return True, str(_json(resposta).get("id") or "enviado")
    return False, _mensagem_erro(resposta)


def _enviar_mailpit(para_email, para_nome, assunto, html, texto):
    nome_remetente, email_remetente = parseaddr(config.EMAIL_REMETENTE)
    corpo = {
        "From": {"Email": email_remetente, "Name": nome_remetente},
        "To": [{"Email": para_email, "Name": para_nome or ""}],
        "Subject": assunto,
        "HTML": html,
        "Text": texto,
    }
    with _cliente(trust_env=False) as cliente:
        resposta = cliente.post(config.URL_MAILPIT.rstrip("/") + "/api/v1/send", json=corpo)
    if resposta.is_success:
        return True, str(_json(resposta).get("ID") or "enviado")
    return False, _mensagem_erro(resposta)


def _imprimir(para_email, para_nome, assunto, texto):
    destino = f"{para_nome} <{para_email}>" if para_nome else para_email
    linhas = [f"[promocoes] e-mail (console) para {destino}", f"    Assunto: {assunto}"]
    linhas += [f"    {linha}" for linha in texto.splitlines()]
    print(texto_ascii("\n".join(linhas)))
    return True, "impresso no console"
