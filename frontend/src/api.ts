// Cliente REST do API Gateway (MS Principal). Toda chamada passa por requisitar(),
// que aplica tempo limite e converte falhas de rede e respostas de erro do FastAPI
// ({"detail": string | [{"loc", "msg"}]}) em ErroApi com mensagem legível.
import { juntarFrases } from "./formatar";
import {
  ehObjeto,
  normalizarCategoria,
  normalizarInscricao,
  normalizarPedido,
  normalizarProduto,
  normalizarPromocao,
} from "./normalizar";
import type {
  Categoria,
  Inscricao,
  NovoInteresse,
  NovoPedido,
  Pedido,
  Produto,
  Promocao,
  RespostaInteresse,
} from "./tipos";

export const API_URL = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8001").trim().replace(/\/+$/, "");

export const MENSAGEM_SEM_CONEXAO = "Não foi possível conectar ao API Gateway (MS Principal)";
const MENSAGEM_TEMPO_ESGOTADO = "O API Gateway (MS Principal) não respondeu a tempo. Tente novamente.";
const MENSAGEM_RESPOSTA_INVALIDA = "Resposta inesperada do API Gateway (MS Principal).";
const TEMPO_LIMITE_MS = 15000;

export class ErroApi extends Error {
  /** Código HTTP da resposta; 0 quando a requisição nem chegou ao servidor. */
  readonly status: number;

  constructor(mensagem: string, status: number) {
    super(mensagem);
    this.name = "ErroApi";
    this.status = status;
  }
}

export function mensagemDeErro(erro: unknown): string {
  if (erro instanceof Error && erro.message) return erro.message;
  return "Ocorreu um erro inesperado.";
}

/** O gateway respondeu, mas o serviço por trás dele está fora do ar (502/503/504). */
export function ehIndisponibilidade(erro: unknown): boolean {
  return erro instanceof ErroApi && (erro.status === 502 || erro.status === 503 || erro.status === 504);
}

/** Um item do 422 do gateway, que já vem em português (ex.: "Deve ser maior ou igual a 1."). */
function formatarErroValidacao(item: unknown): string {
  if (typeof item === "string") return item;
  if (!ehObjeto(item)) return "";
  const mensagem = typeof item.msg === "string" ? item.msg : "";
  const caminho = Array.isArray(item.loc)
    ? item.loc.filter((parte) => !["body", "query", "path"].includes(String(parte))).map(String)
    : [];
  return caminho.length ? `${caminho.join(".")}: ${mensagem}` : mensagem;
}

function extrairDetalhe(corpo: unknown): string | null {
  if (typeof corpo === "string") {
    const limpo = corpo.trim();
    return limpo && limpo.length <= 300 && !limpo.startsWith("<") ? limpo : null;
  }
  if (!ehObjeto(corpo)) return null;
  const { detail } = corpo;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const partes = detail.map(formatarErroValidacao).filter(Boolean);
    if (partes.length) return `Dados inválidos: ${juntarFrases(...partes)}`;
  }
  if (typeof corpo.mensagem === "string" && corpo.mensagem.trim()) return corpo.mensagem.trim();
  return null;
}

function mensagemPorStatus(status: number): string {
  if (status === 404) return "Recurso não encontrado no API Gateway.";
  if (status === 409) return "A operação não é permitida no estado atual.";
  if (status === 422) return "Dados inválidos.";
  if (status === 502 || status === 503 || status === 504) {
    return "Serviço temporariamente indisponível. Tente novamente em instantes.";
  }
  if (status >= 500) return "Erro interno no API Gateway (MS Principal).";
  return `Erro ${status} ao chamar o API Gateway.`;
}

async function requisitar(caminho: string, init: RequestInit = {}): Promise<unknown> {
  const controle = new AbortController();
  const tempo = window.setTimeout(() => controle.abort(), TEMPO_LIMITE_MS);
  try {
    const resposta = await fetch(API_URL + caminho, {
      ...init,
      headers: init.body
        ? { Accept: "application/json", "Content-Type": "application/json" }
        : { Accept: "application/json" },
      signal: controle.signal,
    });
    const bruto = await resposta.text();
    let corpo: unknown = null;
    if (bruto) {
      try {
        corpo = JSON.parse(bruto);
      } catch {
        corpo = bruto;
      }
    }
    if (!resposta.ok) {
      throw new ErroApi(extrairDetalhe(corpo) ?? mensagemPorStatus(resposta.status), resposta.status);
    }
    return corpo;
  } catch (erro) {
    if (erro instanceof ErroApi) throw erro;
    throw new ErroApi(controle.signal.aborted ? MENSAGEM_TEMPO_ESGOTADO : MENSAGEM_SEM_CONEXAO, 0);
  } finally {
    window.clearTimeout(tempo);
  }
}

function comoLista<T>(corpo: unknown, converter: (valor: unknown) => T | null): T[] {
  if (!Array.isArray(corpo)) throw new ErroApi(MENSAGEM_RESPOSTA_INVALIDA, 200);
  return corpo.map(converter).filter((item): item is T => item !== null);
}

function comoPedido(corpo: unknown): Pedido {
  const pedido = normalizarPedido(corpo);
  if (!pedido) throw new ErroApi(MENSAGEM_RESPOSTA_INVALIDA, 200);
  return pedido;
}

function comoRespostaInteresse(corpo: unknown, dados: { email: string; categorias: string[] }): RespostaInteresse {
  const objeto = ehObjeto(corpo) ? corpo : {};
  return {
    mensagem: typeof objeto.mensagem === "string" ? objeto.mensagem : "",
    email: typeof objeto.email === "string" ? objeto.email : dados.email,
    categorias: Array.isArray(objeto.categorias)
      ? objeto.categorias.filter((codigo): codigo is string => typeof codigo === "string")
      : dados.categorias,
  };
}

const segmento = encodeURIComponent;

export async function listarProdutos(): Promise<Produto[]> {
  return comoLista(await requisitar("/api/produtos?incluir_esgotados=true"), normalizarProduto);
}

export async function listarCategorias(): Promise<Categoria[]> {
  return comoLista(await requisitar("/api/categorias"), normalizarCategoria);
}

export async function criarPedido(dados: NovoPedido): Promise<Pedido> {
  return comoPedido(await requisitar("/api/pedidos", { method: "POST", body: JSON.stringify(dados) }));
}

export async function listarPedidos(email: string): Promise<Pedido[]> {
  return comoLista(await requisitar(`/api/pedidos?email=${segmento(email)}`), normalizarPedido);
}

export async function cancelarPedido(idPedido: string): Promise<Pedido> {
  return comoPedido(await requisitar(`/api/pedidos/${segmento(idPedido)}`, { method: "DELETE" }));
}

export async function inscreverInteresse(dados: NovoInteresse): Promise<RespostaInteresse> {
  const corpo = await requisitar("/api/interesses", { method: "POST", body: JSON.stringify(dados) });
  return comoRespostaInteresse(corpo, dados);
}

/** Sem categorias, cancela todas as inscrições do e-mail. */
export async function cancelarInteresse(email: string, categorias: string[]): Promise<RespostaInteresse> {
  const consulta = categorias.map((codigo) => `categorias=${segmento(codigo)}`).join("&");
  const corpo = await requisitar(`/api/interesses/${segmento(email)}${consulta ? `?${consulta}` : ""}`, {
    method: "DELETE",
  });
  return comoRespostaInteresse(corpo, { email, categorias });
}

export async function obterInscricao(email: string): Promise<Inscricao> {
  const inscricao = normalizarInscricao(await requisitar(`/api/interesses/${segmento(email)}`), email);
  if (!inscricao) throw new ErroApi(MENSAGEM_RESPOSTA_INVALIDA, 200);
  return inscricao;
}

export async function listarPromocoesRecentes(): Promise<Promocao[]> {
  return comoLista(await requisitar("/api/promocoes/recentes"), normalizarPromocao);
}

/** URL do fluxo SSE do cliente; `ultimoId` pede o reenvio dos eventos perdidos. */
export function urlEventos(email: string, ultimoId?: string): string {
  const base = `${API_URL}/api/eventos?email=${encodeURIComponent(email)}`;
  return ultimoId ? `${base}&ultimo_id=${encodeURIComponent(ultimoId)}` : base;
}
