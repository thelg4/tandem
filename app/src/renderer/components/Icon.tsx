// A few line icons drawn to sit beside 13 px system text, in the spirit of SF Symbols.
const PATHS = {
  xmark: 'M4 4l8 8M12 4l-8 8',
  warning: 'M8 2.2l6.2 11H1.8L8 2.2zM8 6.5v3.2M8 11.6v.1',
  info: 'M8 1.8a6.2 6.2 0 1 1 0 12.4A6.2 6.2 0 0 1 8 1.8zM8 7.2v4.2M8 4.9v.1',
  calm: 'M1.5 9.5c2-2.6 4.5-2.6 6.5 0s4.5 2.6 6.5 0M1.5 5.5c2-2.6 4.5-2.6 6.5 0s4.5 2.6 6.5 0',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 14 }: { name: IconName; size?: number }): JSX.Element {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <path d={PATHS[name]} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
