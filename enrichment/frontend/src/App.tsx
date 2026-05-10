import { useEffect, useState } from 'react'
import LoginScreen from './components/LoginScreen'
import SessionList from './components/SessionList'
import SpreadsheetView from './components/SpreadsheetView'

export type Session = {
  id: string
  name: string
  columns: string[]
  row_count: number
  created_at: string
  updated_at: string
}

export default function App() {
  const [authed, setAuthed] = useState(false)
  const [activeSession, setActiveSession] = useState<Session | null>(null)

  useEffect(() => {
    const pwd = sessionStorage.getItem('app_password')
    if (pwd) setAuthed(true)
  }, [])

  if (!authed) {
    return <LoginScreen onAuth={() => setAuthed(true)} />
  }

  if (activeSession) {
    return (
      <SpreadsheetView
        session={activeSession}
        onBack={() => setActiveSession(null)}
        onSessionUpdate={(s) => setActiveSession(s)}
      />
    )
  }

  return <SessionList onOpen={(s) => setActiveSession(s)} />
}
