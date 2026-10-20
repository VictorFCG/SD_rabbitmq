import { useState } from "react";
import { gravarCliente, lerCliente, removerCliente } from "./armazenamento";
import { Identificacao } from "./components/Identificacao";
import { Loja } from "./components/Loja";
import { ProvedorToasts } from "./toasts";
import type { Cliente } from "./tipos";

// Não há autenticação real: o e-mail (salvo no localStorage) identifica os pedidos
// e o canal SSE do cliente.
export default function App() {
  const [cliente, setCliente] = useState<Cliente | null>(lerCliente);

  if (!cliente) {
    return (
      <Identificacao
        aoEntrar={(novo) => {
          gravarCliente(novo);
          setCliente(novo);
        }}
      />
    );
  }

  return (
    <ProvedorToasts key={cliente.email}>
      <Loja
        cliente={cliente}
        aoTrocarUsuario={() => {
          removerCliente();
          setCliente(null);
        }}
      />
    </ProvedorToasts>
  );
}
