// Tela principal depois da identificação: junta catálogo, carrinho, pedidos,
// promoções, a conexão SSE e os avisos. É remontada a cada troca de usuário
// (key = e-mail), o que fecha a conexão SSE e zera o estado do cliente anterior.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_URL, ErroApi, cancelarPedido, criarPedido, mensagemDeErro } from "../api";
import { gravarAba, lerAba } from "../armazenamento";
import { arredondar, formatarMoeda, plural } from "../formatar";
import { useAbasPagamento } from "../hooks/useAbasPagamento";
import { ITENS_MAXIMOS, QUANTIDADE_MAXIMA, useCarrinho } from "../hooks/useCarrinho";
import { useCatalogo } from "../hooks/useCatalogo";
import { useEventos } from "../hooks/useEventos";
import { useNotificacoes } from "../hooks/useNotificacoes";
import { usePedidos } from "../hooks/usePedidos";
import { usePromocoes } from "../hooks/usePromocoes";
import { ehAtivo, rotuloStatus, tomDaNotificacao } from "../status";
import { useToasts } from "../toasts";
import type { Cliente, EventoPedido, Pedido, Produto, Promocao } from "../tipos";
import { Abas, IDS_ABAS, type IdAba } from "./Abas";
import { Cabecalho } from "./Cabecalho";
import { Carrinho } from "./Carrinho";
import { Catalogo } from "./Catalogo";
import { MeusPedidos } from "./MeusPedidos";
import { PainelNotificacoes } from "./PainelNotificacoes";
import { Promocoes } from "./Promocoes";

// Eventos depois dos quais o estoque exibido pode ter mudado (reserva ou devolução).
const TIPOS_QUE_MUDAM_ESTOQUE = new Set([
  "pedido.estoque_ok",
  "estoque.indisponivel",
  "pagamento.recusado",
  "pedido.cancelado",
]);
const STATUS_QUE_MUDAM_ESTOQUE = new Set(["ESTOQUE_CONFIRMADO", "ESTOQUE_INDISPONIVEL", "PAGAMENTO_RECUSADO", "CANCELADO"]);

interface Props {
  cliente: Cliente;
  aoTrocarUsuario: () => void;
}

function textoPromocao(promocao: Promocao): string {
  return (
    `${promocao.produto} com ${Math.round(promocao.desconto)}% de desconto: de ` +
    `${formatarMoeda(promocao.preco_original)} por ${formatarMoeda(promocao.preco_promocional)}` +
    (promocao.categoria_nome ? ` (${promocao.categoria_nome}).` : ".")
  );
}

export function Loja({ cliente, aoTrocarUsuario }: Props) {
  const mostrarToast = useToasts();
  const [aba, setAba] = useState<IdAba>(() => lerAba(IDS_ABAS, "catalogo"));
  const [painelAberto, setPainelAberto] = useState(false);
  const [enviandoPedido, setEnviandoPedido] = useState(false);
  const [cancelando, setCancelando] = useState<ReadonlySet<string>>(new Set());
  const refSino = useRef<HTMLButtonElement>(null);
  const jaConectou = useRef(false);

  const catalogo = useCatalogo();
  const carrinho = useCarrinho(cliente.email);
  const notificacoes = useNotificacoes();
  const promocoes = usePromocoes();
  const abasPagamento = useAbasPagamento(mostrarToast);
  const pedidos = usePedidos(cliente.email, abasPagamento.sincronizar);
  const { marcarComoLidas } = notificacoes;

  const aoEventoPedido = (evento: EventoPedido) => {
    const { pedido } = evento;
    pedidos.aplicar([pedido]);
    const titulo = pedido.status_label || rotuloStatus(pedido.status);
    const mensagem = evento.mensagem || `${titulo}: pedido ${pedido.id_pedido}.`;
    const tom = tomDaNotificacao(pedido.status);
    mostrarToast(tom, mensagem);
    notificacoes.adicionar({ tom, titulo, mensagem, momento: evento.timestamp });
    if (TIPOS_QUE_MUDAM_ESTOQUE.has(evento.tipo) || STATUS_QUE_MUDAM_ESTOQUE.has(pedido.status)) {
      catalogo.agendarRecarga();
    }
  };

  const aoPromocao = (promocao: Promocao) => {
    if (!promocoes.adicionar(promocao)) return;
    const mensagem = textoPromocao(promocao);
    mostrarToast("promocao", mensagem, "Nova promoção");
    notificacoes.adicionar({ tom: "promocao", titulo: "Nova promoção", mensagem, momento: promocao.criado_em });
  };

  const aoConectar = () => {
    // A cada (re)conexão, ressincroniza os pedidos: eventos podem ter sido perdidos.
    // Numa reconexão (ou se a carga inicial falhou) atualiza também estoque e promoções.
    void pedidos.recarregar();
    if (jaConectou.current || catalogo.erro) void catalogo.recarregar();
    if (jaConectou.current || promocoes.erro) void promocoes.recarregar();
    jaConectou.current = true;
  };

  const conexao = useEventos(cliente.email, {
    aoConectar,
    aoPedido: aoEventoPedido,
    aoPromocao,
  });

  const selecionarAba = useCallback((nova: IdAba) => {
    setAba(nova);
    gravarAba(nova);
  }, []);

  useEffect(() => {
    // Ao trocar de aba com a página rolada, volta ao início do conteúdo.
    const conteudo = document.getElementById("conteudo");
    if (conteudo && conteudo.getBoundingClientRect().top < 0) conteudo.scrollIntoView({ block: "start" });
  }, [aba]);

  useEffect(() => {
    document.title = notificacoes.naoLidas > 0 ? `(${notificacoes.naoLidas}) Loja SD` : "Loja SD";
    return () => {
      document.title = "Loja SD";
    };
  }, [notificacoes.naoLidas]);

  const fecharPainel = useCallback(() => {
    setPainelAberto(false);
    marcarComoLidas();
    // Sem rolar até o sino: um clique fora fecha o painel e não pode ser perdido.
    refSino.current?.focus({ preventScroll: true });
  }, [marcarComoLidas]);

  const alternarPainel = () => {
    if (painelAberto) {
      fecharPainel();
    } else {
      setPainelAberto(true);
      marcarComoLidas();
    }
  };

  const quantidadesNoCarrinho = useMemo(
    () => new Map(carrinho.itens.map((item) => [item.codigo, item.quantidade] as const)),
    [carrinho.itens],
  );

  const adicionarAoCarrinho = (produto: Produto, quantidade: number): boolean => {
    const resultado = carrinho.adicionar(produto.codigo, quantidade);
    if (resultado === "limite_itens") {
      mostrarToast("aviso", `Um pedido aceita no máximo ${ITENS_MAXIMOS} produtos diferentes.`);
      return false;
    }
    if (resultado === "ajustado") {
      mostrarToast("aviso", `A quantidade máxima por produto é ${QUANTIDADE_MAXIMA}: ${produto.nome} foi ajustado.`);
    }
    return true;
  };

  const finalizarPedido = async () => {
    if (enviandoPedido || carrinho.itens.length === 0) return;
    // A aba precisa ser aberta aqui, ainda dentro do clique (antes de qualquer await),
    // para não ser barrada pelo bloqueador de pop-ups.
    const janela = abasPagamento.abrir();
    const itens = carrinho.itens.map(({ codigo, quantidade }) => ({ codigo, quantidade }));
    setEnviandoPedido(true);
    try {
      const pedido = await criarPedido({ email: cliente.email, nome: cliente.nome || null, itens });
      abasPagamento.registrar(pedido.id_pedido, janela);
      pedidos.aplicar([pedido]);
      carrinho.removerEnviados(itens.map((item) => item.codigo));
      selecionarAba("pedidos");
      if (conexao !== "conectado") {
        mostrarToast("info", `Pedido ${pedido.id_pedido} criado. Aguardando confirmação de estoque.`);
      }
      if (!janela) {
        mostrarToast(
          "aviso",
          "O navegador bloqueou a nova aba de pagamento. Quando o pagamento estiver pronto, use o botão “Pagar agora” no card do pedido.",
          "Aba de pagamento bloqueada",
        );
      }
    } catch (falha) {
      abasPagamento.fechar(janela);
      mostrarToast("erro", mensagemDeErro(falha), "Não foi possível criar o pedido");
    } finally {
      setEnviandoPedido(false);
    }
  };

  const cancelar = async (pedido: Pedido) => {
    const id = pedido.id_pedido;
    if (cancelando.has(id)) return;
    if (!window.confirm(`Cancelar o pedido ${id}? Os itens reservados voltam para o estoque.`)) return;
    setCancelando((atuais) => new Set(atuais).add(id));
    try {
      pedidos.aplicar([await cancelarPedido(id)]);
      mostrarToast("info", `Pedido ${id} cancelado.`);
      catalogo.agendarRecarga();
    } catch (falha) {
      mostrarToast("erro", mensagemDeErro(falha), `Não foi possível cancelar o pedido ${id}`);
      if (falha instanceof ErroApi && (falha.status === 404 || falha.status === 409)) void pedidos.recarregar();
    } finally {
      setCancelando((atuais) => {
        const novos = new Set(atuais);
        novos.delete(id);
        return novos;
      });
    }
  };

  const pedidosAtivos = pedidos.pedidos.filter((pedido) => ehAtivo(pedido.status)).length;
  const unidadesNoCarrinho = carrinho.itens.reduce((soma, item) => soma + item.quantidade, 0);
  const totalCarrinho = arredondar(
    carrinho.itens.reduce((soma, item) => soma + (catalogo.porCodigo.get(item.codigo)?.preco ?? 0) * item.quantidade, 0),
  );

  return (
    <div className="aplicacao">
      <a className="pular-conteudo" href="#conteudo">
        Pular para o conteúdo
      </a>
      <Cabecalho
        cliente={cliente}
        conexao={conexao}
        naoLidas={painelAberto ? 0 : notificacoes.naoLidas}
        painelAberto={painelAberto}
        refSino={refSino}
        aoAlternarPainel={alternarPainel}
        aoTrocarUsuario={aoTrocarUsuario}
      />
      <Abas
        ativa={aba}
        aoSelecionar={selecionarAba}
        contadores={{
          pedidos: { valor: pedidosAtivos, descricao: plural(pedidosAtivos, "pedido ativo", "pedidos ativos") },
        }}
      />

      <main id="conteudo" className="conteudo" tabIndex={-1}>
        <div id="painel-catalogo" role="tabpanel" aria-labelledby="aba-catalogo" hidden={aba !== "catalogo"}>
          <div className="layout-catalogo">
            <Catalogo catalogo={catalogo} noCarrinho={quantidadesNoCarrinho} aoAdicionar={adicionarAoCarrinho} />
            <Carrinho
              itens={carrinho.itens}
              porCodigo={catalogo.porCodigo}
              enviando={enviandoPedido}
              aoAlterar={carrinho.alterar}
              aoRemover={carrinho.remover}
              aoLimpar={carrinho.limpar}
              aoFinalizar={() => void finalizarPedido()}
            />
          </div>
          {carrinho.itens.length > 0 && (
            <div className="barra-carrinho">
              <span>
                <strong>{plural(unidadesNoCarrinho, "item", "itens")}</strong> · {formatarMoeda(totalCarrinho)}
              </span>
              <a className="botao botao-primario botao-pequeno" href="#carrinho">
                Ver carrinho
              </a>
            </div>
          )}
        </div>

        <div id="painel-pedidos" role="tabpanel" aria-labelledby="aba-pedidos" hidden={aba !== "pedidos"}>
          <MeusPedidos
            estado={pedidos}
            porCodigo={catalogo.porCodigo}
            cancelando={cancelando}
            aoCancelar={(pedido) => void cancelar(pedido)}
            aoIrAoCatalogo={() => selecionarAba("catalogo")}
          />
        </div>

        <div id="painel-promocoes" role="tabpanel" aria-labelledby="aba-promocoes" hidden={aba !== "promocoes"}>
          <Promocoes
            cliente={cliente}
            promocoes={promocoes}
            mostrarToast={mostrarToast}
            conectado={conexao === "conectado"}
          />
        </div>
      </main>

      <PainelNotificacoes
        aberto={painelAberto}
        itens={notificacoes.itens}
        aoFechar={fecharPainel}
        aoLimpar={notificacoes.limpar}
      />

      <footer className="rodape">
        <p>
          Loja SD · Trab2 de Sistemas Distribuídos (microsserviços, RabbitMQ, REST e SSE) · Frontend React +
          TypeScript
        </p>
        <p>
          API Gateway: <span className="mono">{API_URL}</span>
        </p>
      </footer>
    </div>
  );
}
