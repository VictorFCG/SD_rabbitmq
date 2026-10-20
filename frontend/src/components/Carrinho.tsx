import { arredondar, avisoEstoque, formatarMoeda, plural } from "../formatar";
import type { ItemCarrinho } from "../hooks/useCarrinho";
import type { Produto } from "../tipos";
import { Carregando, Icone } from "./Icone";
import { SeletorQuantidade } from "./SeletorQuantidade";

interface Props {
  itens: ItemCarrinho[];
  porCodigo: Map<string, Produto>;
  enviando: boolean;
  aoAlterar: (codigo: string, quantidade: number) => void;
  aoRemover: (codigo: string) => void;
  aoLimpar: () => void;
  aoFinalizar: () => void;
}

export function Carrinho({ itens, porCodigo, enviando, aoAlterar, aoRemover, aoLimpar, aoFinalizar }: Props) {
  const linhas = itens.map((item) => {
    const produto = porCodigo.get(item.codigo);
    return { item, produto, subtotal: produto ? arredondar(produto.preco * item.quantidade) : null };
  });
  const total = arredondar(linhas.reduce((soma, linha) => soma + (linha.subtotal ?? 0), 0));
  const totalParcial = linhas.some((linha) => linha.subtotal === null);
  const unidades = itens.reduce((soma, item) => soma + item.quantidade, 0);

  return (
    <aside className="carrinho" id="carrinho" aria-labelledby="titulo-carrinho">
      <div className="carrinho-cabecalho">
        <h2 id="titulo-carrinho">
          <Icone nome="carrinho" />
          Carrinho
        </h2>
        <span className="contador">{plural(unidades, "item", "itens")}</span>
      </div>

      {itens.length === 0 ? (
        <p className="carrinho-vazio">Seu carrinho está vazio. Escolha produtos no catálogo.</p>
      ) : (
        <ul className="carrinho-itens">
          {linhas.map(({ item, produto, subtotal }) => {
            const nome = produto?.nome ?? item.codigo;
            return (
              <li key={item.codigo} className="carrinho-item">
                <div className="carrinho-item-topo">
                  <div>
                    <p className="carrinho-item-nome">{nome}</p>
                    <p className="carrinho-item-preco">
                      {produto ? `${formatarMoeda(produto.preco)} cada` : "Preço indisponível no momento"}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="botao-icone"
                    onClick={() => aoRemover(item.codigo)}
                    disabled={enviando}
                    aria-label={`Remover ${nome} do carrinho`}
                    title="Remover do carrinho"
                  >
                    <Icone nome="lixeira" tamanho={18} />
                  </button>
                </div>
                <div className="carrinho-item-base">
                  <SeletorQuantidade
                    valor={item.quantidade}
                    aoMudar={(quantidade) => aoAlterar(item.codigo, quantidade)}
                    rotulo={`Quantidade de ${nome} no carrinho`}
                    desabilitado={enviando}
                  />
                  <strong className="carrinho-item-subtotal">
                    {subtotal !== null ? formatarMoeda(subtotal) : "-"}
                  </strong>
                </div>
                {produto && item.quantidade > produto.estoque && (
                  <p className="aviso-estoque">{avisoEstoque(produto.estoque)}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <div className="carrinho-total">
        <span>Total estimado{totalParcial ? " (parcial)" : ""}</span>
        <strong>{formatarMoeda(total)}</strong>
      </div>

      <button
        type="button"
        className="botao botao-primario botao-largo"
        onClick={aoFinalizar}
        disabled={itens.length === 0 || enviando}
        aria-busy={enviando}
      >
        {enviando ? <Carregando rotulo="Enviando pedido..." /> : "Finalizar pedido"}
      </button>
      <p className="nota">
        Ao finalizar, uma nova aba é aberta para o pagamento no Mock de Pagamento assim que o MS Estoque
        confirmar o pedido.
      </p>
      {itens.length > 0 && (
        <button type="button" className="botao-link" onClick={aoLimpar} disabled={enviando}>
          Esvaziar carrinho
        </button>
      )}
    </aside>
  );
}
