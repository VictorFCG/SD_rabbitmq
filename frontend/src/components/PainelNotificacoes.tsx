import { useEffect, useRef } from "react";
import { formatarHora } from "../formatar";
import type { Notificacao } from "../hooks/useNotificacoes";
import { Icone, type NomeIcone } from "./Icone";
import type { Tom } from "../tipos";

interface Props {
  aberto: boolean;
  itens: Notificacao[];
  aoFechar: () => void;
  aoLimpar: () => void;
}

const ICONES: Record<Tom, NomeIcone> = {
  sucesso: "sucesso",
  erro: "erro",
  aviso: "alerta",
  info: "info",
  neutro: "info",
  promocao: "etiqueta",
};

export function PainelNotificacoes({ aberto, itens, aoFechar, aoLimpar }: Props) {
  const painel = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!aberto) return;
    painel.current?.focus();
    const aoTeclar = (evento: KeyboardEvent) => {
      if (evento.key === "Escape") aoFechar();
    };
    // Clique fora fecha o painel (exceto no sino, que já alterna, e nos avisos).
    const aoClicar = (evento: PointerEvent) => {
      const alvo = evento.target;
      if (!(alvo instanceof Element) || painel.current?.contains(alvo)) return;
      if (alvo.closest(".sino, .toasts")) return;
      aoFechar();
    };
    document.addEventListener("keydown", aoTeclar);
    document.addEventListener("pointerdown", aoClicar);
    return () => {
      document.removeEventListener("keydown", aoTeclar);
      document.removeEventListener("pointerdown", aoClicar);
    };
  }, [aberto, aoFechar]);

  return (
    <aside
      id="painel-notificacoes"
      ref={painel}
      className="painel-notificacoes"
      aria-labelledby="titulo-notificacoes"
      tabIndex={-1}
      hidden={!aberto}
    >
      <div className="painel-cabecalho">
        <h2 id="titulo-notificacoes">Notificações</h2>
        <div className="painel-acoes">
          <button type="button" className="botao botao-pequeno" onClick={aoLimpar} disabled={itens.length === 0}>
            Limpar
          </button>
          <button type="button" className="botao-icone" onClick={aoFechar} aria-label="Fechar notificações">
            <Icone nome="fechar" />
          </button>
        </div>
      </div>
      {itens.length === 0 ? (
        <p className="painel-vazio">
          Nenhuma notificação ainda. As mudanças de status dos seus pedidos e as novas promoções recebidas em tempo
          real (SSE) aparecem aqui.
        </p>
      ) : (
        <ol className="notificacoes">
          {itens.map((item) => (
            <li key={item.id} className={`notificacao notificacao-${item.tom}`}>
              <Icone nome={ICONES[item.tom]} tamanho={18} className="notificacao-icone" />
              <div>
                <p className="notificacao-titulo">
                  <strong>{item.titulo}</strong>
                  <time dateTime={item.momento}>{formatarHora(item.momento)}</time>
                </p>
                <p className="notificacao-mensagem">{item.mensagem}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
