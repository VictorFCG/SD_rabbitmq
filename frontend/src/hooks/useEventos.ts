// Conexão SSE com o API Gateway (GET /api/eventos?email=...).
// - O EventSource reconecta sozinho (reenviando Last-Event-ID) quando a conexão cai;
//   se ele desistir (readyState CLOSED, ex.: resposta HTTP de erro), um novo é criado
//   com espera exponencial, pedindo os eventos perdidos via ?ultimo_id=.
// - Eventos repetidos (mesmo id SSE ou mesmo conteúdo, comum após um reenvio) são
//   ignorados.
// - Fecha a conexão ao desmontar ou trocar de e-mail; funciona com o StrictMode (o
//   efeito pode montar, desmontar e montar de novo).
import { useEffect, useEffectEvent, useState } from "react";
import { urlEventos } from "../api";
import { ehObjeto, normalizarEventoPedido, normalizarPromocao } from "../normalizar";
import type { EventoConectado, EventoPedido, Promocao } from "../tipos";

export type EstadoConexao = "conectando" | "conectado" | "reconectando" | "desconectado";

export interface ManipuladoresEventos {
  aoConectar?: (dados: EventoConectado) => void;
  aoPedido?: (dados: EventoPedido) => void;
  aoPromocao?: (dados: Promocao) => void;
}

const FALHAS_ATE_DESCONECTADO = 3;
const ESPERA_INICIAL_MS = 1000;
const ESPERA_MAXIMA_MS = 30000;
const MAXIMO_LEMBRADOS = 500;

function lerDados(evento: Event): unknown {
  if (!(evento instanceof MessageEvent) || typeof evento.data !== "string") return null;
  try {
    return JSON.parse(evento.data);
  } catch {
    return null;
  }
}

function idDoEvento(evento: Event): string {
  return evento instanceof MessageEvent ? evento.lastEventId : "";
}

/** Conjunto limitado: esquece os itens mais antigos ao passar do limite. */
function criarMemoria(limite: number) {
  const itens = new Set<string>();
  const ordem: string[] = [];
  return {
    tem: (chave: string) => itens.has(chave),
    lembrar(chave: string) {
      if (itens.has(chave)) return;
      itens.add(chave);
      ordem.push(chave);
      if (ordem.length > limite) itens.delete(ordem.shift() as string);
    },
  };
}

export function useEventos(email: string, manipuladores: ManipuladoresEventos): EstadoConexao {
  const [estado, setEstado] = useState<EstadoConexao>("conectando");

  const aoConectar = useEffectEvent((dados: EventoConectado) => manipuladores.aoConectar?.(dados));
  const aoPedido = useEffectEvent((dados: EventoPedido) => manipuladores.aoPedido?.(dados));
  const aoPromocao = useEffectEvent((dados: Promocao) => manipuladores.aoPromocao?.(dados));

  useEffect(() => {
    let ativo = true;
    let fonte: EventSource | null = null;
    let espera: number | undefined;
    let falhasSeguidas = 0;
    let recriacoes = 0;
    let ultimoId = "";
    const idsVistos = criarMemoria(MAXIMO_LEMBRADOS);
    const conteudosVistos = criarMemoria(MAXIMO_LEMBRADOS);

    // true se o evento é novo; registra id e conteúdo para descartar repetições.
    const ehNovo = (evento: Event, chaveConteudo: string): boolean => {
      const id = idDoEvento(evento);
      if (id) ultimoId = id;
      if ((id && idsVistos.tem(id)) || conteudosVistos.tem(chaveConteudo)) return false;
      if (id) idsVistos.lembrar(id);
      conteudosVistos.lembrar(chaveConteudo);
      return true;
    };

    const marcarConectado = () => {
      falhasSeguidas = 0;
      recriacoes = 0;
      setEstado("conectado");
    };

    const registrarFalha = () => {
      falhasSeguidas += 1;
      const desconectado = !navigator.onLine || falhasSeguidas >= FALHAS_ATE_DESCONECTADO;
      setEstado(desconectado ? "desconectado" : "reconectando");
    };

    const agendarRecriacao = () => {
      window.clearTimeout(espera);
      const atraso = Math.min(ESPERA_MAXIMA_MS, ESPERA_INICIAL_MS * 2 ** recriacoes) + Math.random() * 500;
      recriacoes += 1;
      espera = window.setTimeout(conectar, atraso);
    };

    function conectar() {
      if (!ativo) return;
      fonte?.close();
      const atual = new EventSource(urlEventos(email, ultimoId));
      fonte = atual;
      const valido = () => ativo && fonte === atual;

      atual.onopen = () => {
        if (valido()) marcarConectado();
      };
      atual.onerror = () => {
        if (!valido()) return;
        registrarFalha();
        if (atual.readyState === EventSource.CLOSED) {
          atual.close();
          agendarRecriacao();
        }
      };
      atual.addEventListener("conectado", (evento) => {
        if (!valido()) return;
        marcarConectado();
        const dados = lerDados(evento);
        aoConectar({
          email: ehObjeto(dados) && typeof dados.email === "string" ? dados.email : email,
          timestamp: ehObjeto(dados) && typeof dados.timestamp === "string" ? dados.timestamp : "",
        });
      });
      atual.addEventListener("pedido", (evento) => {
        if (!valido()) return;
        const dados = normalizarEventoPedido(lerDados(evento));
        if (!dados) return;
        const chave = `pedido|${dados.pedido.id_pedido}|${dados.tipo}|${dados.pedido.status}|${dados.timestamp}`;
        if (ehNovo(evento, chave)) aoPedido(dados);
      });
      atual.addEventListener("promocao", (evento) => {
        if (!valido()) return;
        const dados = normalizarPromocao(lerDados(evento));
        if (dados && ehNovo(evento, `promocao|${dados.id_promocao}`)) aoPromocao(dados);
      });
    }

    const aoVoltarOnline = () => {
      if (!ativo || (fonte && fonte.readyState !== EventSource.CLOSED)) return;
      window.clearTimeout(espera);
      recriacoes = 0;
      conectar();
    };
    const aoFicarOffline = () => {
      if (ativo) setEstado("desconectado");
    };

    window.addEventListener("online", aoVoltarOnline);
    window.addEventListener("offline", aoFicarOffline);
    conectar();

    return () => {
      ativo = false;
      window.clearTimeout(espera);
      window.removeEventListener("online", aoVoltarOnline);
      window.removeEventListener("offline", aoFicarOffline);
      fonte?.close();
      fonte = null;
    };
  }, [email]);

  return estado;
}
