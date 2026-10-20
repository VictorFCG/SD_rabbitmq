import type { Ref } from "react";
import type { EstadoConexao } from "../hooks/useEventos";
import type { Cliente } from "../tipos";
import { Icone } from "./Icone";

const CONEXAO: Record<EstadoConexao, { texto: string; classe: string; dica: string }> = {
  conectando: {
    texto: "conectando...",
    classe: "conexao-aviso",
    dica: "Abrindo a conexão SSE com o API Gateway.",
  },
  conectado: {
    texto: "conectado",
    classe: "conexao-ok",
    dica: "Conexão SSE ativa: o status dos pedidos e as promoções chegam em tempo real.",
  },
  reconectando: {
    texto: "reconectando...",
    classe: "conexao-aviso",
    dica: "A conexão SSE caiu; o navegador está tentando reconectar.",
  },
  desconectado: {
    texto: "desconectado",
    classe: "conexao-erro",
    dica: "Sem conexão com o API Gateway (MS Principal). Novas tentativas são feitas automaticamente.",
  },
};

interface Props {
  cliente: Cliente;
  conexao: EstadoConexao;
  naoLidas: number;
  painelAberto: boolean;
  refSino: Ref<HTMLButtonElement>;
  aoAlternarPainel: () => void;
  aoTrocarUsuario: () => void;
}

export function Cabecalho({ cliente, conexao, naoLidas, painelAberto, refSino, aoAlternarPainel, aoTrocarUsuario }: Props) {
  const estado = CONEXAO[conexao];
  const nomeExibido = cliente.nome || cliente.email;

  return (
    <header className="topo">
      <div className="topo-conteudo">
        <div className="marca">
          <span className="marca-logo" aria-hidden="true">
            <Icone nome="carrinho" tamanho={22} />
          </span>
          <span className="marca-nome">Loja SD</span>
        </div>

        <p className={`conexao ${estado.classe}`} title={estado.dica}>
          <span className="conexao-ponto" aria-hidden="true" />
          <span>
            <span className="conexao-prefixo">Tempo real: </span>
            {estado.texto}
          </span>
        </p>

        <div className="cliente" title={cliente.email}>
          <span className="cliente-avatar" aria-hidden="true">
            {nomeExibido.trim().charAt(0).toUpperCase() || "?"}
          </span>
          <span className="cliente-dados">
            <span className="cliente-nome">{nomeExibido}</span>
            <span className="cliente-email">{cliente.email}</span>
          </span>
        </div>

        <div className="topo-acoes">
          <button
            type="button"
            ref={refSino}
            className="botao-icone sino"
            onClick={aoAlternarPainel}
            aria-expanded={painelAberto}
            aria-controls="painel-notificacoes"
            aria-label={naoLidas > 0 ? `Notificações (${naoLidas} não lidas)` : "Notificações"}
            title="Notificações"
          >
            <Icone nome="sino" />
            {naoLidas > 0 && (
              <span className="sino-contador" aria-hidden="true">
                {naoLidas > 99 ? "99+" : naoLidas}
              </span>
            )}
          </button>

          <button type="button" className="botao botao-secundario botao-pequeno" onClick={aoTrocarUsuario}>
            <Icone nome="sair" tamanho={16} />
            Trocar usuário
          </button>
        </div>
      </div>
    </header>
  );
}
