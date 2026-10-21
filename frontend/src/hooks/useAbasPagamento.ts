// Abre a aba de pagamento ainda no clique (depois de um await o bloqueador de pop-ups a barra),
// com uma página de espera, e a leva à url_pagamento quando ela chega pelo REST ou pelo SSE.
import { useCallback, useEffect, useRef } from "react";
import { ehUrlHttp, juntarFrases } from "../formatar";
import type { MostrarToast } from "../toasts";
import type { Pedido } from "../tipos";

type TomPagina = "espera" | "aviso" | "erro" | "ok";

interface RegistroAba {
  janela: Window | null;
  concluido: boolean;
}

interface MensagemFinal {
  titulo: string;
  tom: TomPagina;
  texto: (pedido: Pedido) => string;
  detalhe: (pedido: Pedido) => string;
}

const MENSAGENS_FINAIS: Record<string, MensagemFinal> = {
  ESTOQUE_INDISPONIVEL: {
    titulo: "Pagamento - estoque indisponível",
    tom: "erro",
    texto: (pedido) => `Estoque indisponível para o pedido ${pedido.id_pedido}.`,
    detalhe: (pedido) => juntarFrases(pedido.motivo, "Nenhuma cobrança foi gerada", "Você já pode fechar esta aba"),
  },
  PAGAMENTO_RECUSADO: {
    titulo: "Pagamento - recusado",
    tom: "erro",
    texto: (pedido) => `Pagamento recusado para o pedido ${pedido.id_pedido}.`,
    detalhe: (pedido) => juntarFrases(pedido.motivo, "Você já pode fechar esta aba"),
  },
  CANCELADO: {
    titulo: "Pagamento - pedido cancelado",
    tom: "erro",
    texto: (pedido) => `O pedido ${pedido.id_pedido} foi cancelado.`,
    detalhe: () => juntarFrases("Nenhum pagamento é necessário", "Você já pode fechar esta aba"),
  },
  PAGAMENTO_APROVADO: {
    titulo: "Pagamento - aprovado",
    tom: "ok",
    texto: (pedido) => `Pagamento aprovado para o pedido ${pedido.id_pedido}.`,
    detalhe: () => juntarFrases("Você já pode fechar esta aba"),
  },
  ENVIADO: {
    titulo: "Pagamento - pedido enviado",
    tom: "ok",
    texto: (pedido) => `O pedido ${pedido.id_pedido} já foi pago e enviado.`,
    detalhe: () => juntarFrases("Você já pode fechar esta aba"),
  },
};

const ESTILO_PAGINA = `
  :root { color-scheme: light dark; --fundo: #f5f6fa; --cartao: #fff; --texto: #1b2030; --suave: #5b6478;
    --destaque: #4f46e5; --aviso: #b45309; --erro: #b91c1c; --ok: #15803d; }
  @media (prefers-color-scheme: dark) { :root { --fundo: #0f1220; --cartao: #171b2d; --texto: #e7e9f2;
    --suave: #a3aac2; --destaque: #818cf8; --aviso: #fbbf24; --erro: #f87171; --ok: #4ade80; } }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px;
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    background: var(--fundo); color: var(--texto); }
  main { max-width: 460px; width: 100%; background: var(--cartao); border-radius: 16px; padding: 32px 24px;
    text-align: center; box-shadow: 0 12px 32px rgba(16, 24, 40, .12); border-top: 4px solid var(--destaque); }
  main.aviso { border-top-color: var(--aviso); }
  main.erro { border-top-color: var(--erro); }
  main.ok { border-top-color: var(--ok); }
  h1 { font-size: 1.15rem; margin: 0 0 8px; }
  p { margin: 0 0 8px; color: var(--suave); }
  .marca { margin-top: 20px; font-weight: 700; color: var(--destaque); }
  .giro { width: 40px; height: 40px; margin: 0 auto 16px; border-radius: 50%;
    border: 4px solid rgba(127, 127, 127, .25); border-top-color: var(--destaque); animation: giro 0.9s linear infinite; }
  @keyframes giro { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .giro { animation-duration: 3s; } }
`;

function escaparHtml(texto: string): string {
  return texto.replace(/[&<>"']/g, (caractere) => `&#${caractere.charCodeAt(0)};`);
}

function escreverPagina(janela: Window, titulo: string, texto: string, detalhe: string, tom: TomPagina): void {
  try {
    const documento = janela.document;
    documento.open();
    documento.write(
      `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">` +
        `<meta name="viewport" content="width=device-width, initial-scale=1">` +
        `<title>${escaparHtml(titulo)}</title><style>${ESTILO_PAGINA}</style></head><body>` +
        `<main class="${tom}" role="status">` +
        (tom === "espera" ? `<div class="giro" aria-hidden="true"></div>` : "") +
        `<h1>${escaparHtml(texto)}</h1>` +
        (detalhe ? `<p>${escaparHtml(detalhe)}</p>` : "") +
        `<p class="marca">Loja SD</p></main></body></html>`,
    );
    documento.close();
  } catch {
    // A aba foi fechada ou já está em outra origem.
  }
}

function navegar(janela: Window, url: string): boolean {
  try {
    janela.location.href = url;
    return true;
  } catch {
    return false;
  }
}

export function useAbasPagamento(mostrarToast: MostrarToast) {
  const registros = useRef(new Map<string, RegistroAba>());

  const abrir = useCallback((): Window | null => {
    let janela: Window | null = null;
    try {
      janela = window.open("", "_blank");
    } catch {
      janela = null;
    }
    if (!janela) return null;
    try {
      janela.opener = null;
    } catch {}
    escreverPagina(
      janela,
      "Pagamento - aguardando",
      "Aguardando a confirmação do estoque e o link de pagamento...",
      "Esta aba vai abrir o Mock de Pagamento automaticamente assim que o MS Estoque confirmar o pedido e o MS Pagamento gerar a cobrança.",
      "espera",
    );
    return janela;
  }, []);

  const fechar = useCallback((janela: Window | null) => {
    try {
      if (janela && !janela.closed) janela.close();
    } catch {}
  }, []);

  /** Chamar antes de aplicar o pedido do POST: o SSE pode já ter trazido o link de pagamento. */
  const registrar = useCallback((idPedido: string, janela: Window | null) => {
    registros.current.set(idPedido, { janela, concluido: false });
    if (!janela || janela.closed) return;
    escreverPagina(
      janela,
      "Pagamento - aguardando",
      `Pedido ${idPedido}: aguardando a confirmação do estoque e o link de pagamento...`,
      `Esta aba abre o Mock de Pagamento sozinha. Se ela não mudar em alguns segundos (por exemplo, se a loja for recarregada ou fechada), use “Pagar agora” no pedido ${idPedido}, em Meus pedidos.`,
      "espera",
    );
  }, []);

  useEffect(() => {
    // Recarregar/fechar a loja ou trocar de usuário: as abas em espera passam a indicar o "Pagar agora".
    const mapa = registros.current;
    const encerrarEsperas = () => {
      for (const [idPedido, registro] of mapa) {
        const { janela } = registro;
        if (registro.concluido || !janela || janela.closed) continue;
        registro.concluido = true;
        escreverPagina(
          janela,
          "Pagamento - continue pela loja",
          `Pedido ${idPedido}: a loja foi recarregada, fechada ou trocou de usuário.`,
          juntarFrases(
            "Esta aba não vai mais abrir o Mock de Pagamento sozinha",
            `Abra a loja com o mesmo e-mail e, em Meus pedidos, use “Pagar agora” no pedido ${idPedido} quando ele estiver Aguardando Pagamento`,
          ),
          "aviso",
        );
      }
    };
    window.addEventListener("pagehide", encerrarEsperas);
    return () => {
      window.removeEventListener("pagehide", encerrarEsperas);
      encerrarEsperas();
    };
  }, []);

  const sincronizar = useCallback(
    (pedido: Pedido) => {
      const registro = registros.current.get(pedido.id_pedido);
      if (!registro || registro.concluido) return;
      const janela = registro.janela && !registro.janela.closed ? registro.janela : null;

      if (pedido.status === "AGUARDANDO_PAGAMENTO" && ehUrlHttp(pedido.url_pagamento)) {
        registro.concluido = true;
        if (janela && navegar(janela, pedido.url_pagamento)) return;
        mostrarToast(
          "aviso",
          `O pagamento do pedido ${pedido.id_pedido} está pronto: clique em “Pagar agora” no card do pedido.`,
          "A aba de pagamento não está aberta",
        );
        return;
      }

      const final = MENSAGENS_FINAIS[pedido.status];
      if (!final) return;
      registro.concluido = true;
      if (janela) escreverPagina(janela, final.titulo, final.texto(pedido), final.detalhe(pedido), final.tom);
    },
    [mostrarToast],
  );

  return { abrir, fechar, registrar, sincronizar };
}
