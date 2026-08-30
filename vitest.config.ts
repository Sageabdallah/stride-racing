import { defineConfig } from "vitest/config";

// Server-side unit tests only; the Vite app config (root: client/) does not
// apply here.
export default defineConfig({
  test: {
    include: ["server/**/*.test.ts"],
    environment: "node",
  },
});
