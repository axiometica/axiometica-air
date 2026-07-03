/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      // Professional enterprise color palette
      colors: {
        // Custom base dark theme (extends standard Tailwind slate)
        slate: {
          950: '#0f1419',
          900: '#1a1f2e',
          875: '#252c3c',  // Changed from 850 to 875 (between 900 and 800)
          800: '#2d3748',
          700: '#3d4557',
        },
        // Text colors
        text: {
          primary: '#e8eef5',
          secondary: '#a0aec0',
          tertiary: '#7a8ba3',
        },
        // Semantic colors - muted enterprise palette
        critical: {
          50: '#f9f0f0',
          100: '#f0d8d8',
          500: '#a04848',
          700: '#7a3030',
          900: '#3d1818',
        },
        success: {
          50: '#f0f6f3',
          100: '#d4e9df',
          500: '#3a7a5a',
          700: '#285545',
          900: '#152e25',
        },
        warning: {
          50: '#f7f3ec',
          100: '#ecdfc8',
          500: '#9a7030',
          700: '#734f1a',
          900: '#3d2a0e',
        },
        info: {
          50: '#eef3f8',
          100: '#ccdaeb',
          500: '#4070a0',
          700: '#2a5080',
          900: '#152840',
        },
        approval: {
          50: '#f3f0f8',
          100: '#dcd4ee',
          500: '#6a4a8a',
          700: '#4e3268',
          900: '#281a38',
        },

        // Legacy incident/change colors for backward compatibility
        incident: {
          50: '#f9f0f0',
          500: '#a04848',
          700: '#7a3030',
        },
        change: {
          50: '#f0f6f3',
          500: '#3a7a5a',
          700: '#285545',
        },
        status: {
          open: '#4070a0',
          'in-progress': '#9a7030',
          waiting: '#6a4a8a',
          resolved: '#3a7a5a',
          failed: '#a04848',
          deployed: '#3a6a7a',
          'approval-pending': '#9a7030',
          'remediation-attempting': '#4070a0',
          'remediation-successful': '#3a7a5a',
          'remediation-failed': '#a04848',
          closed: '#5a6070',
        },
      },

      // Enhanced typography
      fontSize: {
        'page-title': ['32px', { fontWeight: '700', letterSpacing: '-0.5px' }],
        'section-title': ['24px', { fontWeight: '600', letterSpacing: '-0.25px' }],
        'subsection-title': ['18px', { fontWeight: '600', letterSpacing: '0' }],
        'body': ['14px', { fontWeight: '400', lineHeight: '1.6' }],
        'label': ['12px', { fontWeight: '500', letterSpacing: '0.5px', textTransform: 'uppercase' }],
      },

      // Enhanced spacing
      spacing: {
        '4.5': '1.125rem',
        '5.5': '1.375rem',
        '6.5': '1.625rem',
      },

      // Custom animations
      animation: {
        'count-up': 'countUp 1s ease-out forwards',
        'metric-pulse': 'metricPulse 2s ease-in-out infinite',
        'gradient-flow': 'gradientFlow 3s ease-in-out infinite',
        'slide-in-right': 'slideInRight 300ms cubic-bezier(0.4, 0, 0.2, 1) forwards',
        'fade-in-scale': 'fadeInScale 250ms cubic-bezier(0.4, 0, 0.2, 1) forwards',
        'wave-shimmer': 'waveShimmer 2s infinite',
        'bounce-gentle': 'bounceGentle 600ms cubic-bezier(0.68, -0.55, 0.265, 1.55)',
        'checkmark-draw': 'checkmarkDraw 400ms cubic-bezier(0.65, 0, 0.35, 1)',
        'stagger-children': 'none', // Used with delay utilities
      },

      // Custom keyframes
      keyframes: {
        countUp: {
          from: { opacity: '0.5' },
          to: { opacity: '1' },
        },
        metricPulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.8' },
        },
        gradientFlow: {
          '0%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
          '100%': { backgroundPosition: '0% 50%' },
        },
        slideInRight: {
          from: { transform: 'translateX(100%)', opacity: '0' },
          to: { transform: 'translateX(0)', opacity: '1' },
        },
        fadeInScale: {
          from: { transform: 'scale(0.95)', opacity: '0' },
          to: { transform: 'scale(1)', opacity: '1' },
        },
        waveShimmer: {
          '0%': { backgroundPosition: '-1000px 0' },
          '100%': { backgroundPosition: '1000px 0' },
        },
        bounceGentle: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-8px)' },
        },
        checkmarkDraw: {
          '0%': { strokeDashoffset: '20' },
          '100%': { strokeDashoffset: '0' },
        },
      },

      // Text color utilities
      textColor: {
        primary: '#e8eef5',
        secondary: '#a0aec0',
        tertiary: '#7a8ba3',
      },

      // Background color utilities
      backgroundColor: {
        primary: '#e8eef5',
        secondary: '#a0aec0',
        tertiary: '#7a8ba3',
      },

      // Enhanced transitions
      transitionDuration: {
        '250': '250ms',
        '350': '350ms',
      },
      transitionTimingFunction: {
        'smooth': 'cubic-bezier(0.4, 0, 0.2, 1)',
        'bounce-in': 'cubic-bezier(0.68, -0.55, 0.265, 1.55)',
      },

      // Shadows for depth
      boxShadow: {
        'sm-dark': '0 1px 2px rgba(0, 0, 0, 0.1)',
        'md-dark': '0 4px 12px rgba(0, 0, 0, 0.15)',
        'lg-dark': '0 12px 24px rgba(0, 0, 0, 0.2)',
        'xl-dark': '0 20px 40px rgba(0, 0, 0, 0.3)',
        'glow-blue': '0 0 10px rgba(64, 112, 160, 0.25)',
        'glow-green': '0 0 10px rgba(58, 122, 90, 0.25)',
        'glow-red': '0 0 10px rgba(160, 72, 72, 0.25)',
        'glow-purple': '0 0 10px rgba(106, 74, 138, 0.25)',
      },

      // Backdrop blur
      backdropBlur: {
        'sm': '4px',
        'md': '8px',
        'lg': '12px',
        'xl': '16px',
      },
    },
  },
  plugins: [],
}
