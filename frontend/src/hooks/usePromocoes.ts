// Promoções recentes: carga inicial via REST (GET /api/promocoes/recentes, que o
// gateway busca no MS Promoções) + novas promoções recebidas pelo SSE.
import { useCallback, useEffect, useRef, useState } from "react";
import { ehIndisponibilidade, listarPromocoesRecentes, mensagemDeErro } from "../api";
import { paraMs } from "../formatar";
import type { Promocao } from "../tipos";

const MAXIMO = 20;

export interface ErroPromocoes {
  mensagem: string;
  indisponivel: boolean;
}

export interface EstadoPromocoes {
  promocoes: Promocao[];
  /** Ids das promoções que chegaram pelo SSE nesta sessão. */
  aoVivo: ReadonlySet<string>;
  carregando: boolean;
  erro: ErroPromocoes | null;
  recarregar: () => Promise<void>;
  /** Acrescenta uma promoção; retorna false se ela já era conhecida. */
  adicionar: (promocao: Promocao) => boolean;
}

function mesclar(novas: Promocao[], atuais: Promocao[]): Promocao[] {
  const porId = new Map<string, Promocao>();
  for (const promocao of [...novas, ...atuais]) {
    if (!porId.has(promocao.id_promocao)) porId.set(promocao.id_promocao, promocao);
  }
  return [...porId.values()]
    .sort((a, b) => (paraMs(b.criado_em) || 0) - (paraMs(a.criado_em) || 0))
    .slice(0, MAXIMO);
}

export function usePromocoes(): EstadoPromocoes {
  const [promocoes, setPromocoes] = useState<Promocao[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<ErroPromocoes | null>(null);
  const [aoVivo, setAoVivo] = useState<ReadonlySet<string>>(new Set());
  const conhecidas = useRef(new Set<string>());
  const sequencia = useRef(0);

  const recarregar = useCallback(async () => {
    const minha = ++sequencia.current;
    setCarregando(true);
    try {
      const lista = await listarPromocoesRecentes();
      if (minha !== sequencia.current) return;
      lista.forEach((promocao) => conhecidas.current.add(promocao.id_promocao));
      setPromocoes((atuais) => mesclar(lista, atuais));
      setErro(null);
    } catch (falha) {
      if (minha === sequencia.current) {
        setErro({ mensagem: mensagemDeErro(falha), indisponivel: ehIndisponibilidade(falha) });
      }
    } finally {
      if (minha === sequencia.current) setCarregando(false);
    }
  }, []);

  const adicionar = useCallback((promocao: Promocao) => {
    if (conhecidas.current.has(promocao.id_promocao)) return false;
    conhecidas.current.add(promocao.id_promocao);
    setPromocoes((atuais) => mesclar([promocao], atuais));
    setAoVivo((atuais) => new Set(atuais).add(promocao.id_promocao));
    return true;
  }, []);

  useEffect(() => {
    void recarregar();
  }, [recarregar]);

  return { promocoes, aoVivo, carregando, erro, recarregar, adicionar };
}
