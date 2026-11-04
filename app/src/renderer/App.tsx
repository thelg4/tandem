import { useEffect, useState } from 'react'
import type { WatcherMsg } from '../shared/protocol'

export function App(): JSX.Element {
  const [msgs, setMsgs] = useState<WatcherMsg[]>([])
  useEffect(() => {
    const off = window.tandem.onWatcher((m) => setMsgs((prev) => [...prev.slice(-200), m]))
    window.tandem.ready()
    return off
  }, [])
  return <pre>{msgs.map((m) => m.type).join('\n')}</pre>
}
