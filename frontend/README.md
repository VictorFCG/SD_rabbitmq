# Frontend web - Loja SD (Trab2)

Interface web do e-commerce, escrita em **React 19 + TypeScript** (Vite), ou seja, em
linguagem diferente do backend Python. Ela fala apenas com o **API Gateway (MS Principal)**:

- **REST** para catálogo, pedidos e inscrições em promoções;
- **SSE** (`GET /api/eventos?email=...`) para receber em tempo real as mudanças de status
  dos pedidos do cliente (Estoque Confirmado, Pagamento Aprovado/Recusado, Pedido Enviado...)
  e as novas promoções.

## Pré-requisitos

- Node.js 20.19+ ou 22.12+ (exigência do Vite 8) e npm.
- Backend no ar: RabbitMQ, MS Principal (porta 8001) e os demais microsserviços, além do
  Mock de Pagamento (porta 8005). Sem eles a interface abre normalmente, mas mostra os
  avisos de serviço indisponível e o indicador "Tempo real: desconectado".

## Como rodar

```bash
cd frontend
npm install
npm run dev
```

> **Windows (PowerShell):** se o `npm` falhar com "running scripts is disabled on this system"
> ("a execução de scripts foi desabilitada neste sistema"), troque `npm` por `npm.cmd`
> (`npm.cmd install`, `npm.cmd run dev`) ou rode antes `Set-ExecutionPolicy -Scope Process Bypass`
> nesse terminal (vale só para ele).

Abra http://127.0.0.1:5173 (ou http://localhost:5173), informe nome e e-mail e use a loja.

Outros comandos:

```bash
npm run build     # checagem de tipos (tsc) + build de produção em frontend/dist
npm run preview   # serve o build em http://localhost:4173
```

O `npm run preview` usa outra origem (porta 4173): para o API Gateway aceitar as
requisições, inclua-a no CORS do backend, por exemplo no `.env` da raiz do projeto:
`FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173`.

### Endereço do API Gateway

Por padrão o frontend chama `http://127.0.0.1:8001`. Para trocar, copie `.env.example`
para `.env` (nesta pasta) e ajuste `VITE_API_URL`; reinicie o `npm run dev` depois.

## O que a interface faz

- **Identificação**: nome + e-mail (sem senha), guardados no `localStorage`. O e-mail
  identifica os pedidos e o canal SSE do cliente. "Trocar usuário" volta para esta tela.
- **Catálogo**: produtos e estoque vindos do MS Estoque (via gateway), com carrinho.
  É possível pedir mais do que o estoque exibido (com aviso) para demonstrar o caso
  "Estoque Indisponível", já que a verificação real é feita de forma assíncrona pelo MS Estoque.
- **Finalizar pedido**: uma nova aba é aberta na hora do clique com uma página de espera;
  quando o pedido chega a "Aguardando Pagamento" (evento SSE com a URL do Mock de Pagamento),
  a aba é levada ao checkout do Mock. Se o navegador bloquear a aba, use o botão
  **Pagar agora** no card do pedido.
- **Meus pedidos**: status em tempo real, etapas do pedido, histórico, nota fiscal e
  rastreio, botões "Pagar agora" e "Cancelar pedido".
- **Promoções**: inscrição por e-mail nas categorias de interesse (os e-mails são enviados
  pelo MS Promoções via Resend ou, localmente, para o Mailpit em http://localhost:8025) e
  lista das promoções recebidas em tempo real.
- **Notificações**: avisos rápidos (toasts) e o painel com o histórico de tudo que chegou
  pelo SSE. O indicador "Tempo real" no topo mostra o estado da conexão SSE (reconecta
  sozinho, com reenvio dos eventos perdidos).

Dica: o navegador limita cada origem a ~6 conexões HTTP/1.1 simultâneas e cada aba da loja
mantém uma conexão SSE aberta com o gateway; evite deixar muitas abas da loja abertas.

## Estrutura

```
src/
  api.ts               cliente REST (erros do FastAPI -> mensagens legíveis)
  tipos.ts             formatos do contrato REST/SSE
  status.ts            rótulos, cores e etapas dos status dos pedidos
  normalizar.ts        validação defensiva das respostas
  hooks/useEventos.ts  conexão SSE (reconexão, Last-Event-ID/ultimo_id, deduplicação)
  hooks/usePedidos.ts  estado dos pedidos (mescla REST + SSE pela versão mais recente)
  hooks/useAbasPagamento.ts  aba de pagamento aberta ao finalizar o pedido
  components/          telas e componentes (Catálogo, Carrinho, Meus pedidos, Promoções...)
```
