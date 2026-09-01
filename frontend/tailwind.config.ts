import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: '#f8fbff',
          sunken: '#eef3fb',
          raised: '#ffffff',
          panel: '#f8fbff',
        },
        brand: {
          50: '#edf2ff',
          100: '#dce5ff',
          200: '#bfd3ff',
          300: '#93b4ff',
          400: '#6a95f4',
          500: '#3b74e8',
          600: '#2456bf',
          700: '#1a4396',
          800: '#16366f',
          900: '#10274f',
        },
        accent: {
          50: '#ecfbff',
          100: '#caf1ff',
          300: '#63d7f0',
          500: '#16c5ea',
          700: '#0798c0',
        },
        signal: {
          ok: '#16a34a',
          warn: '#f59e0b',
          bad: '#dc2626',
          muted: '#94a3b8',
        },
      },
      fontFamily: {
        sans: [
          'Inter',
          'Inter var',
          'Segoe UI',
          'system-ui',
          '-apple-system',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'Cascadia Code', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config
