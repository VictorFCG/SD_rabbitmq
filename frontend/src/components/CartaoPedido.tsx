import { useState } from "react";
import { arredondar, ehUrlHttp, formatarDataHora, formatarHora, formatarMoeda } from "../formatar";
import { ehCancelavel, ehFalha, tomDoStatus } from "../status";
import type { Pedido, Produto } from "../tipos";
import { EtapasPedido } from "./EtapasPedido";
import { Carregando, Icone } from "./Icone";

interface Props {
  pedido: Pedido;
  porCodigo: Map<string, Produto>;
  cancelando: boolean;
  aoCancelar: (pedido: Pedido) => void;
}

function precoDoItem(pedido: Pedido, indice: number, porCodigo: Map<string, Produto>): number | null {
  const item = pedido.itens[indice];
  return item.preco_unitario ?? porCodigo.get(item.codigo)?.preco ?? null;
}

/** valor_total só existe depois do Estoque Confirmado; antes disso, estima pelo catálogo. */
function totalDoPedido(pedido: Pedido, porCodigo: Map<string, Produto>): number | null {
  if (pedido.valor_total !== null) return pedido.valor_total;
  let total = 0;
  for (let indice = 0; indice < pedido.itens.length; indice += 1) {
    const preco = precoDoItem(pedido, indice, porCodigo);
    if (preco === null) return null;
    total += preco * pedido.itens[indice].quantidade;
  }
  return arredondar(total);
}

export function CartaoPedido({ pedido, porCodigo, cancelando, aoCancelar }: Props) {
  // Cada nova versão do pedido (re)inicia a animação de destaque (nova key); o elemento
  // sai ao fim da animação, para ela não se repetir quando a aba volta a ser exibida.
  const versao = `${pedido.status}|${pedido.atualizado_em}`;
  const [versaoExibida, setVersaoExibida] = useState(versao);
  const [destaque, setDestaque] = useState(0);
  if (versao !== versaoExibida) {
    setVersaoExibida(versao);
    setDestaque((atual) => atual + 1);
  }

  const tom = tomDoStatus(pedido.status);
  const total = totalDoPedido(pedido, porCodigo);
  const idTitulo = `pedido-${pedido.id_pedido}`;
  const podePagar = pedido.status === "AGUARDANDO_PAGAMENTO" && ehUrlHttp(pedido.url_pagamento);

  return (
    <article className={`pedido pedido-${tom}`} aria-labelledby={idTitulo}>
      {destaque > 0 && (
        <span key={destaque} className="destaque" aria-hidden="true" onAnimationEnd={() => setDestaque(0)} />
      )}
      <header className="pedido-cabecalho">
        <div>
          <h3 id={idTitulo} className="pedido-id">
            Pedido <span className="mono">{pedido.id_pedido}</span>
          </h3>
          <p className="pedido-data">
            <time dateTime={pedido.criado_em}>Criado em {formatarDataHora(pedido.criado_em)}</time>
          </p>
        </div>
        <span className={`selo selo-${tom}`}>{pedido.status_label}</span>
      </header>

      <EtapasPedido pedido={pedido} />

      <ul className="pedido-itens">
        {pedido.itens.map((item, indice) => {
          const nome = item.nome ?? porCodigo.get(item.codigo)?.nome ?? item.codigo;
          const preco = precoDoItem(pedido, indice, porCodigo);
          const subtotal = item.subtotal ?? (preco !== null ? arredondar(preco * item.quantidade) : null);
          return (
            <li key={`${item.codigo}-${indice}`}>
              <span>
                {nome} <span className="multiplicador">× {item.quantidade}</span>
              </span>
              {subtotal !== null && <span className="valor">{formatarMoeda(subtotal)}</span>}
            </li>
          );
        })}
      </ul>

      <p className="pedido-total">
        <span>{pedido.valor_total !== null ? "Total" : "Total estimado"}</span>
        <strong>{total !== null ? formatarMoeda(total) : "-"}</strong>
      </p>

      {pedido.status === "AGUARDANDO_PAGAMENTO" && (
        <p className="pedido-nota pedido-nota-aviso">
          <Icone nome="cartao" tamanho={18} />
          <span>
            Aguardando o pagamento no Mock de Pagamento
            {pedido.id_cobranca ? <> (cobrança <span className="mono">{pedido.id_cobranca}</span>)</> : null}.
          </span>
        </p>
      )}

      {pedido.status === "ENVIADO" && (
        <dl className="pedido-envio">
          <div>
            <dt>
              <Icone nome="caminhao" tamanho={16} /> Nota fiscal
            </dt>
            <dd className="mono">{pedido.nota_fiscal ?? "-"}</dd>
          </div>
          <div>
            <dt>Código de rastreio</dt>
            <dd className="mono">{pedido.codigo_rastreio ?? "-"}</dd>
          </div>
        </dl>
      )}

      {ehFalha(pedido.status) && pedido.motivo && (
        <p className="pedido-nota pedido-nota-erro">
          <Icone nome="alerta" tamanho={18} />
          <span>
            <strong>Motivo:</strong> {pedido.motivo}
          </span>
        </p>
      )}

      <footer className="pedido-rodape">
        {pedido.historico.length > 0 && (
          <details className="pedido-historico">
            <summary>Histórico ({pedido.historico.length})</summary>
            <ol>
              {pedido.historico.map((entrada, indice) => (
                <li key={`${entrada.status}-${entrada.timestamp}-${indice}`}>
                  <time dateTime={entrada.timestamp}>{formatarHora(entrada.timestamp)}</time>
                  <div>
                    <strong>{entrada.status_label}</strong>
                    {entrada.mensagem && <p>{entrada.mensagem}</p>}
                  </div>
                </li>
              ))}
            </ol>
          </details>
        )}
        <div className="pedido-acoes">
          {podePagar && (
            <a className="botao botao-primario" href={pedido.url_pagamento ?? undefined} target="_blank" rel="noopener noreferrer">
              <Icone nome="externo" tamanho={18} />
              Pagar agora
            </a>
          )}
          {ehCancelavel(pedido.status) && (
            <button
              type="button"
              className="botao botao-perigo"
              onClick={() => aoCancelar(pedido)}
              disabled={cancelando}
              aria-busy={cancelando}
            >
              {cancelando ? <Carregando rotulo="Cancelando..." /> : "Cancelar pedido"}
            </button>
          )}
        </div>
      </footer>
    </article>
  );
}
