import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const allowedHosts = (
  process.env.VITE_ALLOWED_HOSTS ||
  "limitation-combined-premier-sip.trycloudflare.com"
)
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

const apiProxyTarget = process.env.VITE_DEV_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  base: process.env.VITE_BASE_PATH || "/",
  plugins: [react()],
  server: {
    allowedHosts,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  preview: {
    allowedHosts,
  },
});
