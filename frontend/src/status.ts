import type { Pedido, StatusPedido, Tom } from "./tipos";

export const ROTULOS_STATUS: Record<StatusPedido, string> = {
  CRIADO: "Pedido Criado",
  ESTOQUE_CONFIRMADO: "Estoque Confirmado",
  AGUARDANDO_PAGAMENTO: "Aguardando Pagamento",
  PAGAMENTO_APROVADO: "Pagamento Aprovado",
  ENVIADO: "Pedido Enviado",
  ESTOQUE_INDISPONIVEL: "Estoque Indisponível",
  PAGAMENTO_RECUSADO: "Pagamento Recusado",
  CANCELADO: "Pedido Cancelado",
};

/** Caminho feliz exibido no indicador de etapas de cada pedido. */
export const FLUXO_PRINCIPAL: readonly StatusPedido[] = [
  "CRIADO",
  "ESTOQUE_CONFIRMADO",
  "AGUARDANDO_PAGAMENTO",
  "PAGAMENTO_APROVADO",
  "ENVIADO",
];

const STATUS_FALHA = new Set<string>(["ESTOQUE_INDISPONIVEL", "PAGAMENTO_RECUSADO", "CANCELADO"]);
const STATUS_SUCESSO = new Set<string>([
  "ESTOQUE_CONFIRMADO",
  "AGUARDANDO_PAGAMENTO",
  "PAGAMENTO_APROVADO",
  "ENVIADO",
]);
const STATUS_CANCELAVEIS = new Set<string>(["CRIADO", "ESTOQUE_CONFIRMADO", "AGUARDANDO_PAGAMENTO"]);
const STATUS_ATIVOS = new Set<string>([
  "CRIADO",
  "ESTOQUE_CONFIRMADO",
  "AGUARDANDO_PAGAMENTO",
  "PAGAMENTO_APROVADO",
]);

export function rotuloStatus(status: string): string {
  return ROTULOS_STATUS[status as StatusPedido] ?? status;
}

export function ehFalha(status: string): boolean {
  return STATUS_FALHA.has(status);
}

export function ehCancelavel(status: string): boolean {
  return STATUS_CANCELAVEIS.has(status);
}

export function ehAtivo(status: string): boolean {
  return STATUS_ATIVOS.has(status);
}

/** Cor do selo de status. */
export function tomDoStatus(status: string): Tom {
  switch (status) {
    case "CRIADO":
      return "neutro";
    case "ESTOQUE_CONFIRMADO":
      return "info";
    case "AGUARDANDO_PAGAMENTO":
      return "aviso";
    case "PAGAMENTO_APROVADO":
    case "ENVIADO":
      return "sucesso";
    default:
      return STATUS_FALHA.has(status) ? "erro" : "neutro";
  }
}

/** Cor do aviso (toast) de uma mudança de status recebida via SSE. */
export function tomDaNotificacao(status: string): Tom {
  if (STATUS_SUCESSO.has(status)) return "sucesso";
  if (status === "ESTOQUE_INDISPONIVEL" || status === "PAGAMENTO_RECUSADO") return "erro";
  return "info";
}

export type EstadoEtapa = "concluida" | "atual" | "falha" | "pendente";

export interface Etapa {
  chave: string;
  rotulo: string;
  estado: EstadoEtapa;
}

function posicaoDaFalha(status: string, ultimaAlcancada: number): number {
  if (status === "ESTOQUE_INDISPONIVEL") return 1;
  if (status === "PAGAMENTO_RECUSADO") return 3;
  return Math.min(ultimaAlcancada + 1, FLUXO_PRINCIPAL.length - 1);
}

/** Etapas do caminho feliz; em uma falha, a etapa em que o pedido parou fica em vermelho. */
export function etapasDoPedido(pedido: Pedido): Etapa[] {
  const alcancados = new Set<string>(pedido.historico.map((entrada) => entrada.status));
  alcancados.add(pedido.status);
  const indiceAtual = FLUXO_PRINCIPAL.indexOf(pedido.status as StatusPedido);

  if (indiceAtual >= 0) {
    return FLUXO_PRINCIPAL.map((status, indice) => ({
      chave: status,
      rotulo: ROTULOS_STATUS[status],
      estado:
        indice < indiceAtual || (indice === indiceAtual && status === "ENVIADO")
          ? "concluida"
          : indice === indiceAtual
            ? "atual"
            : "pendente",
    }));
  }

  if (STATUS_FALHA.has(pedido.status)) {
    let ultimaAlcancada = 0;
    FLUXO_PRINCIPAL.forEach((status, indice) => {
      if (alcancados.has(status)) ultimaAlcancada = indice;
    });
    const falha = posicaoDaFalha(pedido.status, ultimaAlcancada);
    return FLUXO_PRINCIPAL.map((status, indice) => {
      if (indice === falha) {
        return { chave: status, rotulo: pedido.status_label || rotuloStatus(pedido.status), estado: "falha" };
      }
      const concluida = indice < falha && (indice === 0 || alcancados.has(status));
      return { chave: status, rotulo: ROTULOS_STATUS[status], estado: concluida ? "concluida" : "pendente" };
    });
  }

  return FLUXO_PRINCIPAL.map((status) => ({
    chave: status,
    rotulo: ROTULOS_STATUS[status],
    estado: alcancados.has(status) ? "concluida" : "pendente",
  }));
}
