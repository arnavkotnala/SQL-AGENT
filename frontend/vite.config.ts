import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000; the proxy keeps the browser on one origin and
// the backend's secrets server-side. In production FastAPI serves the built files itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
});
