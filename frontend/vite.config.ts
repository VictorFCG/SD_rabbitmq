import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // 127.0.0.1:5173 é a origem liberada no CORS do gateway (FRONTEND_ORIGINS em common/config.py).
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
  preview: {
    port: 4173,
  },
});
