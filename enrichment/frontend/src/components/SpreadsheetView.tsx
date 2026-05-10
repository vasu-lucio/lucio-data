import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import type { ColDef, GridReadyEvent, CellValueChangedEvent, GetRowIdParams } from 'ag-grid-community'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-alpine.css'
import toast from 'react-hot-toast'
import { ArrowLeft, Download, Plus, RefreshCw, Sparkles, ChevronDown } from 'lucide-react'
import { api } from '../api'
import type { Session } from '../App'
import EnrichPanel from './EnrichPanel'

type RowData = { _rowId: string; [key: string]: string }

type FullSession = Session & {
  rows: { id: string; row_index: number; data: Record<string, string> }[]
}

const STATUS_LABELS: Record<string, string> = {
  not_found: 'Not Found',
  not_sure: 'Not Sure',
  not_available: 'N/A',
}

const STATUS_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  not_found:     { bg: '#FEE2E2', text: '#DC2626', border: '#FECACA' },
  not_sure:      { bg: '#FEF3C7', text: '#D97706', border: '#FDE68A' },
  not_available: { bg: '#F5F5F4', text: '#78716C', border: '#E7E5E4' },
}

function statusCellRenderer(params: { value: string }) {
  const v = params.value ?? ''
  const label = STATUS_LABELS[v]
  if (!label) return <span>{v}</span>
  const c = STATUS_COLORS[v] ?? STATUS_COLORS.not_available
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '1px 7px',
        borderRadius: '8px',
        fontSize: '11px',
        fontWeight: 600,
        background: c.bg,
        color: c.text,
        border: `1px solid ${c.border}`,
      }}
    >
      {label}
    </span>
  )
}

export default function SpreadsheetView({
  session,
  onBack,
  onSessionUpdate,
}: {
  session: Session
  onBack: () => void
  onSessionUpdate: (s: Session) => void
}) {
  const gridRef = useRef<AgGridReact>(null)
  const [fullSession, setFullSession] = useState<FullSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [showEnrich, setShowEnrich] = useState(false)
  const [showExport, setShowExport] = useState(false)
  const [saving, setSaving] = useState(false)

  async function loadSession() {
    setLoading(true)
    try {
      const res = await api.getSession(session.id)
      setFullSession(res.data)
      onSessionUpdate(res.data)
    } catch {
      toast.error('Failed to load session')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadSession() }, [session.id])

  const rowData: RowData[] = useMemo(() => {
    if (!fullSession) return []
    return fullSession.rows.map((r) => ({ _rowId: r.id, ...r.data }))
  }, [fullSession])

  const colDefs: ColDef[] = useMemo(() => {
    if (!fullSession) return []
    return fullSession.columns.map((col, i) => ({
      field: col,
      headerName: col,
      editable: true,
      resizable: true,
      sortable: true,
      filter: true,
      minWidth: 120,
      flex: i === 0 ? 2 : 1,
      cellRenderer: (params: { value: string }) => {
        const v = params.value ?? ''
        if (STATUS_LABELS[v]) return statusCellRenderer(params)
        // Hyperlink detection
        if (v.startsWith('http://') || v.startsWith('https://')) {
          return (
            <a
              href={v}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              style={{ color: '#4F46E5', textDecoration: 'underline' }}
            >
              {v}
            </a>
          )
        }
        return <span>{v}</span>
      },
    }))
  }, [fullSession])

  const defaultColDef: ColDef = useMemo(() => ({
    suppressMovable: false,
    wrapText: false,
    autoHeight: false,
  }), [])

  const getRowId = useCallback((params: GetRowIdParams) => params.data._rowId, [])

  const onCellValueChanged = useCallback(
    async (e: CellValueChangedEvent) => {
      const rowId: string = e.data._rowId
      const col: string = e.colDef.field!
      const value: string = String(e.newValue ?? '')
      setSaving(true)
      try {
        await api.updateCell(session.id, rowId, col, value)
      } catch {
        toast.error('Failed to save cell')
      } finally {
        setSaving(false)
      }
    },
    [session.id],
  )

  const onGridReady = useCallback((_: GridReadyEvent) => {
    // auto-size columns after data loads
  }, [])

  async function handleAddColumn() {
    const name = prompt('New column name:')?.trim()
    if (!name) return
    try {
      await api.addColumns(session.id, [name])
      await loadSession()
      toast.success(`Column "${name}" added`)
    } catch {
      toast.error('Failed to add column')
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-surface-0 flex items-center justify-center">
        <div className="text-text-muted text-sm">Loading session…</div>
      </div>
    )
  }

  if (!fullSession) return null

  return (
    <div className="h-screen bg-surface-0 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 border-b border-surface-border bg-surface-1 px-4 py-3 flex items-center gap-3 shadow-card">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-text-muted hover:text-text-secondary text-sm px-2 py-1.5 rounded-lg hover:bg-surface-2 transition-colors"
        >
          <ArrowLeft size={14} />
          Back
        </button>

        <div className="h-4 w-px bg-surface-border" />

        <div className="flex-1 min-w-0">
          <span className="text-text-primary text-sm font-semibold truncate">{fullSession.name}</span>
          <span className="text-text-muted text-xs ml-2">
            {fullSession.row_count} rows · {fullSession.columns.length} cols
            {saving && <span className="ml-2 text-accent">saving…</span>}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleAddColumn}
            className="flex items-center gap-1.5 text-text-secondary hover:text-text-primary text-xs px-3 py-1.5 rounded-lg border border-surface-border hover:border-accent/30 bg-surface-0 transition-colors"
          >
            <Plus size={12} />
            Add column
          </button>

          <button
            onClick={loadSession}
            className="flex items-center gap-1.5 text-text-secondary hover:text-text-primary text-xs px-3 py-1.5 rounded-lg border border-surface-border hover:border-accent/30 bg-surface-0 transition-colors"
            title="Refresh"
          >
            <RefreshCw size={12} />
          </button>

          {/* Export dropdown */}
          <div className="relative">
            <button
              onClick={() => setShowExport(!showExport)}
              className="flex items-center gap-1.5 text-text-secondary hover:text-text-primary text-xs px-3 py-1.5 rounded-lg border border-surface-border hover:border-accent/30 bg-surface-0 transition-colors"
            >
              <Download size={12} />
              Export
              <ChevronDown size={10} />
            </button>
            {showExport && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowExport(false)} />
                <div className="absolute right-0 top-full mt-1 bg-surface-1 border border-surface-border rounded-lg shadow-panel z-20 overflow-hidden min-w-[120px]">
                  {(['csv', 'xlsx'] as const).map((fmt) => (
                    <button
                      key={fmt}
                      onClick={() => { api.exportSession(session.id, fmt); setShowExport(false) }}
                      className="w-full text-left px-4 py-2 text-sm text-text-secondary hover:bg-surface-2 hover:text-text-primary transition-colors"
                    >
                      {fmt.toUpperCase()}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          <button
            onClick={() => setShowEnrich(true)}
            className="flex items-center gap-1.5 bg-accent hover:bg-accent-hover text-white text-xs font-semibold px-4 py-1.5 rounded-lg transition-colors shadow-sm"
          >
            <Sparkles size={13} />
            Enrich
          </button>
        </div>
      </div>

      {/* Grid */}
      <div className="flex-1 ag-theme-alpine overflow-hidden">
        <AgGridReact
          ref={gridRef}
          rowData={rowData}
          columnDefs={colDefs}
          defaultColDef={defaultColDef}
          getRowId={getRowId}
          onCellValueChanged={onCellValueChanged}
          onGridReady={onGridReady}
          animateRows={false}
          suppressRowClickSelection
          enableCellTextSelection
          stopEditingWhenCellsLoseFocus
          undoRedoCellEditing
          undoRedoCellEditingLimit={20}
          rowBuffer={20}
          suppressColumnVirtualisation={false}
          suppressMenuHide={false}
        />
      </div>

      {/* Enrich panel */}
      {showEnrich && (
        <EnrichPanel
          session={fullSession}
          onClose={() => setShowEnrich(false)}
          onDone={() => { setShowEnrich(false); loadSession() }}
        />
      )}
    </div>
  )
}
