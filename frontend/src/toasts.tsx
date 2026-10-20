// Avisos rápidos (toasts): somem sozinhos após ~6 s, pausam com o mouse/foco em cima
// e ignoram repetições de uma mensagem que ainda está na tela (ex.: a resposta REST
// e o evento SSE do mesmo cancelamento).
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Icone, type NomeIcone } from "./components/Icone";
import type { Tom } from "./tipos";

export type MostrarToast = (tom: Tom, mensagem: string, titulo?: string) => void;

interface Toast {
  id: number;
  tom: Tom;
  mensagem: string;
  titulo?: string;
}

const DURACAO_MS = 6000;
const MAXIMO_NA_TELA = 4; // em telas de até 480px, o CSS mostra só os 2 mais recentes

const ICONES: Record<Tom, NomeIcone> = {
  sucesso: "sucesso",
  erro: "erro",
  aviso: "alerta",
  info: "info",
  neutro: "info",
  promocao: "etiqueta",
};

const ContextoToasts = createContext<MostrarToast | null>(null);

export function ProvedorToasts({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const atuais = useRef<Toast[]>([]);
  const temporizadores = useRef(new Map<number, number>());
  const proximoId = useRef(0);

  const publicar = useCallback((lista: Toast[]) => {
    atuais.current = lista;
    setToasts(lista);
  }, []);

  const pausar = useCallback((id: number) => {
    const temporizador = temporizadores.current.get(id);
    if (temporizador !== undefined) window.clearTimeout(temporizador);
    temporizadores.current.delete(id);
  }, []);

  const remover = useCallback(
    (id: number) => {
      pausar(id);
      if (atuais.current.some((toast) => toast.id === id)) {
        publicar(atuais.current.filter((toast) => toast.id !== id));
      }
    },
    [pausar, publicar],
  );

  const agendar = useCallback(
    (id: number) => {
      pausar(id);
      temporizadores.current.set(
        id,
        window.setTimeout(() => remover(id), DURACAO_MS),
      );
    },
    [pausar, remover],
  );

  const mostrar = useCallback<MostrarToast>(
    (tom, mensagem, titulo) => {
      const repetido = atuais.current.find((toast) => toast.mensagem === mensagem && toast.titulo === titulo);
      if (repetido) {
        agendar(repetido.id);
        return;
      }
      proximoId.current += 1;
      const novo: Toast = { id: proximoId.current, tom, mensagem, titulo };
      const lista = [...atuais.current, novo];
      while (lista.length > MAXIMO_NA_TELA) {
        const descartado = lista.shift();
        if (descartado) pausar(descartado.id);
      }
      publicar(lista);
      agendar(novo.id);
    },
    [agendar, pausar, publicar],
  );

  useEffect(() => {
    // Reagenda após uma remontagem (StrictMode/Fast Refresh) e limpa ao desmontar.
    const mapa = temporizadores.current;
    atuais.current.forEach((toast) => agendar(toast.id));
    return () => {
      mapa.forEach((temporizador) => window.clearTimeout(temporizador));
      mapa.clear();
    };
  }, [agendar]);

  return (
    <ContextoToasts.Provider value={mostrar}>
      {children}
      <section className="toasts" aria-live="polite" aria-label="Avisos">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`toast toast-${toast.tom}`}
            onMouseEnter={() => pausar(toast.id)}
            onMouseLeave={() => agendar(toast.id)}
            onFocus={() => pausar(toast.id)}
            onBlur={() => agendar(toast.id)}
          >
            <Icone nome={ICONES[toast.tom]} className="toast-icone" />
            <div className="toast-texto">
              {toast.titulo && <strong>{toast.titulo}</strong>}
              <span>{toast.mensagem}</span>
            </div>
            <button
              type="button"
              className="botao-icone toast-fechar"
              onClick={() => remover(toast.id)}
              aria-label="Fechar aviso"
            >
              <Icone nome="fechar" tamanho={16} />
            </button>
          </div>
        ))}
      </section>
    </ContextoToasts.Provider>
  );
}

export function useToasts(): MostrarToast {
  const mostrar = useContext(ContextoToasts);
  if (!mostrar) throw new Error("useToasts precisa estar dentro de <ProvedorToasts>");
  return mostrar;
}
