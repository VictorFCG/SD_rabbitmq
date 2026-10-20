// Acesso ao localStorage tolerante a falhas (modo privado, cota cheia, bloqueio).
import { ehEmailValido, normalizarEmail } from "./formatar";
import { ehObjeto } from "./normalizar";
import type { Cliente } from "./tipos";

export const CHAVE_CLIENTE = "lojasd.cliente";
const CHAVE_ABA = "lojasd.aba";

export function lerJSON(chave: string): unknown {
  try {
    const bruto = window.localStorage.getItem(chave);
    return bruto === null ? null : JSON.parse(bruto);
  } catch {
    return null;
  }
}

export function gravarJSON(chave: string, valor: unknown): void {
  try {
    window.localStorage.setItem(chave, JSON.stringify(valor));
  } catch {}
}

export function remover(chave: string): void {
  try {
    window.localStorage.removeItem(chave);
  } catch {}
}

export function lerCliente(): Cliente | null {
  const valor = lerJSON(CHAVE_CLIENTE);
  if (!ehObjeto(valor) || typeof valor.email !== "string" || !ehEmailValido(valor.email)) return null;
  const nome = typeof valor.nome === "string" ? valor.nome.trim() : "";
  return { nome, email: normalizarEmail(valor.email) };
}

export function gravarCliente(cliente: Cliente): void {
  gravarJSON(CHAVE_CLIENTE, cliente);
}

export function removerCliente(): void {
  remover(CHAVE_CLIENTE);
}

export function lerAba<T extends string>(validas: readonly T[], padrao: T): T {
  const valor = lerJSON(CHAVE_ABA);
  return validas.includes(valor as T) ? (valor as T) : padrao;
}

export function gravarAba(aba: string): void {
  gravarJSON(CHAVE_ABA, aba);
}
