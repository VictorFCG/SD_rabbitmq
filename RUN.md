# Como executar e testar as APIs

Guia prático para subir o sistema, chamar as APIs com exemplos prontos e cadastrar um e-mail
para receber as promoções. Todas as respostas mostradas aqui foram obtidas com o sistema
rodando. A documentação completa da arquitetura está no [README](README.md).

## 1. Qual e-mail usar?

**Qualquer um.** Não existe um e-mail fixo no sistema. O e-mail serve para duas coisas:

- **Identificar o cliente:** os pedidos e as notificações em tempo real (SSE) são separados
  por e-mail.
- **Receber as promoções:** quem se inscreve recebe por e-mail as ofertas das categorias
  escolhidas.

Para onde vão os e-mails depende da configuração:

| Configuração | Para onde vão os e-mails | Qual e-mail cadastrar |
|---|---|---|
| **Padrão (nada configurado)** | **Mailpit**, a caixa de entrada local: http://localhost:8025. Nada sai da máquina. | Qualquer endereço, por exemplo `ana@exemplo.com` |
| **Resend** (`RESEND_API_KEY` no `.env`) | Caixa de e-mail **real** | O e-mail **da sua conta no Resend**. Sem um domínio verificado, o Resend só entrega para o dono da conta; outros endereços recebem erro 403. |

O remetente é `Loja SD <onboarding@resend.dev>` (variável `EMAIL_FROM`), inclusive no Mailpit.

### Usando o Resend (opcional)

1. Crie uma conta gratuita em https://resend.com e gere uma chave em **API Keys**. Ela começa
   com `re_`.
2. Na raiz do projeto, crie o `.env` a partir do exemplo e preencha a chave:

   ```bash
   cp .env.example .env        # Windows: copy .env.example .env
   ```

   ```dotenv
   RESEND_API_KEY=re_sua_chave_aqui
   ```

3. Suba o sistema de novo (seção 2) e confira em http://127.0.0.1:8004/saude que o campo
   `provedor_email` é `"resend"`.
4. Cadastre nas promoções **o e-mail da sua conta do Resend** (seção 4, passo 3).

> O plano gratuito do Resend envia no máximo 100 e-mails por dia. Cada promoção gera um
> e-mail por inscrito, então ensaie com o Mailpit e use o Resend só na apresentação.

## 2. Subir o sistema

Pré-requisitos: Docker (com o Docker Desktop aberto, no Windows), Python ≥ 3.10 e Node.js
^20.19 ou ≥ 22.12.

```bash
./run.sh                                             # Linux/macOS
powershell -ExecutionPolicy Bypass -File .\run.ps1   # Windows
```

Os scripts sobem o RabbitMQ e o Mailpit no Docker, instalam as dependências, preparam as
chaves RSA e iniciam os 6 processos Python e o frontend. Para não esperar 30 s por cada promoção,
suba com um intervalo menor:

```bash
PROMOCAO_INTERVALO=10 ./run.sh           # Linux/macOS
```

No Windows, ou para deixar fixo, coloque `PROMOCAO_INTERVALO=10` no `.env`.

Quando tudo estiver no ar:

| O quê | Endereço |
|---|---|
| Loja (frontend) | http://localhost:5173 |
| API Gateway (Swagger) | http://127.0.0.1:8001/docs |
| MS Estoque (Swagger) | http://127.0.0.1:8002/docs |
| MS Pagamento (Swagger) | http://127.0.0.1:8003/docs |
| MS Promoções (Swagger) | http://127.0.0.1:8004/docs |
| Mock de Pagamento (Swagger) | http://127.0.0.1:8005/docs |
| Mailpit (e-mails recebidos) | http://localhost:8025 |
| RabbitMQ (painel) | http://localhost:15672 (usuário e senha `guest`) |

## 3. O jeito mais fácil: pelo navegador

- **Loja:** abra http://localhost:5173, entre com um nome e um e-mail, adicione produtos ao
  carrinho e clique em **Finalizar pedido**. Uma nova aba abre o checkout do Mock de
  Pagamento; clique em **Pagamento Aprovado** e acompanhe o status em **Meus pedidos**.
- **Cadastrar o e-mail nas promoções:** na aba **Promoções** da loja, marque as categorias e
  clique em **Salvar preferências**. A confirmação chega no Mailpit (http://localhost:8025).
- **Swagger:** em http://127.0.0.1:8001/docs, abra um endpoint, clique em **Try it out**, cole
  um dos corpos JSON da seção 4 e clique em **Execute**.

## 4. Roteiro pela linha de comando

Os exemplos usam o `curl` no Linux/macOS (ou Git Bash) e o PowerShell no Windows. Defina antes
as variáveis:

```bash
GW=http://127.0.0.1:8001
```

```powershell
$GW = "http://127.0.0.1:8001"
$json = "application/json; charset=utf-8"
```

> No Windows PowerShell, `curl` é um apelido do `Invoke-WebRequest`. Para usar o curl de
> verdade, digite `curl.exe`.

### Passo 1 — Verificar se o gateway está no ar

```bash
curl $GW/api/saude
```

```powershell
Invoke-RestMethod "$GW/api/saude"
```

```json
{"servico": "principal", "status": "ok", "conexoes_sse": 0}
```

### Passo 2 — Listar os produtos (o gateway consulta o MS Estoque via REST)

```bash
curl $GW/api/produtos                          # só os que têm estoque
curl "$GW/api/produtos?incluir_esgotados=true" # todos
```

```powershell
Invoke-RestMethod "$GW/api/produtos?incluir_esgotados=true" | Format-Table codigo, nome, preco, estoque
```

```json
[
  {
    "codigo": "A1",
    "nome": "Notebook Gamer",
    "categoria": "A",
    "categoria_nome": "Computadores e monitores",
    "preco": 4500.0,
    "estoque": 3,
    "disponivel": true
  },
  {
    "codigo": "B1",
    "nome": "Teclado Mecânico",
    "categoria": "B",
    "categoria_nome": "Periféricos",
    "preco": 350.0,
    "estoque": 10,
    "disponivel": true
  }
]
```

Produtos e categorias disponíveis (estoque inicial):

| Código | Produto | Categoria | Preço | Estoque inicial |
|---|---|---|---|---|
| `A1` | Notebook Gamer | A - Computadores e monitores | R$ 4.500,00 | 3 |
| `A2` | Monitor 69" | A - Computadores e monitores | R$ 1.500,00 | 0 (esgotado) |
| `B1` | Teclado Mecânico | B - Periféricos | R$ 350,00 | 10 |
| `B2` | Mouse sem Fio | B - Periféricos | R$ 120,00 | 8 |
| `C1` | Pendrive 420GB | C - Acessórios | R$ 45,00 | 5 |
| `C2` | Cabo HDMI | C - Acessórios | R$ 30,00 | 7 |

As categorias também podem ser consultadas em `GET $GW/api/categorias`.

### Passo 3 — Cadastrar o e-mail para receber as promoções

```bash
curl -X POST $GW/api/interesses \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@exemplo.com", "nome": "Ana", "categorias": ["A", "B", "C"]}'
```

```powershell
$interesse = @{ email = "ana@exemplo.com"; nome = "Ana"; categorias = @("A", "B", "C") } | ConvertTo-Json
Invoke-RestMethod -Method Post "$GW/api/interesses" -ContentType $json -Body $interesse
```

Resposta (HTTP 202, porque a inscrição segue de forma assíncrona pelo RabbitMQ até o
MS Promoções):

```json
{
  "mensagem": "Interesse registrado! Você receberá por e-mail as promoções de: Computadores e monitores, Periféricos, Acessórios.",
  "email": "ana@exemplo.com",
  "categorias": ["A", "B", "C"]
}
```

Campos do corpo:

| Campo | Obrigatório | Valores |
|---|---|---|
| `email` | sim | um e-mail válido (é gravado em minúsculas) |
| `nome` | não | usado na saudação dos e-mails |
| `categorias` | sim | uma ou mais entre `"A"`, `"B"` e `"C"`; soma às categorias que o e-mail já tinha |

**Confirmar que funcionou:**

1. Consulte a inscrição (quem responde é o MS Promoções):

   ```bash
   curl $GW/api/interesses/ana@exemplo.com
   ```

   ```powershell
   Invoke-RestMethod "$GW/api/interesses/ana@exemplo.com"
   ```

   ```json
   {"email": "ana@exemplo.com", "categorias": ["A", "B", "C"]}
   ```

2. Abra o Mailpit (http://localhost:8025). Chega na hora o e-mail **"Inscrição confirmada -
   Promoções Loja SD"**.
3. Espere a próxima promoção (uma a cada `PROMOCAO_INTERVALO` segundos; o padrão é 30). Se a
   categoria dela for uma das escolhidas, chega o e-mail **"Promoção: Mouse sem Fio com 50%
   OFF"**, por exemplo. As últimas promoções geradas podem ser vistas em:

   ```bash
   curl $GW/api/promocoes/recentes
   ```

   ```json
   [
     {
       "id_promocao": "PROMO46C1C0",
       "codigo": "C2",
       "produto": "Cabo HDMI",
       "categoria": "C",
       "categoria_nome": "Acessórios",
       "desconto": 5,
       "preco_original": 30.0,
       "preco_promocional": 28.5,
       "criado_em": "2026-10-19T23:50:34.421556+00:00"
     }
   ]
   ```

### Passo 4 — Acompanhar as notificações em tempo real (SSE)

Abra **outro terminal** e deixe este comando rodando. Ele mostra cada mudança de status dos
pedidos desse e-mail e as promoções, como a loja recebe:

```bash
curl -N "$GW/api/eventos?email=ana@exemplo.com"
```

```powershell
curl.exe -N "http://127.0.0.1:8001/api/eventos?email=ana@exemplo.com"
```

```text
event: conectado
data: {"email": "ana@exemplo.com", "timestamp": "2026-10-19T23:50:02.603387+00:00"}
retry: 3000

event: pedido
data: {"tipo": "pedido.estoque_ok", "mensagem": "Estoque Confirmado para o pedido PEDC51607C0.", "pedido": {...}, "timestamp": "..."}
id: 1792453779171

event: promocao
data: {"id_promocao": "PROMOA135F2", "codigo": "B2", "produto": "Mouse sem Fio", "desconto": 50, ...}
id: 1792453779174
```

Os acentos aparecem como `ç` no texto cru do SSE; o navegador os converte ao ler o JSON.

### Passo 5 — Criar um pedido

```bash
curl -X POST $GW/api/pedidos \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@exemplo.com", "nome": "Ana", "itens": [{"codigo": "B1", "quantidade": 1}]}'
```

```powershell
$pedido = @{ email = "ana@exemplo.com"; nome = "Ana"; itens = @(@{ codigo = "B1"; quantidade = 1 }) } | ConvertTo-Json -Depth 5
$criado = Invoke-RestMethod -Method Post "$GW/api/pedidos" -ContentType $json -Body $pedido
$criado
$id = $criado.id_pedido
```

Resposta (HTTP 201). O pedido nasce em `CRIADO` e o resto acontece de forma assíncrona:

```json
{
  "id_pedido": "PEDC51607C0",
  "email": "ana@exemplo.com",
  "nome": "Ana",
  "itens": [{"codigo": "B1", "quantidade": 1}],
  "valor_total": null,
  "status": "CRIADO",
  "status_label": "Pedido Criado",
  "url_pagamento": null,
  "id_cobranca": null,
  "nota_fiscal": null,
  "codigo_rastreio": null,
  "motivo": null,
  "historico": [
    {
      "status": "CRIADO",
      "status_label": "Pedido Criado",
      "mensagem": "Pedido criado. Aguardando confirmação de estoque.",
      "timestamp": "2026-10-19T23:50:03.108746+00:00"
    }
  ],
  "criado_em": "2026-10-19T23:50:03.108746+00:00",
  "atualizado_em": "2026-10-19T23:50:03.108746+00:00"
}
```

Campos do corpo:

| Campo | Obrigatório | Valores |
|---|---|---|
| `email` | sim | e-mail válido; identifica o dono do pedido |
| `nome` | não | até 100 caracteres |
| `itens` | sim | de 1 a 20 itens |
| `itens[].codigo` | sim | código do produto (tabela do passo 2) |
| `itens[].quantidade` | sim | inteiro de 1 a 99 |

### Passo 6 — Consultar o pedido e pegar o link de pagamento

Cerca de 1 segundo depois, o MS Estoque já reservou o estoque e o MS Pagamento já criou a
cobrança no Mock:

```bash
curl $GW/api/pedidos/PEDC51607C0        # troque pelo seu id_pedido
```

```powershell
$atual = Invoke-RestMethod "$GW/api/pedidos/$id"
$atual.status
$atual.url_pagamento
```

```json
{
  "id_pedido": "PEDC51607C0",
  "status": "AGUARDANDO_PAGAMENTO",
  "status_label": "Aguardando Pagamento",
  "valor_total": 350.0,
  "url_pagamento": "http://127.0.0.1:8005/checkout/COBA66261158E",
  "id_cobranca": "COBA66261158E",
  "...": "..."
}
```

### Passo 7 — Pagar no Mock de Pagamento

**Pelo navegador:** abra o `url_pagamento` e clique em **Pagamento Aprovado** ou **Pagamento
Recusado**.

**Pela linha de comando**, com o mesmo efeito do clique:

```bash
curl -X POST -d "decisao=APROVADO" http://127.0.0.1:8005/checkout/COBA66261158E/decisao
# resposta: HTTP 303 (redireciona para a página da cobrança)
```

```powershell
Invoke-WebRequest -Method Post -Uri "$($atual.url_pagamento)/decisao" -Body @{ decisao = "APROVADO" } -UseBasicParsing | Out-Null
```

Use `decisao=RECUSADO` para recusar. O Mock envia o Webhook (com assinatura HMAC) ao
MS Pagamento, que publica `pagamento.aprovado` ou `pagamento.recusado` no RabbitMQ.

### Passo 8 — Ver o resultado

```bash
curl $GW/api/pedidos/PEDC51607C0
```

```powershell
Invoke-RestMethod "$GW/api/pedidos/$id" | Select-Object id_pedido, status_label, nota_fiscal, codigo_rastreio
```

Depois de uns 2 segundos (o MS Entrega simula a nota fiscal e o despacho):

```json
{
  "id_pedido": "PEDC51607C0",
  "status": "ENVIADO",
  "status_label": "Pedido Enviado",
  "nota_fiscal": "NF-673DFF3A",
  "codigo_rastreio": "SD417465544BR",
  "...": "..."
}
```

O campo `historico` mostra todas as etapas: Pedido Criado → Estoque Confirmado → Aguardando
Pagamento → Pagamento Aprovado → Pedido Enviado.

Para listar todos os pedidos do cliente (mais recentes primeiro):

```bash
curl "$GW/api/pedidos?email=ana@exemplo.com"
```

```powershell
Invoke-RestMethod "$GW/api/pedidos?email=ana@exemplo.com" | Format-Table id_pedido, status_label, valor_total
```

### Passo 9 — Outros cenários

| Cenário | Como fazer | Resultado |
|---|---|---|
| Pagamento recusado | No passo 7, use `decisao=RECUSADO` | Status `PAGAMENTO_RECUSADO`; o gateway publica `pedido.excluido` e o estoque volta |
| Estoque indisponível | Peça o `A2` (esgotado) ou mais unidades do que o estoque, ex.: `{"codigo": "A1", "quantidade": 50}` | Status `ESTOQUE_INDISPONIVEL`, com o motivo |
| Cancelar o pedido | `DELETE $GW/api/pedidos/{id_pedido}` antes de pagar | Status `CANCELADO`; o estoque volta e a cobrança é cancelada no Mock |
| Cancelar pedido já enviado | `DELETE` em um pedido `ENVIADO` | HTTP 409 |

Exemplo de pedido com estoque indisponível:

```bash
curl -X POST $GW/api/pedidos -H "Content-Type: application/json" \
  -d '{"email": "ana@exemplo.com", "itens": [{"codigo": "A2", "quantidade": 1}]}'
```

```json
{
  "status": "ESTOQUE_INDISPONIVEL",
  "status_label": "Estoque Indisponível",
  "motivo": "Estoque Indisponível para o pedido PED3D2C15D9: Monitor 69\" (solicitado 1, disponível 0)."
}
```

Cancelar um pedido:

```bash
curl -X DELETE $GW/api/pedidos/PED53E64A3A
```

```powershell
Invoke-RestMethod -Method Delete "$GW/api/pedidos/$id"
```

### Passo 10 — Cancelar o interesse nas promoções

```bash
curl -X DELETE "$GW/api/interesses/ana@exemplo.com?categorias=C"   # só a categoria C
curl -X DELETE "$GW/api/interesses/ana@exemplo.com"                # todas
```

```powershell
Invoke-RestMethod -Method Delete "$GW/api/interesses/ana@exemplo.com?categorias=C"
Invoke-RestMethod -Method Delete "$GW/api/interesses/ana@exemplo.com"
```

```json
{"mensagem": "Interesse cancelado nas categorias: Acessórios.", "email": "ana@exemplo.com", "categorias": ["C"]}
```

Chega no Mailpit o e-mail "Inscrição atualizada" (ainda restam categorias) ou "Inscrição
cancelada" (nenhuma restou). Depois disso, as promoções não geram mais e-mail para esse
endereço.

### Passo 11 — Erros de validação (HTTP 422)

```bash
curl -X POST $GW/api/pedidos -H "Content-Type: application/json" \
  -d '{"email": "ana", "itens": [{"codigo": "B1", "quantidade": 0}]}'
```

```powershell
try {
    $ruim = @{ email = "ana"; itens = @(@{ codigo = "B1"; quantidade = 0 }) } | ConvertTo-Json -Depth 5
    Invoke-RestMethod -Method Post "$GW/api/pedidos" -ContentType $json -Body $ruim
}
catch {
    $_.ErrorDetails.Message
}
```

```json
{
  "detail": [
    {"type": "value_error", "loc": ["body", "email"], "msg": "E-mail inválido."},
    {"type": "greater_than_equal", "loc": ["body", "itens", 0, "quantidade"], "msg": "Deve ser maior ou igual a 1."}
  ]
}
```

## 5. Chamando os outros serviços diretamente (opcional)

O frontend só fala com o API Gateway, mas cada serviço tem a sua própria API, útil na
apresentação:

```bash
# MS Estoque: produtos persistidos no SQLite
curl "http://127.0.0.1:8002/produtos?disponiveis=true"
curl http://127.0.0.1:8002/produtos/A1

# MS Promoções: inscrições e provedor de e-mail em uso (mailpit ou resend)
curl http://127.0.0.1:8004/interesses/ana@exemplo.com
curl http://127.0.0.1:8004/saude

# MS Pagamento: cobrança de um pedido
curl http://127.0.0.1:8003/pagamentos/PEDC51607C0

# MS Pagamento: Webhook sem a assinatura HMAC é recusado (HTTP 401)
curl -X POST http://127.0.0.1:8003/webhooks/pagamento \
  -H "Content-Type: application/json" \
  -d '{"id_cobranca": "COBA66261158E", "id_pedido": "PEDC51607C0", "status": "APROVADO"}'

# Mock de Pagamento: criar uma cobrança avulsa e consultá-la
curl -X POST http://127.0.0.1:8005/api/cobrancas -H "Content-Type: application/json" \
  -d '{"id_pedido": "TESTE1", "valor": 99.9, "descricao": "Teste", "webhook_url": "http://127.0.0.1:8003/webhooks/pagamento"}'
curl http://127.0.0.1:8005/api/cobrancas/COB0123456789   # use o id_cobranca devolvido
```

A cobrança avulsa abre normalmente no checkout, mas o MS Pagamento responde 404 ao Webhook
dela, porque não foi ele que a criou.

## 6. Problemas comuns

| Sintoma | Causa provável e solução |
|---|---|
| `curl: (7) Failed to connect` | O serviço não está no ar. Rode o `run.sh`/`run.ps1` ou veja os logs em `logs/` (Linux) ou nas janelas (Windows). |
| `GET /api/produtos` → 503 "MS Estoque indisponível" | O MS Estoque está parado. |
| Inscrevi o e-mail mas nada chegou no Mailpit | Veja em http://127.0.0.1:8004/saude qual é o `provedor_email`. Se for `resend`, o e-mail foi para o Resend, não para o Mailpit. Veja também o log do MS Promoções. |
| Resend: "You can only send testing emails to your own email address" | Cadastre o e-mail da sua conta do Resend, ou verifique um domínio no Resend e ajuste o `EMAIL_FROM`. |
| Pedido parado em "Pedido Criado" | O MS Estoque está parado; o evento espera na fila e é processado quando ele volta. |
| Pedido parado em "Estoque Confirmado" | O MS Pagamento está parado. (Se só o Mock estiver fora, o MS Pagamento tenta 3 vezes e o pedido vai para "Pagamento Recusado".) |
| Pedido parado em "Pagamento Aprovado" | O MS Entrega está parado. |
| `{"detail": "Not Found"}` | Confira o caminho: as rotas do gateway começam com `/api/`. |
