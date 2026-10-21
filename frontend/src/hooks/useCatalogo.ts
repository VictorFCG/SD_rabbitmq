import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listarProdutos, mensagemDeErro } from "../api";
import type { Produto } from "../tipos";

export interface Catalogo {
  produtos: Produto[] | null;
  porCodigo: Map<string, Produto>;
  carregando: boolean;
  erro: string | null;
  atualizadoEm: number | null;
  recarregar: () => Promise<void>;
  agendarRecarga: (atrasoMs?: number) => void;
}

export function useCatalogo(): Catalogo {
  const [produtos, setProdutos] = useState<Produto[] | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [atualizadoEm, setAtualizadoEm] = useState<number | null>(null);
  const sequencia = useRef(0);
  const temporizador = useRef<number | undefined>(undefined);

  const recarregar = useCallback(async () => {
    const minha = ++sequencia.current;
    setCarregando(true);
    try {
      const lista = await listarProdutos();
      if (minha !== sequencia.current) return;
      setProdutos([...lista].sort((a, b) => a.codigo.localeCompare(b.codigo)));
      setErro(null);
      setAtualizadoEm(Date.now());
    } catch (falha) {
      if (minha === sequencia.current) setErro(mensagemDeErro(falha));
    } finally {
      if (minha === sequencia.current) setCarregando(false);
    }
  }, []);

  const agendarRecarga = useCallback(
    (atrasoMs = 1200) => {
      window.clearTimeout(temporizador.current);
      temporizador.current = window.setTimeout(() => void recarregar(), atrasoMs);
    },
    [recarregar],
  );

  useEffect(() => {
    void recarregar();
    return () => window.clearTimeout(temporizador.current);
  }, [recarregar]);

  const porCodigo = useMemo(
    () => new Map((produtos ?? []).map((produto) => [produto.codigo, produto] as const)),
    [produtos],
  );

  return { produtos, porCodigo, carregando, erro, atualizadoEm, recarregar, agendarRecarga };
}
