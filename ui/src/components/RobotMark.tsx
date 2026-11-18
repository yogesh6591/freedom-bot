/** The FreedomBot robot mark: a smiling robot head with ear pieces, drawn in the current text colour. */
export function RobotMark({ size = 22, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <rect x="1.6" y="7.2" width="3.4" height="6.2" rx="1.5" fill="currentColor" />
      <rect x="19" y="7.2" width="3.4" height="6.2" rx="1.5" fill="currentColor" />
      <rect x="4.6" y="3.9" width="14.8" height="12.8" rx="4.8" stroke="currentColor" strokeWidth="2.1" />
      <circle cx="9.3" cy="9.6" r="1.75" fill="currentColor" />
      <circle cx="14.7" cy="9.6" r="1.75" fill="currentColor" />
      <path d="M9.6 12.9c1.45 1.1 3.35 1.1 4.8 0" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}
