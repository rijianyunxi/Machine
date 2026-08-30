import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// SPA 挂在 /app 下（见 server.py）；dev 时把 API/媒体代理到本地面板。
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/snapshots": "http://127.0.0.1:8000",
      "/test_results": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1024,
  },
});
