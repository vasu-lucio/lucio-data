/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          0: '#F7F6F2',
          1: '#FFFFFF',
          2: '#F3F1EC',
          3: '#EAE8E3',
          border: '#E4E1D9',
        },
        accent: {
          DEFAULT: '#4F46E5',
          hover: '#4338CA',
          muted: '#818CF8',
        },
        text: {
          primary: '#1C1916',
          secondary: '#57534E',
          muted: '#9C9A96',
        },
        status: {
          found: '#16A34A',
          'not-found': '#DC2626',
          'not-sure': '#D97706',
          'not-available': '#78716C',
        },
      },
      fontFamily: {
        sans: ['DM Sans', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 3px 0 rgba(0,0,0,0.07), 0 1px 2px -1px rgba(0,0,0,0.05)',
        panel: '0 4px 24px -4px rgba(0,0,0,0.10), 0 2px 8px -2px rgba(0,0,0,0.06)',
      },
    },
  },
  plugins: [],
}
