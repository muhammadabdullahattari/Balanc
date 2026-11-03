import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    headers: {
      "Referrer-Policy": "no-referrer-when-downgrade",
      "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    },
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  preview: {
    headers: {
      "Referrer-Policy": "no-referrer-when-downgrade",
      "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    },
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          forms: ["react-hook-form", "zod", "@hookform/resolvers/zod"],
          query: ["@tanstack/react-query"],
        },
      },
    },
  },
});
