// Carrinho do cliente, salvo no localStorage por e-mail.
import { useCallback, useEffect, useState } from "react";
import { gravarJSON, lerJSON } from "../armazenamento";
import { ehObjeto } from "../normalizar";

export const QUANTIDADE_MAXIMA = 99;
export const ITENS_MAXIMOS = 20;

export interface ItemCarrinho {
  codigo: string;
  quantidade: number;
}

export type ResultadoAdicao = "ok" | "ajustado" | "limite_itens";

export function limitarQuantidade(quantidade: number): number {
  if (!Number.isFinite(quantidade)) return 1;
  return Math.min(QUANTIDADE_MAXIMA, Math.max(1, Math.trunc(quantidade)));
}

function lerCarrinho(chave: string): ItemCarrinho[] {
  const valor = lerJSON(chave);
  if (!Array.isArray(valor)) return [];
  const itens: ItemCarrinho[] = [];
  for (const item of valor) {
    if (!ehObjeto(item) || typeof item.codigo !== "string" || typeof item.quantidade !== "number") continue;
    if (itens.some((existente) => existente.codigo === item.codigo)) continue;
    itens.push({ codigo: item.codigo, quantidade: limitarQuantidade(item.quantidade) });
  }
  return itens.slice(0, ITENS_MAXIMOS);
}

export function useCarrinho(email: string) {
  const chave = `lojasd.carrinho.${email}`;
  const [itens, setItens] = useState<ItemCarrinho[]>(() => lerCarrinho(chave));

  useEffect(() => {
    gravarJSON(chave, itens);
  }, [chave, itens]);

  const adicionar = useCallback(
    (codigo: string, quantidade: number): ResultadoAdicao => {
      const existente = itens.find((item) => item.codigo === codigo);
      if (!existente && itens.length >= ITENS_MAXIMOS) return "limite_itens";
      const desejada = (existente?.quantidade ?? 0) + quantidade;
      setItens((atuais) => {
        const atual = atuais.find((item) => item.codigo === codigo);
        if (!atual) {
          return atuais.length >= ITENS_MAXIMOS
            ? atuais
            : [...atuais, { codigo, quantidade: limitarQuantidade(quantidade) }];
        }
        return atuais.map((item) =>
          item.codigo === codigo ? { ...item, quantidade: limitarQuantidade(item.quantidade + quantidade) } : item,
        );
      });
      return desejada > QUANTIDADE_MAXIMA ? "ajustado" : "ok";
    },
    [itens],
  );

  const alterar = useCallback((codigo: string, quantidade: number) => {
    setItens((atuais) =>
      atuais.map((item) => (item.codigo === codigo ? { ...item, quantidade: limitarQuantidade(quantidade) } : item)),
    );
  }, []);

  const remover = useCallback((codigo: string) => {
    setItens((atuais) => atuais.filter((item) => item.codigo !== codigo));
  }, []);

  const limpar = useCallback(() => setItens([]), []);

  /** Tira do carrinho os produtos já enviados num pedido (mantém o que entrou depois). */
  const removerEnviados = useCallback((codigos: string[]) => {
    const enviados = new Set(codigos);
    setItens((atuais) => atuais.filter((item) => !enviados.has(item.codigo)));
  }, []);

  return { itens, adicionar, alterar, remover, limpar, removerEnviados };
}
