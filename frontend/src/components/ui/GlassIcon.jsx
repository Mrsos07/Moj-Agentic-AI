const SIZES = {
  sm: 'size-5',
  md: 'size-7',
}

export default function GlassIcon({ children, size = 'md', className = '' }) {
  return (
    <span
      aria-hidden
      className={`moj-icon inline-flex items-center justify-center shrink-0 text-current ${SIZES[size] || SIZES.md} ${className}`}
    >
      {children}
    </span>
  )
}
