# Microsservico Promocoes: gera promocoes de produtos e as envia por e-mail aos
# clientes interessados na categoria (API de e-mail: Resend ou Mailpit).
# Exchange Promocoes (tipo topic).
# Consome: interesse.promocao (INSCREVER/CANCELAR categorias de um e-mail).
# Publica: promocao.categoria.<A|B|C> a cada PROMOCAO_INTERVALO segundos.
# Fila propria: fila.promocoes.
import math
import random
import re
import sys
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import uvicorn
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import config
from common.catalogo import CATEGORIAS, PRODUTOS
from common.console import desativar_edicao_rapida
from common.mq import EXCHANGE_PROMOCOES, Consumer, Publisher

import email_api
from interesses_db import InteressesDB, normalizar_email

SERVICE = "promocoes"
KEYS = Path(__file__).resolve().parent / "keys"
CAMINHO_DB = Path(__file__).resolve().parent / "data" / "promocoes.db"

NOME_LOJA = "Loja SD"
DESCONTOS = [5, 10, 15, 20, 30, 50]
ATRASO_PRIMEIRA_PROMOCAO = 5  # segundos
INTERVALO_MINIMO = 1.0  # segundos
MAX_RECENTES = 20
REGEX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ESTILO_P = "margin:0 0 12px;line-height:1.5"
RODAPE_INSCRITO = "Para cancelar ou alterar as categorias, acesse a aba Promoções da loja."

publisher = None
db = None
recentes = deque(maxlen=MAX_RECENTES)
lock_recentes = threading.Lock()

app = FastAPI(
    title="MS Promoções",
    description="Interesses dos clientes em promoções por categoria e promoções geradas.",
)


def log(mensagem):
    print(email_api.texto_ascii(f"[promocoes] {mensagem}"))


def agora():
    return datetime.now(timezone.utc).isoformat()


def formatar_reais(valor):
    texto = f"{float(valor):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {texto}"


def nome_categoria(codigo):
    return f"{CATEGORIAS[codigo]} ({codigo})"


def email_valido(email):
    return len(email) <= 254 and REGEX_EMAIL.match(email) is not None


def saudacao(nome):
    return f"Olá, {nome}!" if nome else "Olá!"


def paragrafo(texto):
    return f'<p style="{ESTILO_P}">{escape(texto)}</p>', texto


def lista(itens):
    html = "".join(f'<li style="margin:2px 0">{escape(item)}</li>' for item in itens)
    texto = "\n".join(f"  - {item}" for item in itens)
    return f'<ul style="margin:0 0 12px;padding-left:20px">{html}</ul>', texto


def oferta(promocao):
    produto = promocao["produto"]
    detalhe = f"Código {promocao['codigo']} - {nome_categoria(promocao['categoria'])}"
    de = formatar_reais(promocao["preco_original"])
    por = formatar_reais(promocao["preco_promocional"])
    desconto = promocao["desconto"]
    html = (
        '<div style="margin:0 0 12px;padding:16px;border:1px solid #e5e7eb;'
        'border-radius:8px;background:#f9fafb">'
        f'<div style="font-size:18px;font-weight:bold;color:#111827">{escape(produto)}</div>'
        f'<div style="font-size:13px;color:#6b7280">{escape(detalhe)}</div>'
        '<div style="margin-top:10px">'
        f'<span style="text-decoration:line-through;color:#6b7280">{de}</span> '
        f'<span style="font-size:22px;font-weight:bold;color:#047857">{por}</span> '
        '<span style="padding:2px 8px;border-radius:12px;background:#dc2626;'
        f'color:#ffffff;font-size:13px;font-weight:bold">-{desconto}%</span>'
        "</div></div>"
    )
    texto = f"{produto} com {desconto}% de desconto\n{detalhe}\nDe {de} por {por}"
    return html, texto


def montar_email(titulo, nome, blocos, rodape):
    loja = config.URL_FRONTEND
    corpo_html = "".join(html for html, _ in blocos)
    corpo_texto = "\n\n".join(texto for _, texto in blocos)
    html = (
        '<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:16px;background:#f3f4f6">'
        '<div style="max-width:560px;margin:0 auto;padding:24px;background:#ffffff;'
        'border-radius:8px;font-family:Arial,Helvetica,sans-serif;color:#1f2937">'
        f'<h2 style="margin:0 0 16px;font-size:20px;color:#111827">{escape(titulo)}</h2>'
        f'<p style="{ESTILO_P}">{escape(saudacao(nome))}</p>'
        f"{corpo_html}"
        f'<p style="margin:20px 0"><a href="{escape(loja)}" style="display:inline-block;'
        "padding:10px 18px;background:#2563eb;color:#ffffff;text-decoration:none;"
        f'border-radius:6px;font-weight:bold">Acessar a {escape(NOME_LOJA)}</a></p>'
        f'<p style="margin:0;font-size:12px;color:#6b7280">{escape(rodape)}</p>'
        "</div></body></html>"
    )
    texto = f"{saudacao(nome)}\n\n{corpo_texto}\n\nAcesse a loja: {loja}\n\n{rodape}\n"
    return html, texto


def email_inscricao(nome, categorias):
    blocos = [
        paragrafo(
            f"Sua inscrição para receber as promoções da {NOME_LOJA} foi confirmada. "
            "Você receberá por e-mail as ofertas das categorias:"
        ),
        lista([nome_categoria(c) for c in categorias]),
    ]
    html, texto = montar_email("Inscrição confirmada", nome, blocos, RODAPE_INSCRITO)
    return f"Inscrição confirmada - Promoções {NOME_LOJA}", html, texto


def email_cancelamento(nome, removidas, restantes):
    if not restantes:
        blocos = [
            paragrafo(
                "Sua inscrição foi cancelada: você não receberá mais e-mails de "
                f"promoções da {NOME_LOJA}."
            )
        ]
        rodape = "Para voltar a receber ofertas, inscreva-se novamente na aba Promoções da loja."
        html, texto = montar_email("Inscrição cancelada", nome, blocos, rodape)
        return f"Inscrição cancelada - Promoções {NOME_LOJA}", html, texto
    blocos = [
        paragrafo("Você deixou de receber as promoções das categorias:"),
        lista([nome_categoria(c) for c in removidas]),
        paragrafo("Continua recebendo as ofertas das categorias:"),
        lista([nome_categoria(c) for c in restantes]),
    ]
    html, texto = montar_email("Inscrição atualizada", nome, blocos, RODAPE_INSCRITO)
    return f"Inscrição atualizada - Promoções {NOME_LOJA}", html, texto


def email_promocao(promocao, nome):
    categoria = promocao["categoria_nome"]
    titulo = f"{promocao['produto']} com {promocao['desconto']}% OFF"
    blocos = [
        paragrafo(f"Temos uma promoção na categoria {categoria} para você:"),
        oferta(promocao),
    ]
    rodape = (
        f"Você recebe este e-mail porque se inscreveu nas promoções da categoria "
        f"{categoria}. Para cancelar, acesse a aba Promoções da loja."
    )
    html, texto = montar_email(titulo, nome, blocos, rodape)
    return f"Promoção: {titulo}", html, texto


def enviar(para_email, para_nome, assunto, html, texto, avisar_sucesso=True):
    ok, detalhe = email_api.enviar_email(para_email, para_nome, assunto, html, texto)
    if not ok:
        log(f"falha ao enviar '{assunto}' para {para_email}: {detalhe}")
    elif avisar_sucesso:
        log(f"e-mail '{assunto}' enviado para {para_email} ({email_api.provedor()}: {detalhe})")
    return ok


def ler_categorias(valor):
    if valor is None:
        return [], 0
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list):
        return [], 1
    validas = []
    for item in valor:
        codigo = str(item).strip().upper()
        if codigo in CATEGORIAS and codigo not in validas:
            validas.append(codigo)
    return validas, len(valor)


def inscrever(email, nome, categorias):
    if not categorias:
        log(f"INSCREVER de {email} ignorado: nenhuma categoria valida")
        return
    atuais = db.inscrever(email, nome, categorias)
    log(f"{email} inscrito em {','.join(categorias)} (categorias atuais: {','.join(atuais)})")
    nome = nome or db.nome_de(email)
    enviar(email, nome, *email_inscricao(nome, atuais))


def cancelar(email, nome, categorias):
    antes = db.categorias_de(email)
    nome = nome or db.nome_de(email)
    restantes = db.cancelar(email, categorias)
    removidas = [c for c in antes if c not in restantes]
    if not removidas:
        log(f"CANCELAR de {email}: nenhuma inscricao a remover")
        return
    log(f"{email} cancelou {','.join(removidas)} (restantes: {','.join(restantes) or 'nenhuma'})")
    enviar(email, nome, *email_cancelamento(nome, removidas, restantes))


# PDF (0,1): consome interesse.promocao.
def processar_interesse(data):
    if not isinstance(data, dict):
        log("interesse.promocao ignorado: payload invalido")
        return
    acao = str(data.get("acao") or "").strip().upper()
    email = normalizar_email(data.get("email"))
    nome = data.get("nome")
    nome = (nome.strip() or None) if isinstance(nome, str) else None
    categorias, informadas = ler_categorias(data.get("categorias"))
    if not email_valido(email):
        log(f"interesse.promocao ignorado: e-mail invalido {email[:80]!r}")
    elif acao == "INSCREVER":
        inscrever(email, nome, categorias)
    elif acao == "CANCELAR" and informadas and not categorias:
        log(f"CANCELAR de {email} ignorado: nenhuma categoria valida")
    elif acao == "CANCELAR":
        cancelar(email, nome, categorias)
    else:
        log(f"interesse.promocao ignorado: acao desconhecida {acao[:20]!r}")


def handle(routing_key, data):
    if routing_key == "interesse.promocao":
        processar_interesse(data)


def sortear_promocao():
    codigo, produto = random.choice(list(PRODUTOS.items()))
    desconto = random.choice(DESCONTOS)
    preco = round(float(produto["preco"]), 2)
    return {
        "id_promocao": "PROMO" + uuid.uuid4().hex[:6].upper(),
        "codigo": codigo,
        "produto": produto["nome"],
        "categoria": produto["categoria"],
        "categoria_nome": CATEGORIAS[produto["categoria"]],
        "desconto": desconto,
        "preco_original": preco,
        "preco_promocional": round(preco * (1 - desconto / 100), 2),
        "criado_em": agora(),
    }


def registrar_recente(promocao):
    with lock_recentes:
        recentes.append(promocao)


def listar_recentes():
    with lock_recentes:
        return list(reversed(recentes))


def notificar_interessados(promocao):
    interessados = db.interessados(promocao["categoria"])
    enviados = 0
    for cliente in interessados:
        assunto, html, texto = email_promocao(promocao, cliente["nome"])
        if enviar(cliente["email"], cliente["nome"], assunto, html, texto, avisar_sucesso=False):
            enviados += 1
    return enviados, len(interessados) - enviados


# PDF (0,3): gera a promocao e envia e-mail aos interessados pela API externa (email_api).
def gerar_promocao():
    promocao = sortear_promocao()
    routing_key = f"promocao.categoria.{promocao['categoria']}"
    try:
        publisher.publish(EXCHANGE_PROMOCOES, routing_key, promocao)
    except Exception as exc:  # noqa: BLE001 - os e-mails saem mesmo sem o RabbitMQ
        log(f"falha ao publicar {routing_key} ({exc.__class__.__name__}); enviando so os e-mails")
    registrar_recente(promocao)
    enviados, falhas = notificar_interessados(promocao)
    resumo = (
        f"{routing_key}: {promocao['produto']} {promocao['desconto']}% "
        f"({formatar_reais(promocao['preco_promocional'])}) -> {enviados} e-mail(s) enviado(s)"
    )
    log(resumo + (f", {falhas} falha(s)" if falhas else ""))
    return promocao


def intervalo_promocoes():
    intervalo = config.PROMOCAO_INTERVALO
    if math.isnan(intervalo):
        return INTERVALO_MINIMO
    return min(max(intervalo, INTERVALO_MINIMO), threading.TIMEOUT_MAX)


def gerar_promocoes(parar):
    espera = ATRASO_PRIMEIRA_PROMOCAO
    while not parar.wait(espera):
        try:
            gerar_promocao()
        except Exception as exc:  # noqa: BLE001 - a thread geradora nao pode morrer
            log(f"erro ao gerar promocao: {exc!r}")
        espera = intervalo_promocoes()


@app.get("/interesses/{email:path}", summary="Categorias de interesse de um e-mail")
def consultar_interesses(email: str):
    email = normalizar_email(email)
    return {"email": email, "categorias": db.categorias_de(email)}


@app.get("/promocoes/recentes", summary="Últimas promoções geradas (mais recente primeiro)")
def promocoes_recentes():
    return listar_recentes()


@app.get("/saude", summary="Estado do serviço")
def saude():
    return {"servico": "promocoes", "status": "ok", "provedor_email": email_api.provedor()}


def avisar_provedor_email():
    escolhido = email_api.provedor()
    if config.EMAIL_PROVEDOR and config.EMAIL_PROVEDOR not in email_api.PROVEDORES:
        log(f"EMAIL_PROVIDER '{config.EMAIL_PROVEDOR}' desconhecido; usando {escolhido}")
    if escolhido == "resend" and not config.RESEND_API_KEY:
        log("EMAIL_PROVIDER=resend sem RESEND_API_KEY: os envios vao falhar")
    destinos = {
        "resend": f"API do Resend (remetente {config.EMAIL_REMETENTE})",
        "mailpit": f"Mailpit (caixa de entrada em {config.URL_MAILPIT})",
        "console": "console (apenas impressos no terminal)",
    }
    log(f"e-mails via {destinos[escolhido]}")


def main():
    global publisher, db
    desativar_edicao_rapida()
    db = InteressesDB(CAMINHO_DB)
    publisher = Publisher(SERVICE, KEYS)
    Consumer(
        SERVICE,
        KEYS,
        "fila.promocoes",
        [(EXCHANGE_PROMOCOES, "interesse.promocao")],
        handle,
    ).start_in_thread()
    parar = threading.Event()
    threading.Thread(
        target=gerar_promocoes, args=(parar,), name="gerador-promocoes", daemon=True
    ).start()
    avisar_provedor_email()
    log(
        f"iniciado: uma promocao a cada {intervalo_promocoes():g}s; "
        f"REST em http://{config.HTTP_HOST}:{config.PORTA_PROMOCOES} (docs em /docs)"
    )
    try:
        uvicorn.run(app, host=config.HTTP_HOST, port=config.PORTA_PROMOCOES)
    except KeyboardInterrupt:
        pass
    finally:
        parar.set()


if __name__ == "__main__":
    main()
