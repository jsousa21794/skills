import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      // Nos testes não há ambiente de React Server Components; o marcador é inofensivo.
      "server-only": path.resolve(__dirname, "tests/helpers/server-only.ts"),
    },
  },
});
