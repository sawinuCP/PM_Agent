import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  appType: "spa",
  preview: {
    port: 5173,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyRes", (response) => {
            if (response.headers["content-type"]?.includes("text/event-stream")) {
              response.headers["cache-control"] = "no-cache, no-transform";
              response.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
    },
  },
  test: {
    environment: "node",
    globals: false,
  },
});
