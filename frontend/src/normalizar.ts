// Validação defensiva das respostas REST e dos eventos SSE: campos ausentes ou com
// tipo inesperado viram valores padrão em vez de derrubar a interface.
import { paraMs } from "./formatar";
import { rotuloStatus } from "./status";
import type {
  Categoria,
  EntradaHistorico,
  EventoPedido,
  Inscricao,
  ItemPedido,
  Pedido,
  Produto,
  Promocao,
} from "./tipos";

type Objeto = Record<string, unknown>;

export function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

function texto(valor: unknown, padrao = ""): string {
  if (typeof valor === "string") return valor;
  if (typeof valor === "number" && Number.isFinite(valor)) return String(valor);
  return padrao;
}

function textoOuNulo(valor: unknown): string | null {
  const resultado = texto(valor);
  return resultado ? resultado : null;
}

function numeroOuNulo(valor: unknown): number | null {
  if (valor === null || valor === undefined || valor === "") return null;
  const numero = typeof valor === "number" ? valor : Number(valor);
  return Number.isFinite(numero) ? numero : null;
}

function numero(valor: unknown, padrao = 0): number {
  return numeroOuNulo(valor) ?? padrao;
}

function listaDe<T>(valor: unknown, converter: (item: unknown) => T | null): T[] {
  if (!Array.isArray(valor)) return [];
  return valor.map(converter).filter((item): item is T => item !== null);
}

export function normalizarProduto(valor: unknown): Produto | null {
  if (!ehObjeto(valor) || typeof valor.codigo !== "string" || !valor.codigo) return null;
  const estoque = Math.max(0, Math.trunc(numero(valor.estoque)));
  return {
    codigo: valor.codigo,
    nome: texto(valor.nome, valor.codigo),
    categoria: texto(valor.categoria),
    categoria_nome: texto(valor.categoria_nome) || texto(valor.categoria),
    preco: numero(valor.preco),
    estoque,
    disponivel: typeof valor.disponivel === "boolean" ? valor.disponivel : estoque > 0,
  };
}

export function normalizarCategoria(valor: unknown): Categoria | null {
  if (!ehObjeto(valor) || typeof valor.codigo !== "string" || !valor.codigo) return null;
  return { codigo: valor.codigo, nome: texto(valor.nome, valor.codigo) };
}

function normalizarItem(valor: unknown): ItemPedido | null {
  if (!ehObjeto(valor) || typeof valor.codigo !== "string") return null;
  return {
    codigo: valor.codigo,
    quantidade: Math.max(0, Math.trunc(numero(valor.quantidade))),
    nome: textoOuNulo(valor.nome),
    categoria: textoOuNulo(valor.categoria),
    preco_unitario: numeroOuNulo(valor.preco_unitario),
    subtotal: numeroOuNulo(valor.subtotal),
  };
}

function normalizarHistorico(valor: unknown): EntradaHistorico | null {
  if (!ehObjeto(valor)) return null;
  const status = texto(valor.status);
  return {
    status,
    status_label: texto(valor.status_label) || rotuloStatus(status),
    mensagem: texto(valor.mensagem),
    timestamp: texto(valor.timestamp),
  };
}

export function normalizarPedido(valor: unknown): Pedido | null {
  if (!ehObjeto(valor) || typeof valor.id_pedido !== "string" || !valor.id_pedido) return null;
  const status = texto(valor.status, "CRIADO");
  const criadoEm = texto(valor.criado_em);
  return {
    id_pedido: valor.id_pedido,
    email: texto(valor.email),
    nome: textoOuNulo(valor.nome),
    itens: listaDe(valor.itens, normalizarItem),
    valor_total: numeroOuNulo(valor.valor_total),
    status,
    status_label: texto(valor.status_label) || rotuloStatus(status),
    url_pagamento: textoOuNulo(valor.url_pagamento),
    id_cobranca: textoOuNulo(valor.id_cobranca),
    nota_fiscal: textoOuNulo(valor.nota_fiscal),
    codigo_rastreio: textoOuNulo(valor.codigo_rastreio),
    motivo: textoOuNulo(valor.motivo),
    historico: listaDe(valor.historico, normalizarHistorico),
    criado_em: criadoEm,
    atualizado_em: texto(valor.atualizado_em) || criadoEm,
  };
}

export function normalizarPromocao(valor: unknown): Promocao | null {
  if (!ehObjeto(valor) || typeof valor.id_promocao !== "string" || !valor.id_promocao) return null;
  const precoOriginal = numero(valor.preco_original);
  return {
    id_promocao: valor.id_promocao,
    codigo: texto(valor.codigo),
    produto: texto(valor.produto, texto(valor.codigo, "Produto")),
    categoria: texto(valor.categoria),
    categoria_nome: texto(valor.categoria_nome) || texto(valor.categoria),
    desconto: numero(valor.desconto),
    preco_original: precoOriginal,
    preco_promocional: numero(valor.preco_promocional, precoOriginal),
    criado_em: texto(valor.criado_em),
  };
}

export function normalizarInscricao(valor: unknown, emailPadrao: string): Inscricao | null {
  if (!ehObjeto(valor)) return null;
  const categorias = Array.isArray(valor.categorias)
    ? valor.categorias.filter((codigo): codigo is string => typeof codigo === "string")
    : [];
  return { email: texto(valor.email, emailPadrao), categorias: [...new Set(categorias)].sort() };
}

export function normalizarEventoPedido(valor: unknown): EventoPedido | null {
  if (!ehObjeto(valor)) return null;
  const pedido = normalizarPedido(valor.pedido);
  if (!pedido) return null;
  return {
    tipo: texto(valor.tipo),
    mensagem: texto(valor.mensagem),
    pedido,
    timestamp: texto(valor.timestamp) || pedido.atualizado_em,
  };
}

/** Pedidos mais novos primeiro (data de criação; empate pelo id). */
export function ordenarPedidos(pedidos: Pedido[]): Pedido[] {
  return [...pedidos].sort((a, b) => {
    const diferenca = (paraMs(b.criado_em) || 0) - (paraMs(a.criado_em) || 0);
    return diferenca || b.id_pedido.localeCompare(a.id_pedido);
  });
}

/**
 * Indica se `novo` é uma versão pelo menos tão recente quanto `atual` do mesmo pedido.
 * Cada transição de status acrescenta uma entrada ao histórico, então o tamanho do
 * histórico decide; empate pelo campo atualizado_em.
 */
export function ehVersaoMaisRecente(novo: Pedido, atual: Pedido): boolean {
  if (novo.historico.length !== atual.historico.length) {
    return novo.historico.length > atual.historico.length;
  }
  const msNovo = paraMs(novo.atualizado_em);
  const msAtual = paraMs(atual.atualizado_em);
  if (Number.isNaN(msNovo) || Number.isNaN(msAtual)) return true;
  return msNovo >= msAtual;
}
