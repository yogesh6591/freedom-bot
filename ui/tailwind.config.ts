import type { Config } from 'tailwindcss'
import tailwindcssAnimate from 'tailwindcss-animate'

/**
 * FreedomBot — light + dark via CSS variables (`.dark` on <html>).
 * Brand stays purple/violet; surfaces flip with theme.
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
        info: 'rgb(var(--color-info) / <alpha-value>)'
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
        panel: 'var(--shadow-panel)',
        glow: 'var(--shadow-glow)',
        lift: 'var(--shadow-lift)'
      },
      backgroundImage: {
        'app-atmosphere': 'var(--app-atmosphere)',
        'login-atmosphere': 'var(--login-atmosphere)'
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
