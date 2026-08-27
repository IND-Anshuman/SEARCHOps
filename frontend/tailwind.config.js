/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        background: {
          DEFAULT: '#020617', // slate-950
          surface: '#0f172a',    // slate-900
          card: '#131c31',       // slate-850
          hover: '#1e293b',      // slate-800
        },
        border: {
          DEFAULT: '#1e293b',    // slate-800
          subtle: '#334155',     // slate-700
          accent: '#475569',     // slate-600
        },
        brand: {
          emerald: '#10b981',
          indigo: '#6366f1',
          cyan: '#06b6d4',
          amber: '#f59e0b',
          rose: '#f43f5e',
        }
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['10px', '14px'],
        'xs': ['11px', '16px'],
        'sm': ['12px', '18px'],
        'base': ['13px', '20px'],
        'md': ['14px', '22px'],
      }
    },
  },
  plugins: [],
}
