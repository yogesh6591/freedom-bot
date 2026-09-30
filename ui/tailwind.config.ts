import type { Config } from 'tailwindcss'
import tailwindcssAnimate from 'tailwindcss-animate'

/**
 * FreedomBot — JeanneCAIO client-facing command center.
 * Dark / black surfaces + bright purple–blue brand accents.
 * Visual only; product behavior is unchanged.
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
        primary: '#F4F2FF',
        primaryAccent: '#0A0A12',
        brand: {
          DEFAULT: '#8B5CF6',
          soft: '#A78BFA',
          deep: '#6366F1',
          ink: '#F8F7FF'
        },
        background: {
          DEFAULT: '#07070F',
          secondary: '#10101C',
          elevated: '#171728',
          wash: '#1C1834'
        },
        secondary: '#C9C5DE',
        border: 'rgba(var(--color-border-default))',
        accent: '#1C1834',
        muted: '#8E89A8',
        destructive: '#F87171',
        positive: '#34D399',
        info: '#38BDF8'
      },
      fontFamily: {
        geist: ['var(--font-ui)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        dmmono: ['var(--font-mono)', 'ui-monospace', 'monospace']
      },
      borderRadius: {
        xl: '16px',
        lg: '12px',
        md: '10px',
        sm: '8px'
      },
      spacing: {
        4.5: '1.125rem',
        13: '3.25rem',
        15: '3.75rem',
        18: '4.5rem'
      },
      fontSize: {
        caption: ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.04em' }],
        body: ['0.875rem', { lineHeight: '1.45rem' }],
        'body-lg': ['1rem', { lineHeight: '1.6rem' }],
        subtitle: ['1.125rem', { lineHeight: '1.5rem', letterSpacing: '-0.01em' }],
        title: ['1.25rem', { lineHeight: '1.6rem', letterSpacing: '-0.02em' }],
        display: ['2.25rem', { lineHeight: '1.15', letterSpacing: '-0.02em' }]
      },
      maxWidth: {
        prose: '70ch',
        measure: '42rem'
      },
      transitionDuration: {
        fast: '150ms',
        base: '200ms',
        slow: '250ms'
      },
      boxShadow: {
        panel:
          '0 0 0 1px rgba(139, 92, 246, 0.12), 0 8px 32px rgba(0, 0, 0, 0.45)',
        glow: '0 0 0 2px rgba(139, 92, 246, 0.45), 0 0 24px rgba(99, 102, 241, 0.35)',
        lift: '0 16px 48px rgba(0, 0, 0, 0.55), 0 0 40px rgba(99, 102, 241, 0.15)'
      },
      backgroundImage: {
        'app-atmosphere':
          'radial-gradient(900px 480px at 0% -10%, rgba(139, 92, 246, 0.28), transparent 55%), radial-gradient(700px 420px at 100% 0%, rgba(56, 189, 248, 0.14), transparent 50%), radial-gradient(600px 400px at 50% 120%, rgba(99, 102, 241, 0.12), transparent 55%), linear-gradient(180deg, #07070F 0%, #0A0A14 45%, #07070F 100%)',
        'login-atmosphere':
          'radial-gradient(900px 560px at 10% 15%, rgba(139, 92, 246, 0.4), transparent 55%), radial-gradient(700px 480px at 95% 85%, rgba(56, 189, 248, 0.22), transparent 50%), linear-gradient(165deg, #05050C 0%, #0E0A1C 50%, #070712 100%)'
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
