import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// VITE_API_BASE_URL — adres API widziany z przeglądarki (puste = ten sam origin, np. za
// reverse proxy albo przez proxy dev-serwera). VITE_DEV_PROXY_TARGET — dokąd dev-serwer
// przekazuje /api (domyślnie lokalny uvicorn).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  const target = env.VITE_DEV_PROXY_TARGET || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": { target, changeOrigin: false },
      },
    },
    preview: { port: 4173, proxy: { "/api": { target } } },
    test: { environment: "node" },
  };
});
