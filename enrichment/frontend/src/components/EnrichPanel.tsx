import { useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { X, Plus, Trash2, Sparkles, Save, BookOpen, ChevronDown, AlertCircle } from 'lucide-react'
import { api } from '../api'
import type { Session } from '../App'

type DataPoint = {
  id: string
  column: string
  prompt: string
  preset: string | null
}

type FullSession = Session & {
  rows: { id: string; row_index: number; data: Record<string, string> }[]
}

type JobStatus = {
  id: string
  status: 'pending' | 'running' | 'done' | 'failed'
  progress_done: number
  progress_total: number
  error: string | null
}

const PRESETS = [
  { label: 'COO Name',               preset: 'coo_name',       column: 'COO Name' },
  { label: 'COO Email',              preset: 'coo_email',       column: 'COO Email' },
  { label: 'COO Phone',              preset: 'coo_phone',       column: 'COO Phone' },
  { label: 'COO LinkedIn',           preset: 'coo_linkedin',    column: 'COO LinkedIn' },
  { label: 'Managing Partner Name',  preset: 'mp_name',         column: 'Managing Partner Name' },
  { label: 'Managing Partner Email', preset: 'mp_email',        column: 'Managing Partner Email' },
  { label: 'Number of Attorneys',    preset: 'attorney_count',  column: 'Attorney Count' },
  { label: 'Firm Website',           preset: 'website',         column: 'Website' },
  { label: 'Office Locations',       preset: 'offices',         column: 'Offices' },
  { label: 'Practice Areas',         preset: 'practice_areas',  column: 'Practice Areas' },
]

const NOT_FOUND_VALUES = new Set(['not_found', 'not_sure'])

function uid() {
  return Math.random().toString(36).slice(2)
}

export default function EnrichPanel({
  session,
  onClose,
  onDone,
}: {
  session: FullSession
  onClose: () => void
  onDone: () => void
}) {
  const [dataPoints, setDataPoints] = useState<DataPoint[]>([
    { id: uid(), column: '', prompt: '', preset: null },
  ])
  const [scope, setScope] = useState<'all' | 'failed'>('all')
  const [dryRun, setDryRun] = useState(false)
  const [running, setRunning] = useState(false)
  const [dryResult, setDryResult] = useState<null | { rows: number; data_points: number; estimated_cost_usd: number; estimated_time_min: number }>(null)
  const [job, setJob] = useState<JobStatus | null>(null)
  const [templates, setTemplates] = useState<{ id: string; name: string; config: any }[]>([])
  const [showTemplates, setShowTemplates] = useState(false)
  const [showSaveTemplate, setShowSaveTemplate] = useState(false)
  const [templateName, setTemplateName] = useState('')
  const [showPresets, setShowPresets] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    api.listTemplates().then((r) => setTemplates(r.data)).catch(() => {})
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  // Rows that are candidates for "re-run failed" scope
  const failedRowIds = (() => {
    const targetCols = dataPoints.map((p) => p.column).filter(Boolean)
    if (targetCols.length === 0) return []
    return session.rows
      .filter((r) => targetCols.some((col) => NOT_FOUND_VALUES.has(r.data[col] ?? '')))
      .map((r) => r.id)
  })()

  const targetCount = scope === 'failed' ? failedRowIds.length : session.row_count

  function addPoint() {
    if (dataPoints.length >= 6) { toast.error('Maximum 6 data points per run'); return }
    setDataPoints([...dataPoints, { id: uid(), column: '', prompt: '', preset: null }])
  }

  function removePoint(id: string) {
    setDataPoints(dataPoints.filter((p) => p.id !== id))
  }

  function updatePoint(id: string, field: keyof DataPoint, value: string | null) {
    setDataPoints(dataPoints.map((p) => (p.id === id ? { ...p, [field]: value } : p)))
  }

  function applyPreset(dpId: string, preset: (typeof PRESETS)[0]) {
    setDataPoints(dataPoints.map((p) =>
      p.id === dpId ? { ...p, column: preset.column, preset: preset.preset, prompt: '' } : p,
    ))
    setShowPresets(null)
  }

  function startPolling(jobId: string) {
    pollRef.current = setInterval(async () => {
      try {
        const res = await api.getJob(jobId)
        const j: JobStatus = res.data
        setJob(j)
        if (j.status === 'done') {
          clearInterval(pollRef.current!)
          setRunning(false)
          toast.success(`Enriched ${j.progress_total} rows`)
          onDone()
        } else if (j.status === 'failed') {
          clearInterval(pollRef.current!)
          setRunning(false)
          toast.error(`Enrichment failed: ${j.error || 'unknown error'}`)
        }
      } catch {
        // transient error — keep polling
      }
    }, 2000)
  }

  async function handleRun() {
    const valid = dataPoints.filter((p) => p.column.trim() && (p.prompt.trim() || p.preset))
    if (valid.length === 0) {
      toast.error('Add at least one data point with a column name and description')
      return
    }

    setDryResult(null)
    setRunning(true)

    const row_ids = scope === 'failed' ? failedRowIds : []
    const payload = {
      row_ids,
      data_points: valid.map(({ column, prompt, preset }) => ({ column, prompt, preset })),
      dry_run: dryRun,
    }

    try {
      const res = await api.enrich(session.id, payload)

      if (dryRun) {
        setDryResult(res.data)
        setRunning(false)
      } else {
        const { job_id } = res.data
        setJob({ id: job_id, status: 'pending', progress_done: 0, progress_total: targetCount, error: null })
        startPolling(job_id)
      }
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to start enrichment')
      setRunning(false)
    }
  }

  async function saveTemplate() {
    if (!templateName.trim()) return
    const config = { dataPoints: dataPoints.map(({ id: _, ...p }) => p) }
    try {
      await api.saveTemplate(templateName.trim(), config)
      const res = await api.listTemplates()
      setTemplates(res.data)
      setShowSaveTemplate(false)
      setTemplateName('')
      toast.success('Template saved')
    } catch { toast.error('Failed to save template') }
  }

  function loadTemplate(config: { dataPoints: Omit<DataPoint, 'id'>[] }) {
    setDataPoints(config.dataPoints.map((p) => ({ ...p, id: uid() })))
    setShowTemplates(false)
    toast.success('Template loaded')
  }

  const isRunning = running && !dryRun
  const pct = job ? Math.round((job.progress_done / Math.max(job.progress_total, 1)) * 100) : 0

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={isRunning ? undefined : onClose} />
      <div className="relative w-[480px] h-full bg-surface-1 border-l border-surface-border flex flex-col shadow-panel">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-surface-border">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-accent" />
            <span className="text-text-primary font-semibold text-sm">Enrich Data</span>
          </div>
          <button
            onClick={isRunning ? undefined : onClose}
            disabled={isRunning}
            className="text-text-muted hover:text-text-secondary p-1 rounded transition-colors disabled:opacity-30"
          >
            <X size={16} />
          </button>
        </div>

        {/* Progress overlay while job is running */}
        {isRunning && job && (
          <div className="flex-shrink-0 bg-accent/5 border-b border-accent/15 px-5 py-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-text-primary">
                {job.status === 'pending' ? 'Starting…' : `Enriching ${job.progress_done} / ${job.progress_total} rows`}
              </span>
              <span className="text-xs text-text-muted">{pct}%</span>
            </div>
            <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden">
              <div
                className="h-full bg-accent rounded-full transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
            <p className="text-xs text-text-muted mt-2">
              Running in background — you can navigate away and come back
            </p>
          </div>
        )}

        {/* Dry run result */}
        {dryResult && (
          <div className="flex-shrink-0 bg-surface-2 border-b border-surface-border px-5 py-4 space-y-1">
            <p className="text-sm font-semibold text-text-primary">Dry Run Estimate</p>
            <div className="grid grid-cols-2 gap-2 mt-2">
              {[
                ['Rows', dryResult.rows],
                ['Data points', dryResult.data_points],
                ['Est. cost', `$${dryResult.estimated_cost_usd}`],
                ['Est. time', `${dryResult.estimated_time_min} min`],
              ].map(([label, val]) => (
                <div key={label as string} className="bg-surface-1 border border-surface-border rounded-lg px-3 py-2 shadow-card">
                  <p className="text-xs text-text-muted">{label}</p>
                  <p className="text-sm font-semibold text-text-primary">{val}</p>
                </div>
              ))}
            </div>
            <button
              onClick={() => { setDryRun(false); setDryResult(null) }}
              className="mt-2 text-xs text-accent hover:text-accent-hover transition-colors font-medium"
            >
              Run for real →
            </button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-5 space-y-5">

          {/* Templates */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-text-muted text-xs font-semibold uppercase tracking-wider">Templates</label>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowSaveTemplate(!showSaveTemplate)}
                  className="flex items-center gap-1 text-xs text-text-muted hover:text-text-secondary transition-colors"
                >
                  <Save size={11} /> Save current
                </button>
                <button
                  onClick={() => setShowTemplates(!showTemplates)}
                  className="flex items-center gap-1 text-xs text-accent hover:text-accent-hover transition-colors font-medium"
                >
                  <BookOpen size={11} /> Load <ChevronDown size={10} />
                </button>
              </div>
            </div>

            {showSaveTemplate && (
              <div className="flex gap-2 mb-3">
                <input
                  autoFocus
                  value={templateName}
                  onChange={(e) => setTemplateName(e.target.value)}
                  placeholder="Template name…"
                  className="flex-1 bg-surface-2 border border-surface-border rounded-lg px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20"
                  onKeyDown={(e) => e.key === 'Enter' && saveTemplate()}
                />
                <button onClick={saveTemplate} className="bg-accent hover:bg-accent-hover text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors shadow-sm">
                  Save
                </button>
              </div>
            )}

            {showTemplates && (
              <div className="bg-surface-1 border border-surface-border rounded-lg overflow-hidden mb-3 shadow-card">
                {templates.length === 0
                  ? <p className="text-text-muted text-xs px-3 py-2">No saved templates yet</p>
                  : templates.map((t) => (
                      <button
                        key={t.id}
                        onClick={() => loadTemplate(t.config)}
                        className="w-full text-left px-3 py-2 text-sm text-text-secondary hover:bg-surface-2 hover:text-text-primary transition-colors border-b border-surface-border last:border-0"
                      >
                        {t.name}
                      </button>
                    ))}
              </div>
            )}
          </div>

          {/* Data points */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <label className="text-text-muted text-xs font-semibold uppercase tracking-wider">
                Data Points ({dataPoints.length}/6)
              </label>
            </div>

            <div className="space-y-3">
              {dataPoints.map((dp, i) => (
                <div key={dp.id} className="bg-surface-2 border border-surface-border rounded-xl p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-text-muted text-xs font-medium">Point {i + 1}</span>
                    {dataPoints.length > 1 && (
                      <button onClick={() => removePoint(dp.id)} className="text-text-muted hover:text-status-not-found p-1 rounded transition-colors">
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>

                  <div>
                    <label className="text-text-muted text-xs mb-1 block">Output column name</label>
                    <div className="flex gap-2">
                      <input
                        value={dp.column}
                        onChange={(e) => updatePoint(dp.id, 'column', e.target.value)}
                        placeholder="e.g. COO Email"
                        className="flex-1 bg-surface-1 border border-surface-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20 transition-colors"
                      />
                      <div className="relative">
                        <button
                          onClick={() => setShowPresets(showPresets === dp.id ? null : dp.id)}
                          className="flex items-center gap-1 bg-surface-1 border border-surface-border hover:border-accent/30 text-text-muted hover:text-text-secondary text-xs px-2.5 py-2 rounded-lg transition-colors whitespace-nowrap"
                        >
                          Presets <ChevronDown size={10} />
                        </button>
                        {showPresets === dp.id && (
                          <>
                            <div className="fixed inset-0 z-10" onClick={() => setShowPresets(null)} />
                            <div className="absolute right-0 top-full mt-1 bg-surface-1 border border-surface-border rounded-lg shadow-panel z-20 overflow-hidden w-52">
                              {PRESETS.map((p) => (
                                <button
                                  key={p.preset}
                                  onClick={() => applyPreset(dp.id, p)}
                                  className="w-full text-left px-3 py-2 text-xs text-text-secondary hover:bg-surface-2 hover:text-text-primary transition-colors border-b border-surface-border last:border-0"
                                >
                                  {p.label}
                                </button>
                              ))}
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  </div>

                  {!dp.preset && (
                    <div>
                      <label className="text-text-muted text-xs mb-1 block">What to look for</label>
                      <textarea
                        value={dp.prompt}
                        onChange={(e) => updatePoint(dp.id, 'prompt', e.target.value)}
                        placeholder="e.g. Find the name of the Chief Operating Officer or Firm Administrator"
                        rows={2}
                        className="w-full bg-surface-1 border border-surface-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20 resize-none transition-colors"
                      />
                    </div>
                  )}

                  {dp.preset && (
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-accent font-medium">Preset: {PRESETS.find((p) => p.preset === dp.preset)?.label}</span>
                      <button onClick={() => updatePoint(dp.id, 'preset', null)} className="text-xs text-text-muted hover:text-text-secondary transition-colors">
                        Use custom prompt
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>

            <button
              onClick={addPoint}
              disabled={dataPoints.length >= 6}
              className="mt-3 w-full flex items-center justify-center gap-2 border border-dashed border-surface-border hover:border-accent/40 text-text-muted hover:text-text-secondary rounded-xl py-2.5 text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Plus size={14} /> Add data point
            </button>
          </div>

          {/* Scope + options */}
          <div className="space-y-3">
            <label className="text-text-muted text-xs font-semibold uppercase tracking-wider block">Scope</label>

            <div className="grid grid-cols-2 gap-2">
              {[
                { value: 'all',    label: 'All rows',          count: session.row_count },
                { value: 'failed', label: 'Re-run not found',  count: failedRowIds.length },
              ].map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setScope(opt.value as 'all' | 'failed')}
                  className={`flex flex-col items-start px-3 py-2.5 rounded-lg border text-left transition-all ${
                    scope === opt.value
                      ? 'border-accent bg-accent/8 text-text-primary shadow-sm'
                      : 'border-surface-border bg-surface-2 text-text-secondary hover:border-accent/30'
                  }`}
                >
                  <span className="text-xs font-semibold">{opt.label}</span>
                  <span className="text-xs text-text-muted mt-0.5">{opt.count} rows</span>
                </button>
              ))}
            </div>

            {scope === 'failed' && failedRowIds.length === 0 && (
              <p className="text-xs text-status-not-sure flex items-center gap-1.5">
                <AlertCircle size={12} /> No "Not Found" or "Not Sure" rows for the selected columns
              </p>
            )}

            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(e) => { setDryRun(e.target.checked); setDryResult(null) }}
                className="w-4 h-4 accent-[#4F46E5] rounded"
              />
              <div>
                <span className="text-sm text-text-primary">Dry run</span>
                <p className="text-xs text-text-muted">Show cost + time estimate without running</p>
              </div>
            </label>
          </div>
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 border-t border-surface-border px-5 py-4 bg-surface-1">
          {!isRunning && (
            <div className="flex items-center gap-3">
              <button onClick={onClose} className="flex-1 text-sm text-text-secondary border border-surface-border hover:border-accent/30 rounded-lg py-2.5 transition-colors hover:bg-surface-2">
                Cancel
              </button>
              <button
                onClick={handleRun}
                disabled={running || (scope === 'failed' && failedRowIds.length === 0)}
                className="flex-1 flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg py-2.5 transition-colors shadow-sm"
              >
                <Sparkles size={14} />
                {dryRun ? 'Preview estimate' : `Enrich ${targetCount} rows`}
              </button>
            </div>
          )}
          {isRunning && (
            <p className="text-center text-xs text-text-muted">
              Enrichment running… panel will close when complete
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
