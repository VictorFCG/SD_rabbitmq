/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Endereço do API Gateway (MS Principal). Padrão: http://127.0.0.1:8001 */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
