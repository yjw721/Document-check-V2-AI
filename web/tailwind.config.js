/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 深色宇宙基底
        cosmic: {
          DEFAULT: "#0a0a1f",
          deep: "#070718",
          dim: "#1a0b2e",
          glow: "#0f0f2e",
        },
        // 全息三原色
        holo: {
          pink: "#ff0080",
          purple: "#7928ca",
          cyan: "#00d4ff",
        },
      },
      fontFamily: {
        sans: ["\"Microsoft YaHei\"", "\"PingFang SC\"", "\"Segoe UI\"", "system-ui", "sans-serif"],
        mono: ["\"Cascadia Code\"", "Consolas", "monospace"],
      },
      borderRadius: {
        holo: "1rem", // rounded-2xl
      },
      keyframes: {
        holoFlow: {
          "0%, 100%": { backgroundPosition: "0% 50%" },
          "50%": { backgroundPosition: "100% 50%" },
        },
        fadeUp: {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "none" },
        },
        shimmer: {
          from: { backgroundPosition: "0% 0%" },
          to: { backgroundPosition: "200% 0%" },
        },
        pulseGlow: {
          "0%, 100%": { opacity: "0.55" },
          "50%": { opacity: "0.95" },
        },
      },
      animation: {
        holoFlow: "holoFlow 8s ease infinite",
        fadeUp: "fadeUp .28s ease both",
        shimmer: "shimmer 3s linear infinite",
        pulseGlow: "pulseGlow 3.2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
