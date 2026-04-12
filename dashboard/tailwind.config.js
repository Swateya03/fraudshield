/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans:  ['DM Sans', 'system-ui', 'sans-serif'],
        mono:  ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      colors: {
        // Design system — fraud ops dark theme
        canvas:  '#0a0e13',
        surface: '#111720',
        panel:   '#161d27',
        border:  '#1e2a38',
        muted:   '#2a3a4e',
        // Semantic
        allow:   { DEFAULT: '#0d9e75', dim: '#064d38', text: '#5de4b4' },
        block:   { DEFAULT: '#d84040', dim: '#5c1414', text: '#f87171' },
        review:  { DEFAULT: '#c47f12', dim: '#5c3a06', text: '#fbbf24' },
        info:    { DEFAULT: '#2979d4', dim: '#0e2d5c', text: '#7bb8f8' },
      },
      keyframes: {
        pulse_soft: {
          '0%,100%': { opacity: 1 },
          '50%':      { opacity: 0.4 },
        },
        slide_in: {
          from: { transform: 'translateY(-8px)', opacity: 0 },
          to:   { transform: 'translateY(0)',    opacity: 1 },
        },
      },
      animation: {
        pulse_soft: 'pulse_soft 2s ease-in-out infinite',
        slide_in:   'slide_in 0.25s ease-out',
      },
    },
  },
  plugins: [],
}
