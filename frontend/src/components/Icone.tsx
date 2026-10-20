import type { ReactNode } from "react";

// Ícones de traço simples desenhados em SVG inline (sem dependências externas).
const DESENHOS = {
  carrinho: (
    <>
      <circle cx="9" cy="20" r="1.4" />
      <circle cx="18" cy="20" r="1.4" />
      <path d="M2.5 3.5h2.6l2.3 11a1.6 1.6 0 0 0 1.6 1.3h8.7a1.6 1.6 0 0 0 1.5-1.2L21 7.5H6.1" />
    </>
  ),
  sino: (
    <>
      <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.9 1.9 0 0 0 3.4 0" />
    </>
  ),
  atualizar: (
    <>
      <path d="M20.5 12a8.5 8.5 0 1 1-2.5-6" />
      <path d="M20.5 3.5v5h-5" />
    </>
  ),
  sair: (
    <>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="m16 17 5-5-5-5" />
      <path d="M21 12H9" />
    </>
  ),
  fechar: (
    <>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </>
  ),
  check: <path d="M20 6 9 17l-5-5" />,
  alerta: (
    <>
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9.5" />
      <path d="M12 16v-4.5" />
      <path d="M12 8h.01" />
    </>
  ),
  erro: (
    <>
      <circle cx="12" cy="12" r="9.5" />
      <path d="m15 9-6 6" />
      <path d="m9 9 6 6" />
    </>
  ),
  sucesso: (
    <>
      <circle cx="12" cy="12" r="9.5" />
      <path d="m8 12.5 2.7 2.7L16.5 9.5" />
    </>
  ),
  etiqueta: (
    <>
      <path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z" />
      <circle cx="7.5" cy="7.5" r="1.4" />
    </>
  ),
  caminhao: (
    <>
      <path d="M14 17V5H2v12h2" />
      <path d="M14 8h4l4 4v5h-2" />
      <path d="M9 17h6" />
      <circle cx="6.5" cy="17.5" r="2.2" />
      <circle cx="17.5" cy="17.5" r="2.2" />
    </>
  ),
  email: (
    <>
      <rect x="2.5" y="4.5" width="19" height="15" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </>
  ),
  externo: (
    <>
      <path d="M15 3h6v6" />
      <path d="M10 14 21 3" />
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    </>
  ),
  lixeira: (
    <>
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M18.5 6l-.9 13.1a2 2 0 0 1-2 1.9H8.4a2 2 0 0 1-2-1.9L5.5 6" />
    </>
  ),
  mais: (
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>
  ),
  menos: <path d="M5 12h14" />,
  monitor: (
    <>
      <rect x="2.5" y="3.5" width="19" height="13" rx="2" />
      <path d="M8 20.5h8" />
      <path d="M12 16.5v4" />
    </>
  ),
  teclado: (
    <>
      <rect x="2" y="6" width="20" height="12" rx="2" />
      <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8" />
    </>
  ),
  cabo: (
    <>
      <path d="M9 2.5v5" />
      <path d="M15 2.5v5" />
      <path d="M6.5 7.5h11V11a5.5 5.5 0 0 1-11 0z" />
      <path d="M12 16.5v5" />
    </>
  ),
  caixa: (
    <>
      <path d="M21 8 12 3 3 8v8l9 5 9-5z" />
      <path d="m3 8 9 5 9-5" />
      <path d="M12 13v8" />
    </>
  ),
  usuario: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </>
  ),
  cartao: (
    <>
      <rect x="2.5" y="5" width="19" height="14" rx="2" />
      <path d="M2.5 10h19" />
      <path d="M6.5 15h4" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type NomeIcone = keyof typeof DESENHOS;

interface Props {
  nome: NomeIcone;
  tamanho?: number;
  className?: string;
}

export function Icone({ nome, tamanho = 20, className }: Props) {
  return (
    <svg
      className={className}
      width={tamanho}
      height={tamanho}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {DESENHOS[nome]}
    </svg>
  );
}

export function iconeDaCategoria(categoria: string): NomeIcone {
  if (categoria === "A") return "monitor";
  if (categoria === "B") return "teclado";
  if (categoria === "C") return "cabo";
  return "caixa";
}

export function Carregando({ rotulo }: { rotulo?: string }) {
  return (
    <span className="carregando" role={rotulo ? "status" : undefined}>
      <span className="giro" aria-hidden="true" />
      {rotulo && <span>{rotulo}</span>}
    </span>
  );
}
