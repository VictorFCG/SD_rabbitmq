// Histórico das notificações recebidas via SSE (painel "Notificações").
import { useCallback, useMemo, useRef, useState } from "react";
import type { Tom } from "../tipos";

const MAXIMO = 100;

export interface Notificacao {
  id: number;
  tom: Tom;
  titulo: string;
  mensagem: string;
  /** Instante do evento (ISO); usa o horário de chegada se o evento não trouxer. */
  momento: string;
}

export function useNotificacoes() {
  const [itens, setItens] = useState<Notificacao[]>([]);
  const [vistasAte, setVistasAte] = useState(0);
  const proximoId = useRef(0);

  const adicionar = useCallback((notificacao: Omit<Notificacao, "id">) => {
    proximoId.current += 1;
    const nova: Notificacao = {
      ...notificacao,
      id: proximoId.current,
      momento: notificacao.momento || new Date().toISOString(),
    };
    setItens((atuais) => [nova, ...atuais].slice(0, MAXIMO));
  }, []);

  const marcarComoLidas = useCallback(() => setVistasAte(proximoId.current), []);

  const limpar = useCallback(() => {
    setItens([]);
    setVistasAte(proximoId.current);
  }, []);

  const naoLidas = useMemo(() => itens.filter((item) => item.id > vistasAte).length, [itens, vistasAte]);

  return { itens, naoLidas, adicionar, marcarComoLidas, limpar };
}
