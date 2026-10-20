import { useEffect, useState } from "react";
import { avisoEstoque, formatarMoeda } from "../formatar";
import type { Produto } from "../tipos";
import { Icone, iconeDaCategoria } from "./Icone";
import { SeletorQuantidade } from "./SeletorQuantidade";

interface Props {
  produto: Produto;
  noCarrinho: number;
  aoAdicionar: (produto: Produto, quantidade: number) => boolean;
}

export function CartaoProduto({ produto, noCarrinho, aoAdicionar }: Props) {
  const [quantidade, setQuantidade] = useState(1);
  const [adicionado, setAdicionado] = useState(false);

  useEffect(() => {
    if (!adicionado) return;
    const temporizador = window.setTimeout(() => setAdicionado(false), 1600);
    return () => window.clearTimeout(temporizador);
  }, [adicionado]);

  const esgotado = produto.estoque <= 0;
  const excede = !esgotado && noCarrinho + quantidade > produto.estoque;
  const idTitulo = `produto-${produto.codigo}`;

  const adicionar = () => {
    if (aoAdicionar(produto, quantidade)) {
      setAdicionado(true);
      setQuantidade(1);
    }
  };

  return (
    <article className={esgotado ? "produto produto-esgotado" : "produto"} aria-labelledby={idTitulo}>
      <div className={`produto-figura categoria-${produto.categoria || "outra"}`} aria-hidden="true">
        <Icone nome={iconeDaCategoria(produto.categoria)} tamanho={44} />
        <span className="produto-codigo">{produto.codigo}</span>
      </div>
      <div className="produto-corpo">
        <p className="produto-categoria">{produto.categoria_nome || "Sem categoria"}</p>
        <h3 id={idTitulo} className="produto-nome">
          {produto.nome}
        </h3>
        <p className="produto-preco">{formatarMoeda(produto.preco)}</p>
        <p className={`produto-estoque${esgotado ? " esgotado" : produto.estoque <= 3 ? " baixo" : ""}`}>
          {esgotado ? "Esgotado" : `${produto.estoque} em estoque`}
          {noCarrinho > 0 && <span className="produto-no-carrinho"> · {noCarrinho} no carrinho</span>}
        </p>
        <div className="produto-acoes">
          <SeletorQuantidade
            valor={quantidade}
            aoMudar={setQuantidade}
            rotulo={`Quantidade de ${produto.nome}`}
            desabilitado={esgotado}
          />
          <button
            type="button"
            className={adicionado ? "botao botao-sucesso" : "botao botao-primario"}
            onClick={adicionar}
            disabled={esgotado}
          >
            <Icone nome={adicionado ? "check" : "carrinho"} tamanho={18} />
            {adicionado ? "Adicionado" : "Adicionar ao carrinho"}
          </button>
        </div>
        <p className="aviso-estoque" aria-live="polite">
          {excede ? avisoEstoque(produto.estoque) : ""}
        </p>
      </div>
    </article>
  );
}
