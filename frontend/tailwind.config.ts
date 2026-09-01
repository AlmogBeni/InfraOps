import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: '#f4f5f0',
          sunken: '#e9ede7',
          raised: '#ffffff',
          panel: '#fafbf8',
        },
        brand: {
          50: '#edf7f3',
          100: '#d7eee5',
          200: '#b0ddce',
          300: '#7fc5ae',
          400: '#4ba88c',
          500: '#2b876d',
          600: '#1f6d58',
          700: '#174f40',
          800: '#153f34',
          900: '#12342b',
        },
        accent: {
          50: '#fff5ed',
          100: '#ffe7d5',
          300: '#f6aa72',
          500: '#e56b3f',
          700: '#ad4328',
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
