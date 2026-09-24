import type { Config } from 'tailwindcss'
import tailwindcssAnimate from 'tailwindcss-animate'

/**
 * FreedomBot v2 — light product theme.
 * White surfaces + bold yellow accent. Intentionally different from the prior
 * dark charcoal / copper console look.
 */
export default {
  darkMode: ['class'],
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}'
  ],
  theme: {
    extend: {
      colors: {
        primary: '#14120B',
        primaryAccent: '#FFFFFF',
        brand: {
          DEFAULT: '#F5C400',
          soft: '#FFE566',
          deep: '#C9A000',
          ink: '#1A1500'
        },
        background: {
          DEFAULT: '#FFFDF7',
          secondary: '#FFFFFF',
          elevated: '#FFF8E1',
          wash: '#FFF3C4'
        },
        secondary: '#3D3A32',
        border: 'rgba(var(--color-border-default))',
        accent: '#FFF8E1',
        muted: '#6B6558',
        destructive: '#D94848',
        positive: '#1B8A5A',
        info: '#1F6F8B'
      },
      fontFamily: {
        geist: ['var(--font-ui)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        dmmono: ['var(--font-mono)', 'ui-monospace', 'monospace']
      },
      borderRadius: {
        xl: '16px',
        lg: '12px'
      },
      boxShadow: {
        panel: '0 1px 2px rgba(20, 18, 11, 0.04), 0 8px 24px rgba(20, 18, 11, 0.06)',
        glow: '0 0 0 3px rgba(245, 196, 0, 0.35)',
        lift: '0 12px 40px rgba(20, 18, 11, 0.1)'
      },
      backgroundImage: {
        'app-atmosphere':
          'radial-gradient(900px 420px at 0% 0%, rgba(245, 196, 0, 0.18), transparent 55%), radial-gradient(700px 380px at 100% 0%, rgba(255, 243, 196, 0.9), transparent 50%), linear-gradient(180deg, #FFFDF7 0%, #FFFFFF 40%, #FFFDF7 100%)',
        'login-atmosphere':
          'radial-gradient(800px 500px at 15% 20%, rgba(245, 196, 0, 0.45), transparent 55%), radial-gradient(600px 400px at 90% 80%, rgba(255, 229, 102, 0.35), transparent 50%), linear-gradient(160deg, #FFFFFF 0%, #FFF8E1 55%, #FFF3C4 100%)'
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' }
        },
        pulseSoft: {
          '0%, 100%': { opacity: '0.4' },
          '50%': { opacity: '1' }
        }
      },
      animation: {
        'fade-up': 'fade-up 0.4s ease-out',
        pulseSoft: 'pulseSoft 1.5s ease-in-out infinite'
      }
    }
  },
  plugins: [tailwindcssAnimate]
} satisfies Config
