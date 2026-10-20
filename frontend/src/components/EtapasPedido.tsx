import { etapasDoPedido, type EstadoEtapa } from "../status";
import type { Pedido } from "../tipos";
import { Icone } from "./Icone";

const DESCRICAO: Record<EstadoEtapa, string> = {
  concluida: "concluída",
  atual: "etapa atual",
  falha: "falhou",
  pendente: "pendente",
};

export function EtapasPedido({ pedido }: { pedido: Pedido }) {
  const etapas = etapasDoPedido(pedido);
  // Em telas estreitas os rótulos não cabem sob cada marcador: a legenda mostra só a
  // etapa em destaque (falha, atual ou a última concluída).
  let indice = etapas.findIndex((etapa) => etapa.estado === "falha" || etapa.estado === "atual");
  if (indice < 0) indice = etapas.map((etapa) => etapa.estado).lastIndexOf("concluida");
  const destaque = indice >= 0 ? etapas[indice] : null;

  return (
    <div className="etapas-bloco">
      <ol className="etapas" aria-label="Andamento do pedido">
        {etapas.map((etapa) => (
          <li
            key={etapa.chave}
            className={`etapa etapa-${etapa.estado}`}
            aria-current={etapa.estado === "atual" ? "step" : undefined}
          >
            <span className="etapa-marcador" aria-hidden="true">
              {etapa.estado === "concluida" && <Icone nome="check" tamanho={14} />}
              {etapa.estado === "falha" && <Icone nome="fechar" tamanho={14} />}
            </span>
            <span className="etapa-rotulo">
              {etapa.rotulo}
              <span className="sr-only"> ({DESCRICAO[etapa.estado]})</span>
            </span>
          </li>
        ))}
      </ol>
      {destaque && (
        <p className={`etapas-legenda etapas-legenda-${destaque.estado}`} aria-hidden="true">
          Etapa {indice + 1} de {etapas.length}: {destaque.rotulo}
        </p>
      )}
    </div>
  );
}
