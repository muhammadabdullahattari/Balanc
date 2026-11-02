import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    environmentOptions: { jsdom: { url: "http://127.0.0.1:8000" } },
    include: ["tests/live.acceptance.tsx"],
    testTimeout: 45000,
    hookTimeout: 30000,
    setupFiles: ["./tests/setup.ts"],
  },
});
