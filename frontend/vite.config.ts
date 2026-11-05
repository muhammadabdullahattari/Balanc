import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { webSecurityPolicy } from "./security-policy";

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    {
      name: "production-content-security-policy",
      apply: "build",
      transformIndexHtml: {
        order: "pre",
        handler() {
          const env = loadEnv(mode, process.cwd(), "VITE_");
          return [
            {
              tag: "meta",
              attrs: {
                "http-equiv": "Content-Security-Policy",
                content: webSecurityPolicy(env.VITE_API_ORIGIN),
              },
              injectTo: "head-prepend",
            },
          ];
        },
      },
    },
  ],
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
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
      "Content-Security-Policy": "frame-ancestors 'none'",
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
}));
