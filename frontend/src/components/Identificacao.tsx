import { useId, useRef, useState, type FormEvent } from "react";
import { API_URL } from "../api";
import { ehEmailValido, normalizarEmail } from "../formatar";
import type { Cliente } from "../tipos";
import { Icone } from "./Icone";

const TAMANHO_MAXIMO_NOME = 80;

export function Identificacao({ aoEntrar }: { aoEntrar: (cliente: Cliente) => void }) {
  const ids = useId();
  const [nome, setNome] = useState("");
  const [email, setEmail] = useState("");
  const [tocados, setTocados] = useState({ nome: false, email: false });
  const campoNome = useRef<HTMLInputElement>(null);
  const campoEmail = useRef<HTMLInputElement>(null);

  const erroNome = !nome.trim()
    ? "Informe seu nome."
    : nome.trim().length > TAMANHO_MAXIMO_NOME
      ? `Use no máximo ${TAMANHO_MAXIMO_NOME} caracteres.`
      : null;
  const erroEmail = !email.trim()
    ? "Informe seu e-mail."
    : !ehEmailValido(email)
      ? "Informe um e-mail válido (ex.: ana@exemplo.com)."
      : null;
  const mostrarErroNome = tocados.nome && erroNome;
  const mostrarErroEmail = tocados.email && erroEmail;

  const enviar = (evento: FormEvent) => {
    evento.preventDefault();
    setTocados({ nome: true, email: true });
    if (erroNome) {
      campoNome.current?.focus();
      return;
    }
    if (erroEmail) {
      campoEmail.current?.focus();
      return;
    }
    aoEntrar({ nome: nome.trim(), email: normalizarEmail(email) });
  };

  return (
    <main className="identificacao">
      <div className="identificacao-cartao">
        <div className="marca marca-grande">
          <span className="marca-logo" aria-hidden="true">
            <Icone nome="carrinho" tamanho={28} />
          </span>
          <span className="marca-nome">Loja SD</span>
        </div>
        <h1>Bem-vindo à Loja SD</h1>
        <p className="identificacao-texto">
          E-commerce do Trab2 de Sistemas Distribuídos: microsserviços que conversam pelo RabbitMQ, API Gateway REST e
          notificações em tempo real via SSE.
        </p>

        <form className="formulario" onSubmit={enviar} noValidate>
          <div className="campo">
            <label htmlFor={`${ids}-nome`}>Nome</label>
            <input
              id={`${ids}-nome`}
              ref={campoNome}
              type="text"
              autoComplete="name"
              value={nome}
              maxLength={TAMANHO_MAXIMO_NOME + 20}
              onChange={(evento) => setNome(evento.target.value)}
              onBlur={() => setTocados((atual) => ({ ...atual, nome: true }))}
              aria-invalid={Boolean(mostrarErroNome)}
              aria-describedby={mostrarErroNome ? `${ids}-nome-erro` : undefined}
              required
              autoFocus
            />
            {mostrarErroNome && (
              <p id={`${ids}-nome-erro`} className="campo-erro">
                {erroNome}
              </p>
            )}
          </div>
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
              onBlur={() => setTocados((atual) => ({ ...atual, email: true }))}
              aria-invalid={Boolean(mostrarErroEmail)}
              aria-describedby={mostrarErroEmail ? `${ids}-email-erro` : `${ids}-email-dica`}
              required
            />
            {mostrarErroEmail ? (
              <p id={`${ids}-email-erro`} className="campo-erro">
                {erroEmail}
              </p>
            ) : (
              <p id={`${ids}-email-dica`} className="campo-dica">
                Sem senha: o e-mail identifica seus pedidos e o seu canal de notificações em tempo real.
              </p>
            )}
          </div>
          <button type="submit" className="botao botao-primario botao-largo">
            Entrar na loja
          </button>
        </form>
        <p className="identificacao-rodape">
          API Gateway: <span className="mono">{API_URL}</span>
        </p>
      </div>
    </main>
  );
}
