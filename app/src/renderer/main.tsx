import { createRoot } from 'react-dom/client'
import { App } from './App'
import { installAppearance } from './platform'
import '@xterm/xterm/css/xterm.css'
import './styles.css'

installAppearance()
createRoot(document.getElementById('root')!).render(<App />)
