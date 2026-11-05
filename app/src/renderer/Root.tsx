import { useCallback, useEffect, useState } from 'react'
import { App } from './App'
import { ProjectPicker } from './components/ProjectPicker'

/** The welcome window until a repository is open; after that the workspace, with the picker as a sheet. */
export function Root(): JSX.Element {
  const [repo, setRepo] = useState<string | null | undefined>(undefined)
  const [picking, setPicking] = useState(false)

  useEffect(() => {
    void window.tandem.repo().then((r) => setRepo(r?.root ?? null))
    return window.tandem.onMenu((event) => {
      if (event.kind === 'show-projects') setPicking(true)
    })
  }, [])

  const show = useCallback(() => setPicking(true), [])
  const hide = useCallback(() => setPicking(false), [])

  if (repo === undefined) return <div className="app" />
  if (repo === null) return <ProjectPicker current={null} />
  return (
    <>
      <App onShowProjects={show} />
      {picking && <ProjectPicker current={repo} onCancel={hide} />}
    </>
  )
}
