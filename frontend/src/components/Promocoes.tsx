import { useCallback, useEffect, useEffectEvent, useId, useRef, useState, type FormEvent } from "react";
import {
  cancelarInteresse,
  ehIndisponibilidade,
  inscreverInteresse,
  listarCategorias,
  mensagemDeErro,
  obterInscricao,
} from "../api";
import { ehEmailValido, formatarHora, formatarMoeda, juntarFrases, listarNomes, normalizarEmail } from "../formatar";
import type { EstadoPromocoes } from "../hooks/usePromocoes";
import type { MostrarToast } from "../toasts";
import type { Categoria, Cliente, Promocao } from "../tipos";
import { Carregando, Icone } from "./Icone";

type EstadoInscricao =
  | { tipo: "invalido" }
  | { tipo: "carregando" }
  | { tipo: "ok"; email: string; categorias: string[]; confirmando: boolean }
  | { tipo: "erro"; mensagem: string; indisponivel: boolean };

// A inscrição é processada de forma assíncrona (gateway -> RabbitMQ -> MS Promoções),
// então a consulta de confirmação espera um pouco e tenta de novo se ainda não refletir.
const ESPERA_CONFIRMACAO_MS = 700;
const TENTATIVAS_CONFIRMACAO = 4;
const ESPERA_DIGITACAO_MS = 400;
const URL_MAILPIT = "http://localhost:8025";

function pausa(ms: number): Promise<void> {
  return new Promise((resolver) => window.setTimeout(resolver, ms));
}

function mesmoConjunto(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((codigo) => b.includes(codigo));
}

interface Props {
  cliente: Cliente;
  promocoes: EstadoPromocoes;
  mostrarToast: MostrarToast;
  /** Conexão SSE ativa: ao (re)conectar, refaz as consultas que tinham falhado. */
  conectado: boolean;
}

export function Promocoes({ cliente, promocoes, mostrarToast, conectado }: Props) {
  const ids = useId();
  const [categorias, setCategorias] = useState<Categoria[] | null>(null);
  const [carregandoCategorias, setCarregandoCategorias] = useState(true);
  const [erroCategorias, setErroCategorias] = useState<string | null>(null);
  const [email, setEmail] = useState(cliente.email);
  const [emailTocado, setEmailTocado] = useState(false);
  const [nome, setNome] = useState(cliente.nome);
  const [selecionadas, setSelecionadas] = useState<ReadonlySet<string>>(new Set());
  const [inscricao, setInscricao] = useState<EstadoInscricao>({ tipo: "carregando" });
  const [acao, setAcao] = useState<"salvar" | "cancelar" | null>(null);
  const consulta = useRef(0);
  const campoEmail = useRef<HTMLInputElement>(null);

  const emailNormalizado = normalizarEmail(email);
  const emailValido = ehEmailValido(email);
  const mostrarErroEmail = !emailValido && emailTocado;

  const nomeCategoria = (codigo: string) =>
    categorias?.find((categoria) => categoria.codigo === codigo)?.nome ?? codigo;

  const carregarCategorias = useCallback(async () => {
    setCarregandoCategorias(true);
    try {
      setCategorias(await listarCategorias());
      setErroCategorias(null);
    } catch (falha) {
      setErroCategorias(mensagemDeErro(falha));
    } finally {
      setCarregandoCategorias(false);
    }
  }, []);

  useEffect(() => {
    void carregarCategorias();
  }, [carregarCategorias]);

  /** Consulta a inscrição; com `esperado`, repete até o MS Promoções refletir a mudança. */
  const consultar = useCallback(async (alvo: string, esperado: string[] | null, aguardar: boolean) => {
    const minha = ++consulta.current;
    const tentativas = esperado ? TENTATIVAS_CONFIRMACAO : 1;
    for (let tentativa = 1; ; tentativa += 1) {
      if (aguardar || tentativa > 1) await pausa(ESPERA_CONFIRMACAO_MS);
      if (minha !== consulta.current) return;
      try {
        const resultado = await obterInscricao(alvo);
        if (minha !== consulta.current) return;
        if (esperado && !mesmoConjunto(resultado.categorias, esperado) && tentativa < tentativas) continue;
        setInscricao({ tipo: "ok", email: alvo, categorias: resultado.categorias, confirmando: false });
        setSelecionadas(new Set(resultado.categorias));
      } catch (falha) {
        if (minha !== consulta.current) return;
        setInscricao({ tipo: "erro", mensagem: mensagemDeErro(falha), indisponivel: ehIndisponibilidade(falha) });
      }
      return;
    }
  }, []);

  useEffect(() => {
    consulta.current += 1;
    if (!emailValido) {
      setInscricao({ tipo: "invalido" });
      return;
    }
    setInscricao({ tipo: "carregando" });
    const temporizador = window.setTimeout(() => void consultar(emailNormalizado, null, false), ESPERA_DIGITACAO_MS);
    return () => window.clearTimeout(temporizador);
  }, [emailNormalizado, emailValido, consultar]);

  useEffect(
    () => () => {
      consulta.current += 1;
    },
    [],
  );

  const refazerConsultasComErro = useEffectEvent(() => {
    if (!categorias && !carregandoCategorias) void carregarCategorias();
    if (inscricao.tipo === "erro" && emailValido) void consultar(emailNormalizado, null, false);
  });

  useEffect(() => {
    if (conectado) refazerConsultasComErro();
  }, [conectado]);

  const alternar = (codigo: string) => {
    setSelecionadas((atuais) => {
      const novas = new Set(atuais);
      if (novas.has(codigo)) novas.delete(codigo);
      else novas.add(codigo);
      return novas;
    });
  };

  const validarEmail = (): boolean => {
    setEmailTocado(true);
    if (emailValido) return true;
    campoEmail.current?.focus();
    return false;
  };

  const salvar = async (evento: FormEvent) => {
    evento.preventDefault();
    if (acao || !validarEmail()) return;
    const atuais = inscricao.tipo === "ok" && inscricao.email === emailNormalizado ? inscricao.categorias : null;
    const escolhidas = [...selecionadas].sort();
    const adicionadas = escolhidas.filter((codigo) => !atuais?.includes(codigo));
    const removidas = atuais ? atuais.filter((codigo) => !selecionadas.has(codigo)) : [];
    if (adicionadas.length === 0 && removidas.length === 0) {
      mostrarToast(
        "info",
        atuais ? "Nenhuma alteração nas preferências." : "Selecione ao menos uma categoria para se inscrever.",
      );
      return;
    }

    setAcao("salvar");
    try {
      if (adicionadas.length > 0) {
        await inscreverInteresse({ email: emailNormalizado, nome: nome.trim() || null, categorias: adicionadas });
      }
      if (removidas.length > 0) await cancelarInteresse(emailNormalizado, removidas);
      mostrarToast(
        "sucesso",
        juntarFrases(
          adicionadas.length ? `Inscrição em ${listarNomes(adicionadas.map(nomeCategoria))}` : null,
          removidas.length ? `Cancelamento em ${listarNomes(removidas.map(nomeCategoria))}` : null,
          "O MS Promoções processa a solicitação de forma assíncrona (RabbitMQ)",
        ),
        "Preferências enviadas",
      );
      // Sem a inscrição atual conhecida, o resultado final (atuais + novas) não é previsível.
      setInscricao({ tipo: "ok", email: emailNormalizado, categorias: atuais ? escolhidas : adicionadas, confirmando: true });
      void consultar(emailNormalizado, atuais ? escolhidas : null, true);
    } catch (falha) {
      mostrarToast("erro", mensagemDeErro(falha), "Não foi possível salvar as preferências");
      void consultar(emailNormalizado, null, true);
    } finally {
      setAcao(null);
    }
  };

  const cancelarTodas = async () => {
    if (acao || !validarEmail()) return;
    const confirmou = window.confirm(
      `Cancelar todas as inscrições de ${emailNormalizado}? Você deixará de receber e-mails de promoções.`,
    );
    if (!confirmou) return;
    setAcao("cancelar");
    try {
      await cancelarInteresse(emailNormalizado, []);
      mostrarToast(
        "sucesso",
        "Você não receberá mais e-mails de promoções. O MS Promoções processa o cancelamento de forma assíncrona (RabbitMQ).",
        "Inscrições canceladas",
      );
      setInscricao({ tipo: "ok", email: emailNormalizado, categorias: [], confirmando: true });
      setSelecionadas(new Set());
      void consultar(emailNormalizado, [], true);
    } catch (falha) {
      mostrarToast("erro", mensagemDeErro(falha), "Não foi possível cancelar as inscrições");
    } finally {
      setAcao(null);
    }
  };

  const interesses = inscricao.tipo === "ok" ? inscricao.categorias : [];
  const semInscricoes = inscricao.tipo === "ok" && inscricao.categorias.length === 0;
  // A consulta de confirmação redefine as caixas marcadas ao terminar; até lá elas ficam travadas.
  const confirmando = inscricao.tipo === "ok" && inscricao.confirmando;

  return (
    <div className="layout-promocoes">
      <section className="secao cartao" aria-labelledby="titulo-inscricao">
        <h2 id="titulo-inscricao">Receber promoções por e-mail</h2>
        <p className="secao-descricao">
          Escolha as categorias de interesse. Quando o MS Promoções gerar uma oferta nessas categorias, você recebe um
          e-mail.
        </p>

        <div className="inscricao-atual" aria-live="polite">
          {inscricao.tipo === "invalido" && <p>Informe um e-mail válido para consultar as inscrições.</p>}
          {inscricao.tipo === "carregando" && <Carregando rotulo="Consultando inscrições no MS Promoções..." />}
          {inscricao.tipo === "erro" && (
            <>
              <p className="texto-erro">
                {inscricao.indisponivel
                  ? "MS Promoções indisponível: não foi possível consultar as inscrições. Você ainda pode enviar novas inscrições; elas ficam na fila do RabbitMQ até o serviço voltar."
                  : `Não foi possível consultar as inscrições. ${juntarFrases(inscricao.mensagem)}`}
              </p>
              <button
                type="button"
                className="botao botao-pequeno botao-secundario inscricao-repetir"
                onClick={() => void consultar(emailNormalizado, null, false)}
              >
                Tentar novamente
              </button>
            </>
          )}
          {inscricao.tipo === "ok" &&
            (inscricao.categorias.length > 0 ? (
              <div className="chips-linha">
                <span>Você está inscrito em:</span>
                <ul className="chips">
                  {inscricao.categorias.map((codigo) => (
                    <li key={codigo} className="chip">
                      {nomeCategoria(codigo)}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p>Você ainda não está inscrito em nenhuma categoria.</p>
            ))}
          {confirmando && <Carregando rotulo="Confirmando com o MS Promoções..." />}
        </div>

        <form className="formulario" onSubmit={salvar} noValidate>
          <div className="campo">
            <label htmlFor={`${ids}-email`}>E-mail</label>
            <input
              id={`${ids}-email`}
              ref={campoEmail}
              type="email"
              inputMode="email"
              autoComplete="email"
              value={email}
              onChange={(evento) => setEmail(evento.target.value)}
              onBlur={() => setEmailTocado(true)}
              aria-invalid={mostrarErroEmail}
              aria-describedby={mostrarErroEmail ? `${ids}-email-erro` : undefined}
              required
            />
            {mostrarErroEmail && (
              <p id={`${ids}-email-erro`} className="campo-erro">
                Informe um e-mail válido (ex.: ana@exemplo.com).
              </p>
            )}
          </div>
          <div className="campo">
            <label htmlFor={`${ids}-nome`}>
              Nome <span className="opcional">(opcional)</span>
            </label>
            <input
              id={`${ids}-nome`}
              type="text"
              autoComplete="name"
              maxLength={80}
              value={nome}
              onChange={(evento) => setNome(evento.target.value)}
            />
          </div>

          <fieldset className="categorias" disabled={acao !== null || confirmando}>
            <legend>Categorias de interesse</legend>
            {!categorias && carregandoCategorias && <Carregando rotulo="Carregando categorias..." />}
            {!categorias && !carregandoCategorias && erroCategorias && (
              <div className="alerta alerta-erro">
                <Icone nome="alerta" />
                <p>Não foi possível carregar as categorias. {juntarFrases(erroCategorias)}</p>
                <button type="button" className="botao botao-pequeno" onClick={() => void carregarCategorias()}>
                  Tentar novamente
                </button>
              </div>
            )}
            {categorias?.map((categoria) => (
              <label key={categoria.codigo} className="opcao">
                <input
                  type="checkbox"
                  checked={selecionadas.has(categoria.codigo)}
                  onChange={() => alternar(categoria.codigo)}
                />
                <span className="opcao-codigo" aria-hidden="true">
                  {categoria.codigo}
                </span>
                <span>{categoria.nome}</span>
              </label>
            ))}
          </fieldset>

          <div className="formulario-acoes">
            <button type="submit" className="botao botao-primario" disabled={acao !== null || !categorias} aria-busy={acao === "salvar"}>
              {acao === "salvar" ? <Carregando rotulo="Salvando..." /> : "Salvar preferências"}
            </button>
            <button
              type="button"
              className="botao botao-perigo-contorno"
              onClick={() => void cancelarTodas()}
              disabled={acao !== null || semInscricoes}
              aria-busy={acao === "cancelar"}
            >
              {acao === "cancelar" ? <Carregando rotulo="Cancelando..." /> : "Cancelar todas as inscrições"}
            </button>
          </div>
        </form>

        <p className="nota nota-caixa">
          <Icone nome="email" tamanho={18} />
          <span>
            O MS Promoções envia os e-mails por uma API externa de e-mail (Resend). No ambiente local, sem chave do
            Resend, os e-mails chegam ao Mailpit:{" "}
            <a href={URL_MAILPIT} target="_blank" rel="noopener noreferrer">
              {URL_MAILPIT}
            </a>
            .
          </span>
        </p>
      </section>

      <section className="secao" aria-labelledby="titulo-promocoes">
        <div className="secao-cabecalho">
          <div>
            <h2 id="titulo-promocoes">Promoções em tempo real</h2>
            <p className="secao-descricao">
              O MS Promoções publica as ofertas no RabbitMQ e o API Gateway as repassa a todos os clientes conectados
              via SSE.
            </p>
          </div>
          <button
            type="button"
            className="botao botao-secundario"
            onClick={() => void promocoes.recarregar()}
            disabled={promocoes.carregando}
            aria-busy={promocoes.carregando}
          >
            <Icone nome="atualizar" tamanho={18} className={promocoes.carregando ? "girando" : undefined} />
            Atualizar
          </button>
        </div>

        {promocoes.erro && (
          <div className="alerta alerta-aviso" role="alert">
            <Icone nome="alerta" />
            <p>
              {promocoes.erro.indisponivel
                ? "O MS Promoções está indisponível no momento e as promoções recentes não puderam ser carregadas. As novas ofertas aparecem aqui assim que ele voltar."
                : `Não foi possível carregar as promoções recentes. ${juntarFrases(promocoes.erro.mensagem)}`}
            </p>
            <button
              type="button"
              className="botao botao-pequeno"
              onClick={() => void promocoes.recarregar()}
              disabled={promocoes.carregando}
            >
              Tentar novamente
            </button>
          </div>
        )}

        {promocoes.promocoes.length === 0 && promocoes.carregando && <Carregando rotulo="Carregando promoções..." />}
        {promocoes.promocoes.length === 0 && !promocoes.carregando && !promocoes.erro && (
          <div className="estado">
            <Icone nome="etiqueta" tamanho={36} />
            <p>Nenhuma promoção recente. As ofertas geradas pelo MS Promoções aparecem aqui automaticamente.</p>
          </div>
        )}

        {promocoes.promocoes.length > 0 && (
          <ul className="grade-promocoes">
            {promocoes.promocoes.map((promocao) => (
              <li key={promocao.id_promocao}>
                <CartaoPromocao
                  promocao={promocao}
                  doInteresse={interesses.includes(promocao.categoria)}
                  aoVivo={promocoes.aoVivo.has(promocao.id_promocao)}
                />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

interface PropsCartaoPromocao {
  promocao: Promocao;
  doInteresse: boolean;
  /** Recebida pelo SSE (e não na carga inicial): ganha a animação de destaque. */
  aoVivo: boolean;
}

function CartaoPromocao({ promocao, doInteresse, aoVivo }: PropsCartaoPromocao) {
  const [destacar, setDestacar] = useState(aoVivo);
  return (
    <article className="promocao" aria-label={`Promoção de ${promocao.produto}`}>
      {destacar && <span className="destaque" aria-hidden="true" onAnimationEnd={() => setDestacar(false)} />}
      <div className="promocao-topo">
        <span className="selo selo-promocao">-{Math.round(promocao.desconto)}%</span>
        {doInteresse && <span className="selo selo-sucesso">Do seu interesse</span>}
        <time className="promocao-hora" dateTime={promocao.criado_em}>
          {formatarHora(promocao.criado_em)}
        </time>
      </div>
      <h3 className="promocao-produto">{promocao.produto}</h3>
      <p className="promocao-categoria">{promocao.categoria_nome}</p>
      <p className="promocao-precos">
        <span className="sr-only">Preço original: </span>
        <s>{formatarMoeda(promocao.preco_original)}</s>
        <span className="sr-only">. Preço promocional: </span>
        <strong>{formatarMoeda(promocao.preco_promocional)}</strong>
      </p>
    </article>
  );
}
