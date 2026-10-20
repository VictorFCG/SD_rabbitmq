# Paginas HTML do Mock de Pagamento "PayMock" (sistema externo).
# Checkout visto pelo cliente na nova aba aberta pelo frontend: dados da cobranca
# e os botoes "Pagamento Aprovado" / "Pagamento Recusado"; depois da escolha, o
# status final e o resultado da notificacao (Webhook) enviada a loja.
# Paginas autocontidas (CSS e scripts inline, sem fontes, imagens ou CDN
# externos), responsivas e em pt-BR. Todo valor dinamico passa por html.escape.
from html import escape

ROTULOS_STATUS = {
    "PENDENTE": "Aguardando pagamento",
    "APROVADO": "Pagamento aprovado",
    "RECUSADO": "Pagamento recusado",
    "CANCELADO": "Cobrança cancelada pela loja",
}
CLASSES_STATUS = {
    "PENDENTE": "pendente",
    "APROVADO": "aprovado",
    "RECUSADO": "recusado",
    "CANCELADO": "cancelado",
}
ICONES_STATUS = {"APROVADO": "✓", "RECUSADO": "✕", "CANCELADO": "–"}
DICA_FECHAR = "Você já pode fechar esta aba e acompanhar o pedido na loja."

ICONE = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='8' fill='%230f766e'/%3E"
    "%3Ctext x='16' y='22' font-family='Arial,sans-serif' font-size='18' font-weight='700' "
    "text-anchor='middle' fill='white'%3EP%3C/text%3E%3C/svg%3E"
)

ESTILO = """
:root {
  color-scheme: light;
  --fundo: #eef2f6;
  --cartao: #ffffff;
  --texto: #0f172a;
  --suave: #556274;
  --borda: #d9e1ea;
  --topo: #0f172a;
  --marca: #0f766e;
  --verde: #15803d;
  --verde-forte: #116530;
  --vermelho: #c0262d;
  --vermelho-forte: #9b1c21;
  --ambar: #b45309;
  --ambar-forte: #92400e;
  --foco: #2563eb;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --fundo: #0b1220;
    --cartao: #121b2c;
    --texto: #e6ebf2;
    --suave: #9aa7b8;
    --borda: #26334a;
    --topo: #060b14;
    --foco: #60a5fa;
  }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--fundo);
  color: var(--texto);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  -webkit-text-size-adjust: 100%;
}
a { color: var(--foco); }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .95em; }
.topo { background: var(--topo); color: #fff; }
.topo-interno { max-width: 600px; margin: 0 auto; padding: 14px 16px; display: flex; align-items: center; gap: 10px; }
.logo {
  width: 32px; height: 32px; border-radius: 9px; background: var(--marca);
  display: inline-flex; align-items: center; justify-content: center; font-weight: 800;
}
.marca { font-size: 1.15rem; font-weight: 700; }
.selo {
  margin-left: auto; padding: 3px 10px; border: 1px solid rgba(255, 255, 255, .35);
  border-radius: 999px; font-size: .75rem; white-space: nowrap;
}
.conteudo { max-width: 600px; margin: 24px auto; padding: 0 16px; }
.cartao {
  background: var(--cartao); border: 1px solid var(--borda); border-radius: 16px; padding: 24px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, .05), 0 10px 30px rgba(15, 23, 42, .07);
}
.sobretitulo { margin: 0 0 4px; color: var(--suave); font-size: .85rem; }
h1 { margin: 0 0 4px; font-size: 1.4rem; line-height: 1.3; overflow-wrap: anywhere; }
.subtitulo { margin: 0; color: var(--suave); overflow-wrap: anywhere; }
.sem-quebra { white-space: nowrap; }
.valor-bloco { margin: 20px 0; padding: 16px; border-radius: 12px; background: rgba(15, 118, 110, .09); }
.valor-bloco span { display: block; color: var(--suave); font-size: .9rem; }
.valor { display: block; font-size: 2.1rem; font-weight: 800; white-space: nowrap; font-variant-numeric: tabular-nums; }
.dados { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 8px 16px; margin: 20px 0; }
.dados dt { color: var(--suave); }
.dados dd { margin: 0; font-weight: 600; overflow-wrap: anywhere; }
.status { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .85rem; font-weight: 700; }
.status-pendente { background: #fef3c7; color: #92400e; }
.status-aprovado { background: #dcfce7; color: #166534; }
.status-recusado { background: #fee2e2; color: #991b1b; }
.status-cancelado { background: #e2e8f0; color: #334155; }
.acoes { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 8px 0 0; }
.botao {
  display: inline-flex; align-items: center; justify-content: center; width: 100%; min-height: 56px;
  padding: 14px 18px; border: 0; border-radius: 12px; color: #fff; cursor: pointer;
  font: inherit; font-size: 1.05rem; font-weight: 700; text-align: center;
  transition: background-color .15s ease, transform .05s ease;
}
.botao:active { transform: translateY(1px); }
.botao:focus-visible { outline: 3px solid var(--foco); outline-offset: 2px; }
.botao:disabled { opacity: .6; cursor: progress; }
.aprovar { background: var(--verde); }
.aprovar:hover:not(:disabled) { background: var(--verde-forte); }
.recusar { background: var(--vermelho); }
.recusar:hover:not(:disabled) { background: var(--vermelho-forte); }
.alerta { background: var(--ambar); }
.alerta:hover:not(:disabled) { background: var(--ambar-forte); }
.secundario { background: transparent; color: var(--texto); border: 1px solid var(--borda); }
.secundario:hover { background: rgba(100, 116, 139, .12); }
.aviso { margin: 16px 0 0; padding: 12px 14px; border-radius: 10px; background: rgba(37, 99, 235, .1); font-weight: 600; }
.nota { margin: 16px 0 0; color: var(--suave); font-size: .9rem; }
.resultado { display: flex; align-items: center; gap: 16px; }
.icone {
  flex: none; width: 56px; height: 56px; border-radius: 50%; color: #fff;
  display: inline-flex; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: 800;
}
.resultado-aprovado .icone { background: var(--verde); }
.resultado-recusado .icone { background: var(--vermelho); }
.resultado-cancelado .icone, .resultado-neutro .icone { background: #64748b; }
.notificacao { margin: 0 0 16px; padding: 12px 14px; border: 1px solid var(--borda); border-radius: 10px; font-weight: 600; }
.notificacao p { margin: 0; }
.notificacao.ok { border-color: rgba(21, 128, 61, .45); background: rgba(21, 128, 61, .09); }
.notificacao.falha { border-color: rgba(192, 38, 45, .45); background: rgba(192, 38, 45, .08); }
.notificacao.enviando { border-color: rgba(37, 99, 235, .4); background: rgba(37, 99, 235, .08); }
.notificacao details { margin-top: 8px; font-weight: 400; }
.notificacao pre { margin: 6px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: .85rem; }
.dica { margin: 0 0 16px; }
.acoes-finais { display: flex; flex-wrap: wrap; gap: 12px; }
.acoes-finais > * { flex: 1 1 200px; }
.acoes-finais form { margin: 0; }
.rodape { max-width: 600px; margin: 0 auto 32px; padding: 0 16px; color: var(--suave); font-size: .8rem; text-align: center; }
@media (max-width: 480px) {
  .cartao { padding: 18px; }
  .acoes { grid-template-columns: 1fr; }
  .dados { grid-template-columns: 1fr; gap: 0; }
  .dados dd { margin-bottom: 10px; }
  .valor { font-size: 1.8rem; }
  .selo { display: none; }
}
"""

TOPO = """<header class="topo">
  <div class="topo-interno">
    <span class="logo" aria-hidden="true">P</span>
    <span class="marca">PayMock</span>
    <span class="selo">Ambiente de simulação</span>
  </div>
</header>"""

RODAPE = """<footer class="rodape">
  PayMock é um provedor de pagamentos fictício, usado apenas para simulação.
  Nenhum valor é cobrado de verdade.
</footer>"""

# Desabilita os botoes no envio (evita clique duplo). Botao desabilitado nao vai
# no formulario, entao o valor do botao clicado e copiado antes para o campo
# oculto "decisao". Sem JavaScript o formulario continua funcionando.
SCRIPT_DECISAO = """<script>
(function () {
  var formulario = document.getElementById("form-decisao");
  if (!formulario) { return; }
  var campo = document.getElementById("campo-decisao");
  var aviso = document.getElementById("aviso-processando");
  var botoes = formulario.querySelectorAll("button[name=decisao]");
  var enviado = false;
  var i;
  for (i = 0; i < botoes.length; i++) {
    botoes[i].addEventListener("click", function (evento) {
      campo.value = evento.currentTarget.value;
    });
  }
  formulario.addEventListener("submit", function (evento) {
    if (enviado) { evento.preventDefault(); return; }
    enviado = true;
    if (evento.submitter && evento.submitter.value) { campo.value = evento.submitter.value; }
    if (aviso) { aviso.hidden = false; }
    if (!campo.value) { return; }
    for (var j = 0; j < botoes.length; j++) { botoes[j].disabled = true; }
  });
  window.addEventListener("pageshow", function (evento) {
    if (evento.persisted) { window.location.reload(); }
  });
})();
</script>"""

SCRIPT_FECHAR = """<script>
function fecharAba() {
  window.close();
  setTimeout(function () {
    var aviso = document.getElementById("aviso-fechar");
    if (aviso) { aviso.hidden = false; }
  }, 400);
}
</script>"""

BOTAO_FECHAR = (
    '<button type="button" class="botao secundario" onclick="fecharAba()">Fechar aba</button>'
)
AVISO_FECHAR = (
    '<p id="aviso-fechar" class="nota" role="status" hidden>O navegador não permitiu fechar '
    "esta aba automaticamente. Feche-a manualmente.</p>"
)


def formatar_reais(valor):
    texto = f"{float(valor):,.2f}"
    return "R$ " + texto.replace(",", "_").replace(".", ",").replace("_", ".")


def descrever_cliente(cliente):
    if isinstance(cliente, dict):
        nome = str(cliente.get("nome") or "").strip()
        email = str(cliente.get("email") or "").strip()
        if nome and email:
            return f"{nome} ({email})"
        return nome or email
    return str(cliente or "").strip()


def descrever_falha(webhook):
    if not webhook:
        return "A notificação ainda não foi enviada à loja."
    tentativas = webhook.get("tentativas") or 0
    apos = f"após {tentativas} tentativa" + ("" if tentativas == 1 else "s")
    if webhook.get("http_status") is not None:
        return f"Falha ao notificar a loja: HTTP {webhook['http_status']} ({apos})."
    erro = webhook.get("erro") or "erro desconhecido"
    return f"Falha ao notificar a loja: {erro} ({apos})."


def pagina(titulo, conteudo, script="", recarregar_em=None):
    recarga = ""
    if recarregar_em:
        recarga = f'<meta http-equiv="refresh" content="{int(recarregar_em)}">\n'
    return (
        "<!doctype html>\n"
        '<html lang="pt-BR">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex">\n'
        f"{recarga}"
        f"<title>{escape(titulo)} · PayMock</title>\n"
        f'<link rel="icon" href="{ICONE}">\n'
        f"<style>{ESTILO}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{TOPO}\n"
        f'<main class="conteudo">{conteudo}</main>\n'
        f"{RODAPE}\n"
        f"{script}\n"
        "</body>\n"
        "</html>\n"
    )


def _dados(cobranca, com_valor):
    linhas = [("Pedido", cobranca["id_pedido"])]
    if cobranca.get("descricao"):
        linhas.append(("Descrição", cobranca["descricao"]))
    cliente = descrever_cliente(cobranca.get("cliente"))
    if cliente:
        linhas.append(("Cliente", cliente))
    if com_valor:
        linhas.append(("Valor", formatar_reais(cobranca["valor"])))
    linhas.append(("Cobrança", cobranca["id_cobranca"]))
    itens = "".join(
        f"<dt>{escape(rotulo)}</dt><dd>{escape(str(valor))}</dd>" for rotulo, valor in linhas
    )
    status = cobranca["status"]
    classe = CLASSES_STATUS.get(status, "cancelado")
    rotulo = escape(ROTULOS_STATUS.get(status, status))
    itens += f'<dt>Status</dt><dd><span class="status status-{classe}">{rotulo}</span></dd>'
    return f'<dl class="dados">{itens}</dl>'


def _conteudo_pendente(cobranca):
    id_cobranca = escape(cobranca["id_cobranca"])
    id_pedido = escape(cobranca["id_pedido"])
    valor = escape(formatar_reais(cobranca["valor"]))
    return f"""
<section class="cartao" aria-labelledby="titulo">
  <p class="sobretitulo">Cobrança {id_cobranca}</p>
  <h1 id="titulo">Pagamento do pedido {id_pedido}</h1>
  <p class="subtitulo">Confira os dados e escolha o resultado do pagamento.</p>
  <div class="valor-bloco"><span>Valor a pagar</span><strong class="valor">{valor}</strong></div>
  {_dados(cobranca, com_valor=False)}
  <form id="form-decisao" class="acoes" method="post" action="/checkout/{id_cobranca}/decisao" autocomplete="off">
    <input type="hidden" id="campo-decisao" name="decisao" value="">
    <button type="submit" class="botao aprovar" name="decisao" value="APROVADO">Pagamento Aprovado</button>
    <button type="submit" class="botao recusar" name="decisao" value="RECUSADO">Pagamento Recusado</button>
  </form>
  <p id="aviso-processando" class="aviso" role="status" hidden>Processando o pagamento e notificando a loja…</p>
  <p class="nota">Ao escolher uma opção, o PayMock avisa a loja automaticamente (Webhook).</p>
</section>"""


def _notificacao(cobranca, enviando):
    if cobranca["status"] not in ("APROVADO", "RECUSADO"):
        return ""
    webhook = cobranca.get("webhook")
    if enviando:
        return (
            '<div class="notificacao enviando" role="status">'
            "Enviando a notificação para a loja…</div>"
        )
    if webhook and webhook.get("entregue"):
        http_status = escape(str(webhook.get("http_status")))
        return (
            '<div class="notificacao ok" role="status">'
            f"Notificação enviada à loja (HTTP {http_status})</div>"
        )
    detalhe = ""
    if webhook and webhook.get("resposta"):
        resposta = escape(webhook["resposta"])
        detalhe = f"<details><summary>Resposta da loja</summary><pre>{resposta}</pre></details>"
    falha = escape(descrever_falha(webhook))
    return f'<div class="notificacao falha" role="alert"><p>{falha}</p>{detalhe}</div>'


def _botao_reenviar(cobranca, enviando):
    webhook = cobranca.get("webhook")
    if cobranca["status"] not in ("APROVADO", "RECUSADO") or enviando:
        return ""
    if webhook and webhook.get("entregue"):
        return ""
    id_cobranca = escape(cobranca["id_cobranca"])
    return (
        f'<form method="post" action="/checkout/{id_cobranca}/reenviar" '
        "onsubmit=\"this.querySelector('button').disabled = true\">"
        '<button type="submit" class="botao alerta">Reenviar notificação</button></form>'
    )


def _conteudo_final(cobranca, enviando):
    status = cobranca["status"]
    classe = CLASSES_STATUS.get(status, "cancelado")
    icone = ICONES_STATUS.get(status, "–")
    titulo = escape(ROTULOS_STATUS.get(status, status))
    id_pedido = escape(cobranca["id_pedido"])
    if status == "CANCELADO":
        subtitulo = f"O pedido {id_pedido} foi cancelado na loja; nenhum pagamento será processado."
    else:
        valor = escape(formatar_reais(cobranca["valor"]))
        subtitulo = f'Pedido {id_pedido} · <span class="sem-quebra">{valor}</span>'
    return f"""
<section class="cartao" aria-labelledby="titulo">
  <div class="resultado resultado-{classe}">
    <span class="icone" aria-hidden="true">{icone}</span>
    <div><h1 id="titulo">{titulo}</h1><p class="subtitulo">{subtitulo}</p></div>
  </div>
  {_dados(cobranca, com_valor=True)}
  {_notificacao(cobranca, enviando)}
  <p class="dica">{DICA_FECHAR}</p>
  <div class="acoes-finais">
    {_botao_reenviar(cobranca, enviando)}
    {BOTAO_FECHAR}
  </div>
  {AVISO_FECHAR}
</section>"""


def pagina_checkout(cobranca, enviando=False):
    if cobranca["status"] == "PENDENTE":
        titulo = f"Pagamento do pedido {cobranca['id_pedido']}"
        return pagina(titulo, _conteudo_pendente(cobranca), SCRIPT_DECISAO)
    titulo = ROTULOS_STATUS.get(cobranca["status"], cobranca["status"])
    recarregar_em = 2 if enviando else None
    return pagina(titulo, _conteudo_final(cobranca, enviando), SCRIPT_FECHAR, recarregar_em)


def _pagina_erro(titulo, icone, mensagem, extra=""):
    conteudo = f"""
<section class="cartao" aria-labelledby="titulo">
  <div class="resultado resultado-neutro">
    <span class="icone" aria-hidden="true">{icone}</span>
    <div><h1 id="titulo">{escape(titulo)}</h1><p class="subtitulo">{mensagem}</p></div>
  </div>
  {extra}
</section>"""
    return pagina(titulo, conteudo, SCRIPT_FECHAR)


def pagina_nao_encontrada(id_cobranca):
    mensagem = f"Não existe cobrança com o código <code>{escape(id_cobranca)}</code>."
    extra = (
        '<p class="nota">Confira o link recebido da loja. As cobranças do PayMock ficam '
        "apenas em memória e deixam de existir quando o simulador é reiniciado.</p>"
        f'<div class="acoes-finais" style="margin-top:16px">{BOTAO_FECHAR}</div>{AVISO_FECHAR}'
    )
    return _pagina_erro("Cobrança não encontrada", "?", mensagem, extra)


def pagina_decisao_invalida(id_cobranca):
    id_html = escape(id_cobranca)
    mensagem = "Escolha “Pagamento Aprovado” ou “Pagamento Recusado” na página da cobrança."
    extra = f'<p class="nota"><a href="/checkout/{id_html}">Voltar para a cobrança {id_html}</a></p>'
    return _pagina_erro("Decisão inválida", "!", mensagem, extra)


def pagina_inicial():
    conteudo = """
<section class="cartao" aria-labelledby="titulo">
  <p class="sobretitulo">Simulador de provedor de pagamentos</p>
  <h1 id="titulo">PayMock está no ar</h1>
  <p>A loja cria as cobranças pela API REST do PayMock e envia ao cliente o link de
  checkout, onde o pagamento é aprovado ou recusado. O resultado volta para a loja por
  Webhook.</p>
  <p class="nota">Documentação da API: <a href="/docs">/docs</a></p>
</section>"""
    return pagina("Simulador de pagamentos", conteudo)
