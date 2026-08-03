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
        cream: {
          50: '#FDFCF7',
          100: '#F9F8F2',
          200: '#F3F1E7',
          300: '#EAE7D8',
          400: '#D5D1BD',
          500: '#BDB79D',
        },
        pill: {
          bg: '#E7E6DF',
          border: '#D8D7CE',
          text: '#2D2E30',
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
