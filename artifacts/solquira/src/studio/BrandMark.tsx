/** Sun over two contour lines: a valley in perspective, a figure in it, water in the soil. */
export default function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 64 64"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="32" cy="16" r="6.8" fill="#c98f1f" />
      <path
        d="M7 40c8-7 17-10 25-10s17 3 25 10"
        stroke="#173d36"
        strokeWidth="4"
        strokeLinecap="round"
      />
      <path
        d="M12 53c6-4.5 13-7 20-7s14 2.5 20 7"
        stroke="#117f88"
        strokeWidth="4"
        strokeLinecap="round"
      />
    </svg>
  );
}
