import React from 'react'
import ReactDOM from 'react-dom/client'
import { Toaster } from 'react-hot-toast'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
    <Toaster
      position="bottom-right"
      toastOptions={{
        style: {
          background: '#1E2130',
          color: '#E5E7EB',
          border: '1px solid #2D3148',
          borderRadius: '8px',
          fontSize: '14px',
        },
        success: { iconTheme: { primary: '#10B981', secondary: '#1E2130' } },
        error: { iconTheme: { primary: '#EF4444', secondary: '#1E2130' } },
      }}
    />
  </React.StrictMode>,
)
