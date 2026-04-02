import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  // For GitHub Pages deployment: set base to your repo name, e.g.:
  // base: "/tn811-monitor/",
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  // Serve repo root as the public base so /data/exports/*.json is reachable
  // in the Vite dev server without a separate static file server.
  publicDir: path.resolve(__dirname, "../../"),
  server: {
    fs: {
      allow: ["../.."],
    },
  },
});
