import { useState } from "react";
import { QUANTIDADE_MAXIMA, limitarQuantidade } from "../hooks/useCarrinho";
import { Icone } from "./Icone";

interface Props {
  valor: number;
  aoMudar: (valor: number) => void;
  rotulo: string;
  desabilitado?: boolean;
}

export function SeletorQuantidade({ valor, aoMudar, rotulo, desabilitado = false }: Props) {
  const [texto, setTexto] = useState(String(valor));
  const [valorExibido, setValorExibido] = useState(valor);
  if (valor !== valorExibido) {
    setValorExibido(valor);
    setTexto(String(valor));
  }

  const confirmar = () => {
    const numero = Number.parseInt(texto, 10);
    const final = Number.isNaN(numero) ? valor : limitarQuantidade(numero);
    setTexto(String(final));
    if (final !== valor) aoMudar(final);
  };

  const digitar = (bruto: string) => {
    setTexto(bruto);
    const numero = Number(bruto);
    if (bruto.trim() && Number.isInteger(numero) && numero >= 1 && numero <= QUANTIDADE_MAXIMA && numero !== valor) {
      aoMudar(numero);
    }
  };

  return (
    <div className="quantidade" role="group" aria-label={rotulo}>
      <button
        type="button"
        className="quantidade-botao"
        onClick={() => aoMudar(limitarQuantidade(valor - 1))}
        disabled={desabilitado || valor <= 1}
        aria-label="Diminuir quantidade"
      >
        <Icone nome="menos" tamanho={16} />
      </button>
      <input
        className="quantidade-campo"
        type="number"
        inputMode="numeric"
        min={1}
        max={QUANTIDADE_MAXIMA}
        step={1}
        value={texto}
        disabled={desabilitado}
        aria-label="Quantidade"
        onChange={(evento) => digitar(evento.target.value)}
        onBlur={confirmar}
        onKeyDown={(evento) => {
          if (evento.key === "Enter") confirmar();
        }}
      />
      <button
        type="button"
        className="quantidade-botao"
        onClick={() => aoMudar(limitarQuantidade(valor + 1))}
        disabled={desabilitado || valor >= QUANTIDADE_MAXIMA}
        aria-label="Aumentar quantidade"
      >
        <Icone nome="mais" tamanho={16} />
      </button>
    </div>
  );
}
