import { useRef, type KeyboardEvent } from "react";

export const IDS_ABAS = ["catalogo", "pedidos", "promocoes"] as const;
export type IdAba = (typeof IDS_ABAS)[number];

const ROTULOS: Record<IdAba, string> = {
  catalogo: "Catálogo",
  pedidos: "Meus pedidos",
  promocoes: "Promoções",
};

interface Props {
  ativa: IdAba;
  aoSelecionar: (aba: IdAba) => void;
  contadores: Partial<Record<IdAba, { valor: number; descricao: string }>>;
}

export function Abas({ ativa, aoSelecionar, contadores }: Props) {
  const botoes = useRef(new Map<IdAba, HTMLButtonElement>());

  const aoTeclar = (evento: KeyboardEvent<HTMLButtonElement>, indice: number) => {
    let destino: number | null = null;
    if (evento.key === "ArrowRight") destino = (indice + 1) % IDS_ABAS.length;
    else if (evento.key === "ArrowLeft") destino = (indice - 1 + IDS_ABAS.length) % IDS_ABAS.length;
    else if (evento.key === "Home") destino = 0;
    else if (evento.key === "End") destino = IDS_ABAS.length - 1;
    if (destino === null) return;
    evento.preventDefault();
    const aba = IDS_ABAS[destino];
    aoSelecionar(aba);
    botoes.current.get(aba)?.focus();
  };

  return (
    <nav className="abas-barra" aria-label="Seções da loja">
      <div className="abas" role="tablist">
        {IDS_ABAS.map((aba, indice) => {
          const contador = contadores[aba];
          return (
            <button
              key={aba}
              ref={(elemento) => {
                if (elemento) botoes.current.set(aba, elemento);
                else botoes.current.delete(aba);
              }}
              type="button"
              role="tab"
              id={`aba-${aba}`}
              className="aba"
              aria-selected={ativa === aba}
              aria-controls={`painel-${aba}`}
              tabIndex={ativa === aba ? 0 : -1}
              onClick={() => aoSelecionar(aba)}
              onKeyDown={(evento) => aoTeclar(evento, indice)}
            >
              {ROTULOS[aba]}
              {contador && contador.valor > 0 && (
                <span className="aba-contador">
                  <span aria-hidden="true">{contador.valor}</span>
                  <span className="sr-only">({contador.descricao})</span>
                </span>
              )}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
