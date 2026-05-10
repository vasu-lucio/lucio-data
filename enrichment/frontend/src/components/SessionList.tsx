import { useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { Upload, Trash2, Table2, Plus, LogOut } from 'lucide-react'
import { api } from '../api'
import type { Session } from '../App'

export default function SessionList({ onOpen }: { onOpen: (s: Session) => void }) {
  const [sessions, setSessions] = useState<Session[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  async function load() {
    try {
      const res = await api.listSessions()
      setSessions(res.data)
    } catch {
      toast.error('Failed to load sessions')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const res = await api.createSession(file)
      toast.success(`Imported ${res.data.row_count} rows`)
      await load()
      onOpen(res.data)
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Upload failed')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function handleDelete(e: React.MouseEvent, id: string, name: string) {
    e.stopPropagation()
    if (!confirm(`Delete "${name}"? This cannot be undone.`)) return
    try {
      await api.deleteSession(id)
      setSessions((prev) => prev.filter((s) => s.id !== id))
      toast.success('Session deleted')
    } catch {
      toast.error('Failed to delete session')
    }
  }

  function logout() {
    sessionStorage.removeItem('app_password')
    window.location.reload()
  }

  function fmt(iso: string) {
    return new Date(iso).toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  }

  return (
    <div className="min-h-screen bg-surface-0">
      {/* Header */}
      <div className="border-b border-surface-border bg-surface-1 shadow-card">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xl">⚖️</span>
            <div>
              <h1 className="text-base font-semibold text-text-primary tracking-tight">Lucio Enrichment</h1>
              <p className="text-xs text-text-muted">Data enrichment for law firm outreach</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.xlsx,.xls"
              className="hidden"
              onChange={handleUpload}
            />
            <button
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              className="flex items-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-50 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors shadow-sm"
            >
              <Plus size={15} />
              {uploading ? 'Importing…' : 'New Session'}
            </button>
            <button
              onClick={logout}
              className="flex items-center gap-1.5 text-text-muted hover:text-text-secondary text-sm px-3 py-2 rounded-lg hover:bg-surface-2 transition-colors"
            >
              <LogOut size={14} />
              Log out
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-6 py-8">
        {loading ? (
          <div className="text-center py-20 text-text-muted text-sm">Loading…</div>
        ) : sessions.length === 0 ? (
          /* Empty state */
          <div className="flex flex-col items-center justify-center py-24 gap-5">
            <div className="w-16 h-16 rounded-2xl bg-surface-1 border border-surface-border shadow-card flex items-center justify-center">
              <Upload size={24} className="text-text-muted" />
            </div>
            <div className="text-center">
              <p className="text-text-primary font-semibold">No sessions yet</p>
              <p className="text-text-muted text-sm mt-1">Upload a CSV or Excel file to get started</p>
            </div>
            <button
              onClick={() => fileRef.current?.click()}
              className="flex items-center gap-2 bg-accent hover:bg-accent-hover text-white text-sm font-semibold px-5 py-2.5 rounded-lg transition-colors shadow-sm"
            >
              <Upload size={15} />
              Upload file
            </button>
          </div>
        ) : (
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-text-muted text-sm font-medium">
                {sessions.length} session{sessions.length !== 1 ? 's' : ''}
              </h2>
            </div>
            <div className="grid gap-2.5">
              {sessions.map((s) => (
                <div
                  key={s.id}
                  onClick={() => onOpen(s)}
                  className="group bg-surface-1 border border-surface-border rounded-xl px-5 py-4 flex items-center gap-4 cursor-pointer hover:border-accent/30 hover:shadow-card transition-all"
                >
                  <div className="w-9 h-9 rounded-lg bg-accent/8 border border-accent/12 flex items-center justify-center flex-shrink-0">
                    <Table2 size={16} className="text-accent" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-text-primary font-medium text-sm truncate">{s.name}</p>
                    <p className="text-text-muted text-xs mt-0.5">
                      {s.row_count} rows · {s.columns.length} columns · updated {fmt(s.updated_at)}
                    </p>
                  </div>
                  <button
                    onClick={(e) => handleDelete(e, s.id, s.name)}
                    className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-status-not-found p-1.5 rounded transition-all"
                    title="Delete session"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
