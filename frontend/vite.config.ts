import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), vue()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          "vue-vendor": ["vue", "vue-router", "pinia"],
          "primevue-vendor": ["primevue"],
          "i18n-vendor": ["vue-i18n"],
        },
      },
    },
  },
});
