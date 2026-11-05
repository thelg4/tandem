// A few line icons drawn to sit beside 13 px system text, in the spirit of SF Symbols.
const PATHS = {
  xmark: 'M4 4l8 8M12 4l-8 8',
  warning: 'M8 2.2l6.2 11H1.8L8 2.2zM8 6.5v3.2M8 11.6v.1',
  info: 'M8 1.8a6.2 6.2 0 1 1 0 12.4A6.2 6.2 0 0 1 8 1.8zM8 7.2v4.2M8 4.9v.1',
  folder: 'M1.8 4.2a1 1 0 0 1 1-1h3.3l1.5 1.6h5.6a1 1 0 0 1 1 1v6.9a1 1 0 0 1-1 1H2.8a1 1 0 0 1-1-1V4.2zM1.8 6.4h12.4',
  doc: 'M4 1.8h5l3 3v9.4H4V1.8zM9 1.8v3h3',
  gauge: 'M2.2 11.5a6 6 0 1 1 11.6 0M8 11l2.6-4.2',
  pencil: 'M10.6 2.6l2.8 2.8-7.6 7.6-3.4.6.6-3.4 7.6-7.6zM9.2 4l2.8 2.8',
  eye: 'M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8s-2.4 4.5-6.5 4.5S1.5 8 1.5 8zM8 6.2a1.8 1.8 0 1 1 0 3.6 1.8 1.8 0 0 1 0-3.6z',
  list: 'M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4v.1M2.5 8v.1M2.5 12v.1',
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
