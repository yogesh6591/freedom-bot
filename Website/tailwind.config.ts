import type { Config } from 'tailwindcss'

export default {
  darkMode: ['class'],
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#06070d',
          900: '#0b0e17',
          800: '#121826',
          700: '#1a2236',
          600: '#243049'
        },
        mist: {
          50: '#f4f6fb',
          100: '#e8ecf6',
          200: '#c9d1e6',
          300: '#9aa6c4',
          400: '#6d7a9b',
          500: '#4e5b7a'
        },
        brand: {
          DEFAULT: '#6d7cff',
          soft: '#8b97ff',
          deep: '#4f5ef0',
          glow: 'rgba(109, 124, 255, 0.22)'
        },
        accent: {
          DEFAULT: '#3dd6c6',
          soft: '#7eefe4'
        }
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['var(--font-display)', 'var(--font-sans)', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'monospace']
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(109,124,255,0.25), 0 18px 50px -24px rgba(79,94,240,0.55)',
        panel: '0 1px 0 rgba(255,255,255,0.04), 0 24px 60px -28px rgba(0,0,0,0.65)'
      },
      backgroundImage: {
        atmosphere:
          'radial-gradient(900px 480px at 78% -8%, rgba(109,124,255,0.18), transparent 58%), radial-gradient(700px 420px at 12% 110%, rgba(61,214,198,0.08), transparent 55%), linear-gradient(180deg, #06070d 0%, #0b0e17 100%)',
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
