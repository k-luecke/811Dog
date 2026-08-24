import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  // For a project-page deployment, set base to the repo name, e.g.:
  // base: "/tn811-monitor/",
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  // Dev: serve the repo root so /data/exports/*.json is reachable without
  // standing up a second static server.
  //
  // Build: use the conventional public/ directory, which is where CI copies the
  // JSON exports (see .github/workflows/publish.yml). Pointing publicDir at the
  // repo root during a build makes Vite copy the entire repo — including .git
  // and dist itself — into dist, which recurses until the path is too long.
  publicDir: command === "serve" ? path.resolve(__dirname, "../../") : "public",
  server: {
    fs: {
      allow: ["../.."],
    },
  },
}));
