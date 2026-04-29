import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

const bridgePort = process.env.TRANSORIA_BRIDGE_PORT ?? "5018";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": `http://127.0.0.1:${bridgePort}`,
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    target: "es2022",
  },
});
