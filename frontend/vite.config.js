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
const proxyTimeoutMs = Number(process.env.VITE_DEV_PROXY_TIMEOUT_MS || 600000);

function writeProxyError(res, message) {
  if (!res || res.headersSent) return;
  res.writeHead(502, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ detail: message }));
}

export default defineConfig({
  base: process.env.VITE_BASE_PATH || "/",
  plugins: [react()],
  server: {
    allowedHosts,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
        secure: false,
        timeout: proxyTimeoutMs,
        proxyTimeout: proxyTimeoutMs,
        configure: (proxy) => {
          proxy.on("error", (error, _request, response) => {
            writeProxyError(
              response,
              `Proxy frontend tidak dapat menjangkau backend API (${apiProxyTarget}): ${error.message}`,
            );
          });
        },
      },
    },
  },
  preview: {
    allowedHosts,
  },
});
