import axios from 'axios'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '') + '/api'

function headers() {
  const pwd = sessionStorage.getItem('app_password') || ''
  return { 'x-password': pwd }
}

export const api = {
  verifyAuth: (password: string) =>
    axios.post(`${BASE}/auth/verify`, { password }),

  listSessions: () =>
    axios.get(`${BASE}/sessions`, { headers: headers() }),

  createSession: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return axios.post(`${BASE}/sessions`, form, { headers: headers() })
  },

  getSession: (id: string) =>
    axios.get(`${BASE}/sessions/${id}`, { headers: headers() }),

  updateCell: (sessionId: string, rowId: string, column: string, value: string) =>
    axios.patch(`${BASE}/sessions/${sessionId}/cells`, { row_id: rowId, column, value }, { headers: headers() }),

  addColumns: (sessionId: string, columns: string[]) =>
    axios.post(`${BASE}/sessions/${sessionId}/columns`, { columns }, { headers: headers() }),

  deleteSession: (id: string) =>
    axios.delete(`${BASE}/sessions/${id}`, { headers: headers() }),

  exportSession: (id: string, fmt: 'csv' | 'xlsx') => {
    const pwd = sessionStorage.getItem('app_password') || ''
    window.open(`${BASE}/sessions/${id}/export?fmt=${fmt}&x_password=${encodeURIComponent(pwd)}`, '_blank')
  },

  enrich: (sessionId: string, payload: object) =>
    axios.post(`${BASE}/sessions/${sessionId}/enrich`, payload, { headers: headers() }),

  getJob: (jobId: string) =>
    axios.get(`${BASE}/jobs/${jobId}`, { headers: headers() }),

  listTemplates: () =>
    axios.get(`${BASE}/templates`, { headers: headers() }),

  saveTemplate: (name: string, config: object) =>
    axios.post(`${BASE}/templates`, { name, config }, { headers: headers() }),

  deleteTemplate: (id: string) =>
    axios.delete(`${BASE}/templates/${id}`, { headers: headers() }),
}
