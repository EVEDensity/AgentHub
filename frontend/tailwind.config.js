module.exports = {
  content: ['./pages/**/*.{js,jsx}', './components/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        warm: {
          50: '#F8F7F4',
          100: '#F0EFEA',
          150: '#E8E7E2',
          200: '#E0DFDA',
          300: '#C8C7C2',
          400: '#A8A7A3',
          500: '#888783',
          600: '#686764',
          700: '#484744',
          800: '#2E2E2B',
          900: '#1A1A18',
        },
        primary: {
          50: '#F0F3FF',
          100: '#E0E7FF',
          200: '#C7D7FE',
          500: '#4F6CF7',
          600: '#4361E2',
          700: '#3651CC',
        },
        success: {
          50: '#F0F7F0',
          500: '#5B8C5A',
          600: '#4D7A4C',
        },
        danger: {
          50: '#FBF0EE',
          500: '#C4675A',
          600: '#B05548',
        },
        warning: {
          50: '#FBF7EE',
          500: '#C4A35A',
          600: '#B09248',
        },
      },
      borderRadius: {
        sm: '6px',
        md: '8px',
        lg: '12px',
        xl: '16px',
        '2xl': '20px',
      },
      boxShadow: {
        'warm-sm': '0 1px 2px rgba(30, 30, 29, 0.04), 0 1px 3px rgba(30, 30, 29, 0.03)',
        'warm-md': '0 2px 4px rgba(30, 30, 29, 0.04), 0 4px 8px rgba(30, 30, 29, 0.03)',
        'warm-lg': '0 4px 12px rgba(30, 30, 29, 0.05), 0 8px 24px rgba(30, 30, 29, 0.04)',
        'warm-xl': '0 8px 24px rgba(30, 30, 29, 0.06), 0 16px 48px rgba(30, 30, 29, 0.05)',
      },
      fontFamily: {
        sans: [
          '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto',
          '"Helvetica Neue"', 'Arial', '"Noto Sans SC"', 'sans-serif',
          '"Apple Color Emoji"', '"Segoe UI Emoji"',
        ],
      },
    },
  },
  plugins: [],
};