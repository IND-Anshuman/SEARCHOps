/** @type {import('tailwindcss').Config} */
function withOpacity(variableName) {
  return ({ opacityValue }) => {
    if (opacityValue !== undefined) {
      return `rgba(var(${variableName}), ${opacityValue})`;
    }
    return `rgb(var(${variableName}))`;
  };
}

export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // ── Dynamic Theme Tokens ─────────────────────────────────────────────
        // Responsive to .dark class via CSS custom properties.
        paper: {
          DEFAULT: withOpacity('--paper-rgb'),
          raised: withOpacity('--paper-raised-rgb'),
          inset: withOpacity('--paper-inset-rgb'),
          deep: withOpacity('--paper-deep-rgb'),
        },
        ink: {
          DEFAULT: withOpacity('--ink-rgb'),
          muted: withOpacity('--ink-muted-rgb'),
          faint: withOpacity('--ink-faint-rgb'),
          ghost: withOpacity('--ink-ghost-rgb'),
        },
        rule: {
          DEFAULT: withOpacity('--rule-rgb'),
          strong: withOpacity('--rule-strong-rgb'),
        },
        background: {
          DEFAULT: withOpacity('--paper-rgb'),
          surface: withOpacity('--paper-raised-rgb'),
          raised: withOpacity('--paper-raised-rgb'),
          overlay: withOpacity('--paper-inset-rgb'),
        },
        border: {
          DEFAULT: withOpacity('--rule-rgb'),
          subtle: withOpacity('--rule-rgb'),
          strong: withOpacity('--rule-strong-rgb'),
        },
        accent: {
          DEFAULT: withOpacity('--ink-rgb'),
          strong: withOpacity('--ink-rgb'),
          muted: 'rgba(var(--ink-rgb), 0.06)',
          border: 'rgba(var(--ink-rgb), 0.20)',
        },
        success: { DEFAULT: withOpacity('--ink-rgb'), muted: withOpacity('--paper-inset-rgb') },
        warning: { DEFAULT: withOpacity('--ink-rgb'), muted: withOpacity('--paper-inset-rgb') },
        danger: { DEFAULT: withOpacity('--ink-rgb'), muted: withOpacity('--paper-inset-rgb') },
        info: { DEFAULT: withOpacity('--ink-rgb'), muted: withOpacity('--paper-inset-rgb') },
      },
      fontFamily: {
        // Five voices, strictly assigned. See DESIGN.md.
        //  display — a letterpress face. The masthead, the report title,
        //            section heads, pull quotes. Never UI labels, never data.
        //  serif   — a reading face. The report body, at a 68ch measure.
        //  sans    — a grotesque. All interface chrome.
        //  mono    — data only. Never a label, never a heading.
        display: ['Fraunces', 'Georgia', 'serif'],
        serif: ['"Source Serif 4"', 'Georgia', 'serif'],
        sans: ['"Instrument Sans"', 'system-ui', '-apple-system', '"Segoe UI"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        // A literary scale, not a dashboard scale. The steps are visible.
        'display-xl': ['3.25rem', { lineHeight: '1.02', letterSpacing: '-0.03em' }],
        'display-lg': ['2.5rem', { lineHeight: '1.06', letterSpacing: '-0.025em' }],
        'display-md': ['1.75rem', { lineHeight: '1.14', letterSpacing: '-0.02em' }],
        'display-sm': ['1.3125rem', { lineHeight: '1.24', letterSpacing: '-0.015em' }],
        'body-lg': ['1.0625rem', { lineHeight: '1.62' }],
        body: ['0.9375rem', { lineHeight: '1.55' }],
        label: ['0.8125rem', { lineHeight: '1.4' }],
        micro: ['0.6875rem', { lineHeight: '1.35' }],
        data: ['0.8125rem', { lineHeight: '1.45' }],
        '2xs': ['11px', '16px'],
        xs: ['12px', '18px'],
        sm: ['13px', '20px'],
        base: ['14px', '22px'],
        md: ['16px', '24px'],
        lg: ['19px', '28px'],
        xl: ['22px', '30px'],
        '2xl': ['28px', '36px'],
      },
      borderRadius: {
        none: '0px',
        sm: '4px',
        md: '6px',
        lg: '8px',
        xl: '12px',
        '2xl': '16px',
        card: '8px',
        pill: '999px',
      },
      boxShadow: {
        drawer: '0 2px 4px rgba(0, 0, 0, 0.08), 0 16px 40px -16px rgba(0, 0, 0, 0.35)',
        sheet: '0 1px 3px rgba(0, 0, 0, 0.06), 0 8px 24px -4px rgba(0, 0, 0, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.6)',
        'sheet-dark': '0 2px 6px rgba(0, 0, 0, 0.6), 0 12px 32px -4px rgba(0, 0, 0, 0.8), inset 0 1px 0 rgba(255, 255, 255, 0.06)',
        debossed: 'inset 0 1.5px 3px rgba(0, 0, 0, 0.08), inset 0 0 0 1px rgba(0, 0, 0, 0.04), 0 1px 0 rgba(255, 255, 255, 0.6)',
        'debossed-dark': 'inset 0 1.5px 4px rgba(0, 0, 0, 0.7), inset 0 0 0 1px rgba(255, 255, 255, 0.03), 0 1px 0 rgba(255, 255, 255, 0.02)',
        card: '0 1px 2px rgba(0, 0, 0, 0.05), 0 4px 12px -2px rgba(0, 0, 0, 0.06)',
        'card-dark': '0 1px 3px rgba(0, 0, 0, 0.5), 0 6px 18px -2px rgba(0, 0, 0, 0.7)',
        raised: '0 2px 5px rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.04)',
      },
      backgroundImage: {
        'paper-gradient': 'linear-gradient(180deg, rgba(255, 255, 255, 0.65) 0%, rgba(240, 235, 224, 0.45) 100%)',
        'paper-raised-gradient': 'linear-gradient(180deg, rgba(255, 255, 255, 0.85) 0%, rgba(245, 241, 232, 0.6) 100%)',
        'carbon-gradient': 'linear-gradient(180deg, rgba(32, 30, 26, 0.9) 0%, rgba(18, 17, 14, 0.95) 100%)',
        'carbon-raised-gradient': 'linear-gradient(180deg, rgba(38, 35, 30, 0.9) 0%, rgba(24, 22, 19, 0.95) 100%)',
        'cool-sheen': 'linear-gradient(135deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.01) 100%)',
      },
      transitionTimingFunction: {
        'out-expo': 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        // The one authored moment: a sheet settles onto the desk.
        settle: {
          '0%': { opacity: '0', transform: 'translateY(6px) scale(0.985)' },
          '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        // Live state only: a running node breathes.
        breathe: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
        // A rule draws itself from the left, once.
        ruleDraw: {
          '0%': { transform: 'scaleX(0)' },
          '100%': { transform: 'scaleX(1)' },
        },
      },
      animation: {
        settle: 'settle 420ms cubic-bezier(0.16, 1, 0.3, 1) both',
        breathe: 'breathe 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'rule-draw': 'ruleDraw 600ms cubic-bezier(0.16, 1, 0.3, 1) both',
      },
      maxWidth: {
        // A literary measure. 68ch is the target for report prose.
        measure: '68ch',
        'measure-wide': '82ch',
      },
    },
  },
  plugins: [],
};
