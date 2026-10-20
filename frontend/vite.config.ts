import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
// O dev server escuta em 127.0.0.1:5173, origem liberada no CORS do API Gateway
// (FRONTEND_ORIGINS em common/config.py).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
  preview: {
    port: 4173,
  },
});
