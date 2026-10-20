// Pedidos do cliente. As atualizações chegam por três caminhos (resposta do POST,
// eventos SSE e a listagem GET /api/pedidos usada para ressincronizar) e em qualquer
// ordem; por isso cada pedido só é substituído por uma versão pelo menos tão recente
// (ver ehVersaoMaisRecente). O mapa fica num ref para leitura síncrona; o estado
// React é apenas a lista ordenada derivada dele.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { listarPedidos, mensagemDeErro } from "../api";
import { ehVersaoMaisRecente, ordenarPedidos } from "../normalizar";
import type { Pedido } from "../tipos";

export interface EstadoPedidos {
  pedidos: Pedido[];
  carregando: boolean;
  /** true depois da primeira resposta (com sucesso ou erro) da listagem. */
  carregou: boolean;
  erro: string | null;
  aplicar: (recebidos: Pedido[]) => void;
  recarregar: () => Promise<void>;
}

export function usePedidos(email: string, aoAtualizar: (pedido: Pedido) => void): EstadoPedidos {
  const mapa = useRef(new Map<string, Pedido>());
  const inseridoEm = useRef(new Map<string, number>());
  const sequencia = useRef(0);
  const refAoAtualizar = useRef(aoAtualizar);
  const [pedidos, setPedidos] = useState<Pedido[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [carregou, setCarregou] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  useLayoutEffect(() => {
    refAoAtualizar.current = aoAtualizar;
  });

  const publicar = useCallback(() => {
    setPedidos(ordenarPedidos([...mapa.current.values()]));
  }, []);

  const aplicar = useCallback(
    (recebidos: Pedido[]) => {
      let mudou = false;
      const afetados = new Set<string>();
      for (const recebido of recebidos) {
        const atual = mapa.current.get(recebido.id_pedido);
        if (!atual) {
          mapa.current.set(recebido.id_pedido, recebido);
          inseridoEm.current.set(recebido.id_pedido, Date.now());
          mudou = true;
        } else if (ehVersaoMaisRecente(recebido, atual) && JSON.stringify(recebido) !== JSON.stringify(atual)) {
          mapa.current.set(recebido.id_pedido, recebido);
          mudou = true;
        }
        afetados.add(recebido.id_pedido);
      }
      if (mudou) publicar();
      for (const id of afetados) {
        const pedido = mapa.current.get(id);
        if (pedido) refAoAtualizar.current(pedido);
      }
    },
    [publicar],
  );

  const recarregar = useCallback(async () => {
    const minha = ++sequencia.current;
    const inicio = Date.now();
    setCarregando(true);
    try {
      const lista = await listarPedidos(email);
      if (minha !== sequencia.current) return;
      // Pedidos que o servidor não conhece mais saem da lista, exceto os que chegaram
      // (POST/SSE) depois que esta consulta começou.
      const noServidor = new Set(lista.map((pedido) => pedido.id_pedido));
      let removeu = false;
      for (const id of [...mapa.current.keys()]) {
        if (!noServidor.has(id) && (inseridoEm.current.get(id) ?? 0) < inicio) {
          mapa.current.delete(id);
          inseridoEm.current.delete(id);
          removeu = true;
        }
      }
      if (removeu) publicar();
      aplicar(lista);
      setErro(null);
    } catch (falha) {
      if (minha === sequencia.current) setErro(mensagemDeErro(falha));
    } finally {
      if (minha === sequencia.current) {
        setCarregando(false);
        setCarregou(true);
      }
    }
  }, [email, aplicar, publicar]);

  useEffect(() => {
    void recarregar();
  }, [recarregar]);

  return { pedidos, carregando, carregou, erro, aplicar, recarregar };
}
