/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        lexend: ['Lexend', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      colors: {
        dark: {
          bg: '#070B14',
          surface: '#0B1220',
          card: 'rgba(15, 23, 42, 0.85)',
          nested: 'rgba(30, 41, 59, 0.55)',
        },
        pill: {
          bg: 'rgba(30, 41, 59, 0.6)',
          border: 'rgba(71, 85, 105, 0.5)',
          text: '#e2e8f0',
        },
      },
      animation: {
        'slide-in-left': 'slideInLeft 1.6s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        'slide-in-right': 'slideInRight 1.6s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        'hero-title-in': 'heroTitleIn 1.2s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        'hero-fade-in': 'heroFadeIn 1.4s cubic-bezier(0.16, 1, 0.3, 1) forwards',
        'pulse-subtle': 'pulseSubtle 3s ease-in-out infinite',
      },
      keyframes: {
        slideInLeft: {
          '0%': { transform: 'translateX(-100%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '0.45' },
        },
        slideInRight: {
          '0%': { transform: 'translateX(100%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '0.45' },
        },
        heroTitleIn: {
          '0%': { transform: 'translateY(36px) scale(0.96)', opacity: '0' },
          '100%': { transform: 'translateY(0) scale(1)', opacity: '1' },
        },
        heroFadeIn: {
          '0%': { transform: 'translateY(24px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        pulseSubtle: {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.85', transform: 'scale(1.02)' },
        },
      },
    },
  },
  plugins: [],
}
