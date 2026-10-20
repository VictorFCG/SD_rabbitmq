import { juntarFrases } from "../formatar";
import type { EstadoPedidos } from "../hooks/usePedidos";
import type { Pedido, Produto } from "../tipos";
import { CartaoPedido } from "./CartaoPedido";
import { Icone } from "./Icone";

interface Props {
  estado: EstadoPedidos;
  porCodigo: Map<string, Produto>;
  cancelando: ReadonlySet<string>;
  aoCancelar: (pedido: Pedido) => void;
  aoIrAoCatalogo: () => void;
}

export function MeusPedidos({ estado, porCodigo, cancelando, aoCancelar, aoIrAoCatalogo }: Props) {
  const { pedidos, carregando, carregou, erro, recarregar } = estado;

  return (
    <section className="secao" aria-labelledby="titulo-pedidos">
      <div className="secao-cabecalho">
        <div>
          <h2 id="titulo-pedidos">Meus pedidos</h2>
          <p className="secao-descricao">
            O status muda em tempo real (SSE) conforme MS Estoque, MS Pagamento e MS Entrega processam os eventos
            no RabbitMQ.
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

      {erro && (
        <div className="alerta alerta-erro" role="alert">
          <Icone nome="alerta" />
          <p>Não foi possível carregar seus pedidos. {juntarFrases(erro)}</p>
          <button type="button" className="botao botao-pequeno" onClick={() => void recarregar()} disabled={carregando}>
            Tentar novamente
          </button>
        </div>
      )}

      {!carregou && pedidos.length === 0 && (
        <ul className="lista-pedidos" aria-hidden="true">
          <li className="pedido pedido-esqueleto" />
          <li className="pedido pedido-esqueleto" />
        </ul>
      )}

      {carregou && pedidos.length === 0 && !erro && (
        <div className="estado">
          <Icone nome="caixa" tamanho={40} />
          <p className="estado-titulo">Você ainda não fez pedidos.</p>
          <p>Adicione produtos ao carrinho e finalize o pedido para acompanhá-lo aqui.</p>
          <button type="button" className="botao botao-primario" onClick={aoIrAoCatalogo}>
            Ir ao catálogo
          </button>
        </div>
      )}

      {pedidos.length > 0 && (
        <ol className="lista-pedidos">
          {pedidos.map((pedido) => (
            <li key={pedido.id_pedido}>
              <CartaoPedido
                pedido={pedido}
                porCodigo={porCodigo}
                cancelando={cancelando.has(pedido.id_pedido)}
                aoCancelar={aoCancelar}
              />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
