import { useState } from 'react'
import toast from 'react-hot-toast'
import { api } from '../api'

export default function LoginScreen({ onAuth }: { onAuth: () => void }) {
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    try {
      await api.verifyAuth(password)
      sessionStorage.setItem('app_password', password)
      onAuth()
    } catch {
      toast.error('Incorrect password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-surface-0 flex items-center justify-center">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-surface-1 border border-surface-border shadow-card mb-4">
            <span className="text-2xl">⚖️</span>
          </div>
          <h1 className="text-2xl font-semibold text-text-primary tracking-tight">Lucio Enrichment</h1>
          <p className="text-text-muted text-sm mt-1.5">Enter your password to continue</p>
        </div>

        <form onSubmit={handleSubmit} className="bg-surface-1 border border-surface-border rounded-2xl p-6 space-y-4 shadow-card">
          <div>
            <label className="block text-text-secondary text-sm font-medium mb-2">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password"
              autoFocus
              className="w-full bg-surface-2 border border-surface-border rounded-lg px-4 py-2.5 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20 text-sm transition-colors"
            />
          </div>
          <button
            type="submit"
            disabled={loading || !password}
            className="w-full bg-accent hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold rounded-lg py-2.5 text-sm transition-colors shadow-sm"
          >
            {loading ? 'Verifying…' : 'Enter'}
          </button>
        </form>
      </div>
    </div>
  )
}
