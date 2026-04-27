import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--color-background)",
        surface: "var(--color-surface)",
        "surface-elevated": "var(--color-surface-elevated)",
        border: "var(--color-border)",
        primary: "var(--color-primary)",
        "primary-hover": "var(--color-primary-hover)",
        "primary-light": "var(--color-primary-light)",
        success: "var(--color-success)",
        warning: "var(--color-warning)",
        error: "var(--color-error)",
        text: {
          primary: "var(--color-text-primary)",
          secondary: "var(--color-text-secondary)",
          muted: "var(--color-text-muted)",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)"],
        mono: ["var(--font-jetbrains-mono)"],
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(16px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-up": "fade-up 420ms ease-out both",
      },
    },
  },
};

export default config;
