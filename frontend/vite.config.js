import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const allowedHosts = (
  process.env.VITE_ALLOWED_HOSTS ||
  "limitation-combined-premier-sip.trycloudflare.com"
)
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

export default defineConfig({
  base: process.env.VITE_BASE_PATH || "/",
  plugins: [react()],
  server: {
    allowedHosts,
  },
  preview: {
    allowedHosts,
  },
});
