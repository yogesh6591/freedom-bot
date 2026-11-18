import type { Config } from 'tailwindcss'

export default {
  darkMode: ['class'],
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#140b20',
          900: '#1c102b',
          800: '#241538',
          700: '#2d1b45',
          600: '#382254'
        },
        mist: {
          50: '#f7f5fb',
          100: '#f0ecf8',
          200: '#dfd8ec',
          300: '#c0b6d4',
          400: '#9a8eb2',
          500: '#6e6187'
        },
        brand: {
          DEFAULT: '#5b00e4',
          soft: '#8740f2',
          deep: '#4800b8',
          glow: 'rgba(91, 0, 228, 0.22)'
        },
        accent: {
          DEFAULT: '#8740f2',
          soft: '#b58cff'
        }
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['var(--font-display)', 'var(--font-sans)', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'monospace']
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(91,0,228,0.25), 0 18px 50px -24px rgba(91,0,228,0.55)',
        panel: '0 1px 0 rgba(255,255,255,0.04), 0 24px 60px -28px rgba(0,0,0,0.65)'
      },
      backgroundImage: {
        atmosphere:
          'radial-gradient(900px 480px at 78% -8%, rgba(91,0,228,0.30), transparent 58%), radial-gradient(700px 420px at 12% 110%, rgba(135,64,242,0.10), transparent 55%), linear-gradient(180deg, #1c102b 0%, #170d24 100%)',
        'hero-grid':
          'linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)'
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(16px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' }
        },
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-8px)' }
        },
        shimmer: {
          '0%': { backgroundPosition: '200% 0' },
          '100%': { backgroundPosition: '-200% 0' }
        }
      },
      animation: {
        'fade-up': 'fade-up 600ms cubic-bezier(0.22, 1, 0.36, 1) both',
        float: 'float 6s ease-in-out infinite',
        shimmer: 'shimmer 8s linear infinite'
      }
    }
  },
  plugins: []
} satisfies Config
