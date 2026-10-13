import type { Config } from 'tailwindcss'
import tailwindcssAnimate from 'tailwindcss-animate'

/**
 * FreedomBot — purple design system via CSS variables (`.dark` on <html>).
 * Prefer theme tokens over hardcoded hex in components.
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
        purple: {
          50: 'rgb(var(--purple-50) / <alpha-value>)',
          100: 'rgb(var(--purple-100) / <alpha-value>)',
          200: 'rgb(var(--purple-200) / <alpha-value>)',
          300: 'rgb(var(--purple-300) / <alpha-value>)',
          400: 'rgb(var(--purple-400) / <alpha-value>)',
          500: 'rgb(var(--purple-500) / <alpha-value>)',
          600: 'rgb(var(--purple-600) / <alpha-value>)',
          700: 'rgb(var(--purple-700) / <alpha-value>)',
          800: 'rgb(var(--purple-800) / <alpha-value>)',
          900: 'rgb(var(--purple-900) / <alpha-value>)'
        },
        slate: {
          50: 'rgb(var(--slate-50) / <alpha-value>)',
          100: 'rgb(var(--slate-100) / <alpha-value>)',
          200: 'rgb(var(--slate-200) / <alpha-value>)',
          300: 'rgb(var(--slate-300) / <alpha-value>)',
          400: 'rgb(var(--slate-400) / <alpha-value>)',
          500: 'rgb(var(--slate-500) / <alpha-value>)',
          600: 'rgb(var(--slate-600) / <alpha-value>)',
          700: 'rgb(var(--slate-700) / <alpha-value>)',
          800: 'rgb(var(--slate-800) / <alpha-value>)',
          900: 'rgb(var(--slate-900) / <alpha-value>)'
        },
        primary: 'rgb(var(--color-primary) / <alpha-value>)',
        primaryAccent: 'rgb(var(--color-primary-accent) / <alpha-value>)',
        brand: {
          DEFAULT: 'rgb(var(--color-brand) / <alpha-value>)',
          soft: 'rgb(var(--color-brand-soft) / <alpha-value>)',
          deep: 'rgb(var(--color-brand-deep) / <alpha-value>)',
          ink: 'rgb(var(--color-brand-ink) / <alpha-value>)'
        },
        background: {
          DEFAULT: 'rgb(var(--color-bg) / <alpha-value>)',
          secondary: 'rgb(var(--color-bg-secondary) / <alpha-value>)',
          elevated: 'rgb(var(--color-bg-elevated) / <alpha-value>)',
          wash: 'rgb(var(--color-bg-wash) / <alpha-value>)'
        },
        secondary: 'rgb(var(--color-secondary) / <alpha-value>)',
        border: 'rgba(var(--color-border-default))',
        accent: 'rgb(var(--color-accent) / <alpha-value>)',
        muted: 'rgb(var(--color-muted) / <alpha-value>)',
        destructive: 'rgb(var(--color-destructive) / <alpha-value>)',
        positive: 'rgb(var(--color-positive) / <alpha-value>)',
        warning: 'rgb(var(--color-warning-fg) / <alpha-value>)',
        info: 'rgb(var(--color-info-fg) / <alpha-value>)',
        success: 'rgb(var(--color-positive) / <alpha-value>)',
        error: 'rgb(var(--color-destructive) / <alpha-value>)'
      },
      fontFamily: {
        geist: ['var(--font-ui)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        sans: ['var(--font-ui)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
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
        xs: ['0.75rem', { lineHeight: '1rem' }],
        sm: ['0.875rem', { lineHeight: '1.25rem' }],
        base: ['1rem', { lineHeight: '1.5rem' }],
        lg: ['1.125rem', { lineHeight: '1.5rem' }],
        xl: ['1.25rem', { lineHeight: '1.75rem' }],
        '2xl': ['1.5rem', { lineHeight: '2rem' }],
        '3xl': ['1.875rem', { lineHeight: '2.25rem' }],
        '4xl': ['2.25rem', { lineHeight: '2.5rem' }],
        '5xl': ['2.5rem', { lineHeight: '1.15' }],
        caption: ['0.75rem', { lineHeight: '1rem', letterSpacing: '0.04em' }],
        body: ['0.875rem', { lineHeight: '1.45rem' }],
        'body-lg': ['1rem', { lineHeight: '1.6rem' }],
        subtitle: ['1.125rem', { lineHeight: '1.5rem', letterSpacing: '-0.01em' }],
        title: ['1.5rem', { lineHeight: '2rem', letterSpacing: '-0.02em' }],
        display: ['2.5rem', { lineHeight: '1.15', letterSpacing: '-0.02em' }]
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
        panel: 'var(--shadow-panel)',
        glow: 'var(--shadow-glow)',
        lift: 'var(--shadow-lift)'
      },
      backgroundImage: {
        'app-atmosphere': 'var(--app-atmosphere)',
        'login-atmosphere': 'var(--login-atmosphere)',
        'brand-gradient': 'var(--brand-gradient)'
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
        'fade-up': 'fade-up 200ms var(--ease-out)',
        pulseSoft: 'pulseSoft 1.5s ease-in-out infinite'
      }
    }
  },
  plugins: [tailwindcssAnimate]
} satisfies Config
