// Terminal timing lives outside React state: output can arrive thousands of times a
// second, and dispatching per chunk would make typing feel worse (anti-goal 1). The
// status ticker reads these values when it renders.

export interface PaneAct {
  lastOutput: number
  bell: number
  lastInput: number
}

const acts = new Map<string, PaneAct>()

function act(paneId: string): PaneAct {
  let a = acts.get(paneId)
  if (!a) {
    a = { lastOutput: 0, bell: 0, lastInput: 0 }
    acts.set(paneId, a)
  }
  return a
}

export function noteOutput(paneId: string, now: number): void {
  act(paneId).lastOutput = now
}

export function noteBell(paneId: string, now: number): void {
  act(paneId).bell = now
}

export function noteInput(paneId: string, now: number): void {
  act(paneId).lastInput = now
}

export function getAct(paneId: string): PaneAct | undefined {
  return acts.get(paneId)
}

export function forgetPane(paneId: string): void {
  acts.delete(paneId)
}
