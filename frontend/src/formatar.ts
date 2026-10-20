const MOEDA = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const DATA_HORA = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "medium" });
const HORA = new Intl.DateTimeFormat("pt-BR", { timeStyle: "medium" });

const REGEX_EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function formatarMoeda(valor: number): string {
  return MOEDA.format(valor);
}

/** Converte um timestamp ISO (o backend envia microssegundos) em milissegundos. */
export function paraMs(iso: string | null | undefined): number {
  if (!iso) return Number.NaN;
  return Date.parse(iso.replace(/(\.\d{3})\d+/, "$1"));
}

export function formatarDataHora(iso: string | null | undefined): string {
  const ms = paraMs(iso);
  return Number.isNaN(ms) ? iso || "-" : DATA_HORA.format(ms);
}

export function formatarHora(iso: string | null | undefined): string {
  const ms = paraMs(iso);
  return Number.isNaN(ms) ? iso || "-" : HORA.format(ms);
}

export function ehEmailValido(email: string): boolean {
  return REGEX_EMAIL.test(email.trim());
}

/** O backend identifica o cliente pelo e-mail sem espaços e em minúsculas. */
export function normalizarEmail(email: string): string {
  return email.trim().toLowerCase();
}

export function arredondar(valor: number): number {
  return Math.round(valor * 100) / 100;
}

export function listarNomes(nomes: string[]): string {
  if (nomes.length <= 1) return nomes.join("");
  return `${nomes.slice(0, -1).join(", ")} e ${nomes[nomes.length - 1]}`;
}

export function plural(quantidade: number, singular: string, pluralTexto: string): string {
  return `${quantidade} ${quantidade === 1 ? singular : pluralTexto}`;
}

/** Aceita apenas URLs http(s) (evita navegar para javascript:, data: etc.). */
export function ehUrlHttp(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const { protocol } = new URL(url);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

/** Junta frases garantindo a pontuação final de cada uma. */
export function juntarFrases(...frases: (string | null | undefined)[]): string {
  return frases
    .map((frase) => (frase ?? "").trim())
    .filter(Boolean)
    .map((frase) => (/[.!?…]$/.test(frase) ? frase : `${frase}.`))
    .join(" ");
}

/** Aviso exibido quando a quantidade pedida passa do estoque mostrado no catálogo. */
export function avisoEstoque(disponivel: number): string {
  return disponivel <= 0
    ? "Produto esgotado - o MS Estoque pode recusar o pedido"
    : `Somente ${disponivel} em estoque - o MS Estoque pode recusar o pedido`;
}
