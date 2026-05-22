import { cn } from "@/lib/utils";

/**
 * OpenAgentDojo brand mark — octagonal frame with a monospace `<|>` glyph
 * over a two-bar pedestal. Geometry mirrors apps/web/public/logo-mark.svg
 * exactly; colors flow through the theme tokens so the mark inverts on
 * dark backgrounds automatically.
 */
export function BrandMark({
  className,
  size = 20,
}: {
  className?: string;
  size?: number;
}) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 200 200"
      fill="none"
      aria-hidden
      className={cn("shrink-0", className)}
    >
      <g transform="translate(-624, -738)">
        {/* Octagonal frame */}
        <path
          fill="var(--color-foreground)"
          fillRule="evenodd"
          d="M 686 769 L 763 769 L 793 799 L 793 845 L 763 875 L 686 875 L 656 845 L 656 799 Z M 692 784 L 757 784 L 778 805 L 778 839 L 757 860 L 692 860 L 671 839 L 671 805 Z"
        />
        {/* Left chevron */}
        <path
          d="M 706 797 L 687 822 L 706 847"
          stroke="var(--color-primary)"
          strokeWidth="8"
          fill="none"
          strokeLinejoin="miter"
          strokeLinecap="butt"
        />
        {/* Pipe */}
        <rect x="720" y="797" width="8" height="50" fill="var(--color-primary)" />
        {/* Right chevron */}
        <path
          d="M 742 797 L 761 822 L 742 847"
          stroke="var(--color-primary)"
          strokeWidth="8"
          fill="none"
          strokeLinejoin="miter"
          strokeLinecap="butt"
        />
        {/* Pedestal */}
        <rect x="660" y="880" width="128" height="11" fill="var(--color-foreground)" />
        <rect x="638" y="897" width="172" height="11" fill="var(--color-foreground)" />
      </g>
    </svg>
  );
}
