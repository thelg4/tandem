/** "just now", "5 min ago", "3 h ago", "Yesterday", "4 days ago", then a date. */
export function relativeTime(iso: string | null, now: number): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, (now - t) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  const days = Math.floor(s / 86400)
  if (days === 1) return 'Yesterday'
  if (days < 7) return `${days} days ago`
  return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** Time until an ISO instant: "in 2 h 5 min", "in 3 days". */
export function untilTime(iso: string | null, now: number): string {
  if (!iso) return ''
  const s = (Date.parse(iso) - now) / 1000
  if (Number.isNaN(s) || s <= 0) return ''
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))} min`
  if (s < 86400) {
    const h = Math.floor(s / 3600)
    const m = Math.round((s % 3600) / 60)
    return m ? `in ${h} h ${m} min` : `in ${h} h`
  }
  return `in ${Math.round(s / 86400)} days`
}

/** Home directory shown as ~. */
export function tidyPath(path: string, home: string): string {
  if (home && (path === home || path.startsWith(`${home}/`))) return `~${path.slice(home.length)}`
  return path
}

/** 950, 12.4K, 3.1M, 1.2B. */
export function compact(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e4) return `${Math.round(n / 1e3)}K`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return String(n)
}
