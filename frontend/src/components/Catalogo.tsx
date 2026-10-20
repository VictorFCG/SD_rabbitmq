import { useMemo, useState } from "react";
import { formatarHora, juntarFrases } from "../formatar";
import type { Catalogo as EstadoCatalogo } from "../hooks/useCatalogo";
import type { Produto } from "../tipos";
import { CartaoProduto } from "./CartaoProduto";
import { Icone } from "./Icone";

interface Props {
  catalogo: EstadoCatalogo;
  noCarrinho: Map<string, number>;
  aoAdicionar: (produto: Produto, quantidade: number) => boolean;
}

const TODAS = "todas";

export function Catalogo({ catalogo, noCarrinho, aoAdicionar }: Props) {
  const { produtos, carregando, erro, atualizadoEm, recarregar } = catalogo;
  const [filtro, setFiltro] = useState(TODAS);

  const categorias = useMemo(() => {
    const mapa = new Map<string, string>();
    (produtos ?? []).forEach((produto) => {
      if (produto.categoria && !mapa.has(produto.categoria)) mapa.set(produto.categoria, produto.categoria_nome);
    });
    return [...mapa.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [produtos]);

  const filtroAtivo = categorias.some(([codigo]) => codigo === filtro) ? filtro : TODAS;
  const visiveis = (produtos ?? []).filter((produto) => filtroAtivo === TODAS || produto.categoria === filtroAtivo);

  return (
    <section className="secao" aria-labelledby="titulo-catalogo">
      <div className="secao-cabecalho">
        <div>
          <h2 id="titulo-catalogo">Catálogo</h2>
          <p className="secao-descricao">
            Produtos e estoque vêm do MS Estoque (REST), consultado pelo API Gateway.
          </p>
        </div>
        <button
          type="button"
          className="botao botao-secundario"
          onClick={() => void recarregar()}
          disabled={carregando}
          aria-busy={carregando}
        >
          <Icone nome="atualizar" tamanho={18} className={carregando ? "girando" : undefined} />
          Atualizar
        </button>
      </div>

      {categorias.length > 1 && (
        <div className="filtros" role="group" aria-label="Filtrar por categoria">
          {[[TODAS, "Todas"] as const, ...categorias].map(([codigo, nome]) => (
            <button
              key={codigo}
              type="button"
              className="filtro"
              aria-pressed={filtroAtivo === codigo}
              onClick={() => setFiltro(codigo)}
            >
              {nome}
            </button>
          ))}
        </div>
      )}

      {erro && produtos && (
        <div className="alerta alerta-aviso" role="alert">
          <Icone nome="alerta" />
          <p>
            Não foi possível atualizar o catálogo. {juntarFrases(erro, "Exibindo a última versão carregada")}
          </p>
          <button type="button" className="botao botao-pequeno" onClick={() => void recarregar()} disabled={carregando}>
            Tentar novamente
          </button>
        </div>
      )}

      {erro && !produtos && (
        <div className="estado estado-erro" role="alert">
          <Icone nome="alerta" tamanho={36} />
          <p className="estado-titulo">Não foi possível carregar o catálogo.</p>
          <p>{juntarFrases(erro)}</p>
          <button type="button" className="botao botao-primario" onClick={() => void recarregar()} disabled={carregando}>
            <Icone nome="atualizar" tamanho={18} className={carregando ? "girando" : undefined} />
            Tentar novamente
          </button>
        </div>
      )}

      {!produtos && !erro && (
        <ul className="grade-produtos" aria-hidden="true">
          {Array.from({ length: 6 }, (_, indice) => (
            <li key={indice} className="produto produto-esqueleto" />
          ))}
        </ul>
      )}

      {produtos && produtos.length === 0 && (
        <p className="estado">Nenhum produto cadastrado no MS Estoque.</p>
      )}

      {visiveis.length > 0 && (
        <ul className="grade-produtos" aria-busy={carregando}>
          {visiveis.map((produto) => (
            <li key={produto.codigo}>
              <CartaoProduto
                produto={produto}
                noCarrinho={noCarrinho.get(produto.codigo) ?? 0}
                aoAdicionar={aoAdicionar}
              />
            </li>
          ))}
        </ul>
      )}

      {atualizadoEm !== null && (
        <p className="secao-rodape">Estoque consultado às {formatarHora(new Date(atualizadoEm).toISOString())}.</p>
      )}
    </section>
  );
}
