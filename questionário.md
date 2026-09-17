# Relatório / Questionário — E-commerce com Microsserviços, RabbitMQ e Assinatura Digital

Disciplina: Sistemas Distribuídos · Linguagem: Python (pika + pycryptodome)
Base: implementação deste repositório e requisitos da `specs.txt`.

Este documento funciona como uma "prova comentada": cada resposta justifica a decisão e
aponta o arquivo/trecho/variável onde ela está implementada.

---

## Parte A — Autenticação, integridade e criptografia

### A1. O que garante que o Sub (ex.: MS Estoque) saiba que `pedido.criado` veio do Pub (MS Principal) e não de outro processo?

**Resposta:** a assinatura digital RSA no envelope. O Publicador monta um envelope com
`service`, `routing_key` e `data`, calcula o hash (SHA-256) desse conteúdo, assina o hash
com a **chave privada** do produtor e grava o resultado no campo `Signature`. O consumidor
recalcula o hash do conteúdo, obtém a **chave pública do produtor declarado** e verifica a
assinatura. Como só o MS Principal possui `principal_private.pem`, só ele consegue gerar
uma assinatura que valide com `principal_public.pem`.

- Assinatura na publicação: `common/crypto_utils.py:39-42` (`build_envelope`) e `common/mq.py:30-37`.
- Verificação no consumo: `common/crypto_utils.py:45-59` (`verify_envelope`) e `common/mq.py:64-76`.
- Chaves: `ms_principal/keys/principal_private.pem` / `ms_estoque/keys/principal_public.pem` (geradas por `setup_keys.py:24-38`).

**Ressalva (limitação):** o produtor é **auto-declarado** no campo `service`. A assinatura
cobre esse campo (então não dá para forjar sem a chave privada), mas um consumidor
rigoroso poderia também checar se o `service` é o produtor esperado para aquela routing
key. Não é exigido pela especificação.

### A2. Como o consumidor escolhe qual chave pública usar para validar?

**Resposta:** lê o campo `service` do próprio envelope e carrega
`<key_dir>/<service>_public.pem`.

- `common/crypto_utils.py:52-56`:
  `producer = content.get("service")` → `load_public(Path(key_dir) / f"{producer}_public.pem")`.
- Cada processo tem sua pasta de chaves apontada em `KEYS` (ex.: `ms_estoque/main.py:10`).

**Justificativa:** a especificação manda "os microsserviços devem possuir as chaves públicas
de todos os demais"; por isso `setup_keys.py:30-38` copia as 7 chaves públicas para
**todas** as 7 pastas.

### A3. O que exatamente é assinado e por que o JSON é "canonicalizado"?

**Resposta:** é assinado o objeto `{service, routing_key, data}` (sem o campo `Signature`).
Antes de assinar/verificar, o objeto é serializado de forma determinística:
chaves ordenadas (`sort_keys=True`), sem espaços (`separators=(",", ":")`) e codificado em UTF-8.

- `common/crypto_utils.py:10-11` (`canonical_bytes`).
- Assinatura do conteúdo: `common/crypto_utils.py:40-41`.
- Na verificação, o consumidor remove o `Signature` e refaz o mesmo cálculo: `common/crypto_utils.py:51`.

**Justificativa:** dois `json.dumps` do mesmo dicionário podem gerar bytes diferentes
(ordem de chaves/espaçamento) e a assinatura falharia mesmo com conteúdo íntegro. A
canonicalização garante que produtor e consumidor produzam os **mesmos bytes**.

### A4. Qual algoritmo de hash e de assinatura foi usado?

**Resposta:** hash **SHA-256** e assinatura **RSA PKCS#1 v1.5**.

- Imports: `common/crypto_utils.py:5-7` (`SHA256`, `RSA`, `pkcs1_15`).
- Assinatura: `common/crypto_utils.py:27-28` (`pkcs1_15.new(private_key).sign(SHA256.new(message))`).
- Verificação: `common/crypto_utils.py:31-36`.
- Chaves RSA de 2048 bits: `common/crypto_utils.py:14-16`.

**Justificativa:** é exatamente a biblioteca indicada na `specs.txt`
(pycryptodome, seção PKCS#1 v1.5).

### A5. O que acontece quando a assinatura é inválida?

**Resposta:** o evento é **descartado**, com `ack`, e o consumidor continua vivo.

- `common/mq.py:69-76`: se `valido` for falso, imprime "assinatura invalida ... evento descartado",
  dá `basic_ack` e retorna sem chamar o `handler`.

**Justificativa:** `specs.txt` exige "processar o evento somente se a assinatura for válida;
caso contrário, descartar". O `ack` é correto para não reprocessar lixo.

### A6. Se um intermediário alterar o `data` no caminho, a assinatura continua válida?

**Resposta:** não. Alterar qualquer byte de `data`, `routing_key` ou `service` muda o hash e a
verificação falha (retorna `False`). Testado na prática: envelope válido → `True`; `data`
adulterado → `False`; `service` trocado → `False`.

- Verificação: `common/crypto_utils.py:51-59` (recalcula sobre o conteúdo recebido).
- Falha capturada em `_verify`: `common/crypto_utils.py:32-36` (`except ValueError`).
- Descartado em `common/mq.py:69-76`.

### A7. Como as chaves são geradas, distribuídas e por que a privada não é compartilhada?

**Resposta:** `setup_keys.py` gera um par RSA por processo, salva em um diretório central
temporário, copia a **privada** apenas para a pasta do dono e **todas as públicas** para todas
as pastas; por fim remove o diretório central.

- Geração: `setup_keys.py:24-28`.
- Distribuição (privada só do dono + todas as públicas): `setup_keys.py:30-38`.
- Remoção do diretório central: `setup_keys.py:40`.
- Mapa processo→pasta: `setup_keys.py:9-17`.

**Justificativa:** a chave privada é o segredo que dá o poder de assinar; se fosse
compartilhada, qualquer processo poderia se passar por outro (quebra da autenticidade).

---

## Parte B — Exchange, routing key, binding e filas

### B1. Por que a exchange `eCommerce` é do tipo **direct**?

**Resposta:** porque os eventos do fluxo de pedido devem chegar a destinatários
**específicos**, com correspondência **exata** entre routing key e binding.

- Declaração: `common/mq.py:23-25` (`exchange_type="direct"`).
- Exemplos de bindings exatos: `ms_estoque/main.py:68` (`pedido.criado`, `pedido.excluido`);
  `ms_principal/main.py:76-82` (5 eventos exatos).

**Justificativa:** `specs.txt` define `eCommerce` como Direct e o Tutorial 4 descreve
exatamente esse comportamento (routing key == binding key). Com wildcard aqui não haveria
sentido, pois não queremos que, por exemplo, o Pagamento receba `pedido.criado`.

### B2. Por que a exchange `Promoções` é do tipo **topic**?

**Resposta:** porque o interesse dos consumidores é por **categoria**, e o C2 precisa de um
padrão que cubra todas as categorias.

- Declaração: `common/mq.py:26-28` (`exchange_type="topic"`).
- C2 usa padrão: `consumidor_c2/main.py:26` (`promocao.categoria.*`).
- C1 usa keys exatas: `consumidor_c1/main.py:26` (`promocao.categoria.A`, `...B`).

**Justificativa:** `specs.txt` pede Topic e o padrão de binding com `*` para o C2. O `*`
substitui exatamente uma palavra (a categoria), conforme o Tutorial 5.

### B3. Por que **não** foi usada exchange `fanout`?

**Resposta:** é proibida pela especificação e não é necessária. Nenhuma ocorrência de
`fanout` no código (verificado por busca). As entregas seletivas são feitas por `direct`
(fluxo de pedido) e `topic` (promoções).

- Tipos declarados: `common/mq.py:24` (direct) e `common/mq.py:27` (topic).

### B4. Como o consumidor C2 recebe **todas** as categorias e o C1 só A e B?

**Resposta:** pelas bindings. O C2 vincula sua fila a `promocao.categoria.*`, que casa com
A, B, C (e qualquer outra categoria de uma palavra). O C1 vincula a duas keys exatas.

- C1: `consumidor_c1/main.py:26`.
- C2: `consumidor_c2/main.py:26`.
- O publicador gera as categorias a partir do catálogo: `ms_promocoes/main.py:22`.

### B5. Cada consumidor tem sua própria fila? Por que isso importa?

**Resposta:** sim. `fila.principal`, `fila.estoque`, `fila.pagamento`, `fila.entrega`,
`fila.C1`, `fila.C2`.

- Criadas em `common/mq.py:90` (`queue_declare`), com o nome passado por cada serviço:
  `ms_principal/main.py:74`, `ms_estoque/main.py:66`, `ms_pagamento/main.py:42`,
  `ms_entrega/main.py:38`, `consumidor_c1/main.py:24`, `consumidor_c2/main.py:24`.

**Justificativa:** `specs.txt` exige "cada consumidor deve possuir sua própria fila". Com
fila exclusiva, um evento `pedido.estoque_ok` é entregue **tanto** ao Principal quanto ao
Pagamento (cada um tem sua cópia), em vez de ser consumido por apenas um.

### B6. Dê um exemplo de routing key associada a múltiplas filas.

**Resposta:** `pedido.estoque_ok` é publicado pelo Estoque e chega ao Principal e ao
Pagamento; `pagamento.aprovado` é publicado pelo Pagamento e chega ao Principal e à Entrega.

- `ms_estoque/main.py:37-39` publica `pedido.estoque_ok` (binding em `ms_principal/main.py:80` e `ms_pagamento/main.py:44`).
- `ms_pagamento/main.py:20-22` publica `pagamento.aprovado` (binding em `ms_principal/main.py:77` e `ms_entrega/main.py:40`).

### B7. Quem declara as exchanges e as filas — produtor ou consumidor?

**Resposta:** as **exchanges** são declaradas por ambos (no Publisher e no Consumer); as
**filas e bindings** são declaradas apenas pelos **consumidores**, que são quem se inscreve.

- Exchanges no publisher: `common/mq.py:23-28`.
- Exchange/fila/bindings no consumidor: `common/mq.py:87-92`.

**Justificativa:** declarações idempotentes evitam erro de exchange inexistente. O produtor
não precisa conhecer as filas (desacoplamento): ele só publica na exchange com uma routing
key.

### B8. O que é uma routing key hierárquica neste sistema?

**Resposta:** são strings com palavras separadas por `.` que expressam o domínio do evento:
`pedido.criado`, `pedido.excluido`, `estoque.indisponivel`, `promocao.categoria.A`.

- Constantes/rotas de pedido: `ms_principal/main.py:14-20` e as publicações em `ms_principal/main.py:53,60,124,140`.
- Promoções: `ms_promocoes/main.py:22` (`f"promocao.categoria.{produto['categoria']}"`).

**Justificativa:** permite inscrição por categoria (Tutorial 5) e mantém o padrão de nomes
da `specs.txt`.

---

## Parte C — Confiabilidade e garantias de entrega

### C1. Como o sistema confirma que uma mensagem foi processada?

**Resposta:** por **ack manual** (o pika usa `auto_ack=False` por padrão). O consumidor dá
`basic_ack` após decidir o destino da mensagem (processado, inválido ou malformado).

- JSON inválido → ack e descarte: `common/mq.py:59-62`.
- Assinatura inválida → ack e descarte: `common/mq.py:75`.
- Processado → ack: `common/mq.py:82`.

### C2. O que garante que a fila e a mensagem sobrevivam a um reinício do RabbitMQ?

**Resposta:** a fila é declarada `durable=True` e as mensagens são publicadas com
`delivery_mode=2` (persistentes). As exchanges também são duráveis.

- Fila durável: `common/mq.py:90`.
- Mensagem persistente: `common/mq.py:36` (`pika.BasicProperties(delivery_mode=2)`).
- Exchanges duráveis: `common/mq.py:24,27,88`.

### C3. Por que existe `basic_qos(prefetch_count=1)`?

**Resposta:** para que cada consumidor receba uma mensagem por vez, evitando acúmulo
desnecessário na memória do processo e distribuindo melhor o trabalho.

- `common/mq.py:93`.

### C4. O que **não** está garantido nesta implementação? (limitação)

**Resposta:** não há **publisher confirms** (Tutorial 7). Se um evento for publicado antes de
existir fila vinculada à exchange, ele é descartado silenciosamente. Também não há
persistência do estado dos pedidos/estoque em disco (tudo em memória).

- Publicação sem confirmação: `common/mq.py:32-37`.
- Estado volátil: `ms_principal/main.py:22` (`pedidos = {}`) e `ms_estoque/main.py:12-13` (`estoque`, `reservas`).

**Justificativa:** o Tutorial 7 não está entre os recomendados (1–5) pela `specs.txt`, então
é uma melhoria opcional. Na prática o `run.ps1` sobe os consumidores antes do Principal,
reduzindo o risco.

### C5. Uma mensagem JSON válida porém não-objeto derruba o consumidor?

**Resposta:** não (após correção). `verify_envelope` rejeita qualquer envelope que não seja
dicionário, e o `_on_message` envolve a verificação em `try/except`, descartando e dando ack.

- Tipo checado: `common/crypto_utils.py:46-47`.
- Proteção no consumo: `common/mq.py:64-67`.
- Log de origem seguro: `common/mq.py:70`.

**Justificativa:** sem isso, `envelope.get(...)` lançava `AttributeError` fora do `try`,
encerrando `start_consuming()` e matando o serviço (bug corrigido no commit `1096e16`).

---

## Parte D — Fluxo de negócio, desacoplamento e estado

### D1. Descreva o fluxo completo de um pedido até a entrega.

**Resposta:**
1. Usuário faz o pedido no terminal → Principal gera `id_pedido` e publica `pedido.criado`
   (`ms_principal/main.py:119-126`, status inicial `CRIADO` em `:14`).
2. Estoque consome `pedido.criado`, verifica disponibilidade (`ms_estoque/main.py:17-39`).
3. Se ok: dá baixa e publica `pedido.estoque_ok` (`ms_estoque/main.py:33-39`).
4. Se falta algum produto: publica `estoque.indisponivel` (`ms_estoque/main.py:24-30`).
5. Pagamento consome `pedido.estoque_ok` e sorteia aprovação (`ms_pagamento/main.py:15-27`).
6. Aprovado → `pagamento.aprovado`; recusado → `pagamento.recusado` (`ms_pagamento/main.py:20-26`).
7. Entrega consome `pagamento.aprovado`, gera nota fiscal e publica `pedido.enviado`
   (`ms_entrega/main.py:15-23`).
8. Principal consome os eventos e atualiza o status (`ms_principal/main.py:41-65`).

### D2. Por que o Principal publica `pedido.excluido`?

**Resposta:** porque a especificação determina que, em indisponibilidade de estoque ou
recusa de pagamento, o pedido deve ser cancelado, e o Estoque precisa devolver a reserva.

- Publicação em `ms_principal/main.py:53` (recusa) e `:60` (indisponível).
- Consumo/estorno no estoque: `ms_estoque/main.py:42-49`.

### D3. Como o Estoque sabe quais produtos devolver ao receber `pedido.excluido`?

**Resposta:** mantém um dicionário `reservas` (id do pedido → itens reservados). Ao excluir,
remove a entrada e soma de volta as quantidades.

- Estrutura: `ms_estoque/main.py:13` (`reservas = {}`).
- Grava reserva: `ms_estoque/main.py:35`.
- Estorna: `ms_estoque/main.py:42-49`.

**Justificativa:** o evento `pedido.excluido` carrega só o `id_pedido`; a informação dos
itens reservados é estado local do Estoque.

### D4. Como o Principal atualiza o status dos pedidos?

**Resposta:** em `handle`, cada routing key recebida mapeia para um status e chama
`atualizar(id_pedido, status)`; o dicionário `pedidos` guarda itens, status e nota fiscal.

- `atualizar`: `ms_principal/main.py:28-34`.
- `handle` com os 5 eventos: `ms_principal/main.py:41-65`.
- Eventos consumidos: `ms_principal/main.py:76-82`.
- Estrutura do pedido: `ms_principal/main.py:120-121`.

### D5. Existe alguma chamada direta entre processos?

**Resposta:** não. Toda comunicação é por publicação/consumo no RabbitMQ. Não há `import`
cruzado entre pastas de serviços (apenas a biblioteca compartilhada `common/`, que não é um
canal de comunicação em tempo de execução).

- Envio: `common/mq.py:32-37`; recebimento: `common/mq.py:94`.
- Cada `main.py` importa somente `common.*` e bibliotecas padrão (ex.: `ms_pagamento/main.py:1-7`).

**Justificativa:** a `specs.txt` proíbe chamadas diretas; o desacoplamento é o que permite,
por exemplo, o Pagamento não conhecer o Principal — ambos só conhecem a exchange.

### D6. Por que o MS Principal usa duas conexões (e uma thread)?

**Resposta:** uma conexão `pub_ui` para a thread do menu (publicar pedidos) e outra
`pub_eventos` dentro da thread do consumidor (responder eventos, como publicar
`pedido.excluido`). Isso evita compartilhar uma conexão pika entre threads, que **não é
thread-safe**.

- `pub_ui` criado na thread principal: `ms_principal/main.py:184-185`.
- Thread do consumidor: `ms_principal/main.py:186`.
- `pub_eventos` criado dentro da thread: `ms_principal/main.py:68-70`.
- Uso na UI: `ms_principal/main.py:122` e `:140`; uso no handler: `:53` e `:60`.

### D7. Quais são os riscos/limitações do estado em memória e de concorrência?

**Resposta:**
- Se um serviço cair, perde o estado (`pedidos`, `estoque`, `reservas`) — não há banco.
- Excluir um pedido ainda em `CRIADO` pode correr com a reserva no Estoque (o `pedido.criado`
  pode ser processado depois do `pedido.excluido`), gerando reserva "presa".
- O Principal usa `lock` (`threading.Lock`) para proteger `pedidos` entre a UI e a thread
  consumidora: `ms_principal/main.py:23` e uso em `:29,120,132,146`.

**Justificativa:** são decisões aceitáveis para o escopo acadêmico ("implementação simples e
direta"); ficam registradas como trabalho futuro.

---

## Parte E — Promoções e consumidores

### E1. O que diferencia o C1 do C2?

**Resposta:** as bindings. C1 → categorias A e B (keys exatas); C2 → todas as categorias
(padrão com `*`).

- C1: `consumidor_c1/main.py:20-29` (bindings em `:26`).
- C2: `consumidor_c2/main.py:20-29` (binding em `:26`).

### E2. Os consumidores de promoções chamam algum microsserviço?

**Resposta:** não. Eles apenas consomem a exchange `Promoções` e imprimem a promoção; não
têm nem `Publisher`.

- Consumo: `consumidor_c1/main.py:21-29`, `consumidor_c2/main.py:21-29`.
- Handler somente com `print`: `consumidor_c1/main.py:12-16`, `consumidor_c2/main.py:12-16`.

**Justificativa:** `specs.txt` exige que C1/C2 falem exclusivamente com o RabbitMQ.

### E3. Como o MS Promoções sorteia as promoções?

**Resposta:** em laço infinito, escolhe um produto aleatório do catálogo, um desconto
aleatório, monta a routing key pela categoria e publica; dorme de 3 a 6 segundos.

- `ms_promocoes/main.py:19-34`; routing key em `:22`; desconto em `:21`; intervalo em `:34`.

---

## Resumo das evidências

| Tema | Arquivo principal |
|---|---|
| Assinatura/verificação RSA | `common/crypto_utils.py` |
| Envelope + publicação/consumo | `common/mq.py` |
| Definição das exchanges | `common/mq.py:23-28` |
| Fluxo de pedido | `ms_principal/main.py`, `ms_estoque/main.py`, `ms_pagamento/main.py`, `ms_entrega/main.py` |
| Promoções | `ms_promocoes/main.py`, `consumidor_c1/main.py`, `consumidor_c2/main.py` |
| Chaves | `setup_keys.py` |

## Limitações conhecidas (para arguição)

1. Sem publisher confirms (Tutorial 7) — entrega do produtor não confirmada.
2. Estado em memória — sem persistência de pedidos/estoque entre execuções.
3. Produtor auto-declarado no envelope — não há checagem `routing_key → produtor esperado`.
4. Corrida entre exclusão manual e reserva de estoque.
5. Catálogo compartilhado (`common/catalogo.py`) — simplificação acadêmica; não há banco por serviço.
