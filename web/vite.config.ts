import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 文档核验中心 · 全息渐变 UI
// 构建产物为纯静态文件；API 默认指向 http://127.0.0.1:8501（同源时自动用相对路径）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: "127.0.0.1",
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1200,
  },
});
