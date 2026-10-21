// Formatos trocados com o API Gateway (MS Principal) via REST e SSE.

export type StatusPedido =
  | "CRIADO"
  | "ESTOQUE_CONFIRMADO"
  | "AGUARDANDO_PAGAMENTO"
  | "PAGAMENTO_APROVADO"
  | "ENVIADO"
  | "ESTOQUE_INDISPONIVEL"
  | "PAGAMENTO_RECUSADO"
  | "CANCELADO";

export interface Cliente {
  nome: string;
  email: string;
}

export interface Produto {
  codigo: string;
  nome: string;
  categoria: string;
  categoria_nome: string;
  preco: number;
  estoque: number;
  disponivel: boolean;
}

export interface Categoria {
  codigo: string;
  nome: string;
}

export interface ItemPedido {
  codigo: string;
  quantidade: number;
  nome: string | null;
  categoria: string | null;
  preco_unitario: number | null;
  subtotal: number | null;
}

export interface EntradaHistorico {
  status: string;
  status_label: string;
  mensagem: string;
  timestamp: string;
}

export interface Pedido {
  id_pedido: string;
  email: string;
  nome: string | null;
  itens: ItemPedido[];
  valor_total: number | null;
  /** Um dos valores de StatusPedido (string para tolerar status desconhecidos). */
  status: string;
  status_label: string;
  url_pagamento: string | null;
  id_cobranca: string | null;
  nota_fiscal: string | null;
  codigo_rastreio: string | null;
  motivo: string | null;
  historico: EntradaHistorico[];
  criado_em: string;
  atualizado_em: string;
}

export interface NovoPedido {
  email: string;
  nome: string | null;
  itens: { codigo: string; quantidade: number }[];
}

export interface Promocao {
  id_promocao: string;
  codigo: string;
  produto: string;
  categoria: string;
  categoria_nome: string;
  desconto: number;
  preco_original: number;
  preco_promocional: number;
  criado_em: string;
}

export interface Inscricao {
  email: string;
  categorias: string[];
}

export interface NovoInteresse {
  email: string;
  nome: string | null;
  categorias: string[];
}

export interface RespostaInteresse {
  mensagem: string;
  email: string;
  categorias: string[];
}

export interface EventoConectado {
  email: string;
  timestamp: string;
}

export interface EventoPedido {
  tipo: string;
  mensagem: string;
  pedido: Pedido;
  timestamp: string;
}

export type Tom = "sucesso" | "erro" | "aviso" | "info" | "neutro" | "promocao";
