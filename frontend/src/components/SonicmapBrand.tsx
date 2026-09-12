import Link from "next/link";

export function SonicmapLogomark({ size = 18 }: { size?: number }) {
  return (
    <svg className="sonicmap-logomark shrink-0" width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <line className="sonicmap-logomark__edge sonicmap-logomark__edge--1" x1="6" y1="17" x2="12" y2="7" stroke="currentColor" strokeWidth="1.4" />
      <line className="sonicmap-logomark__edge sonicmap-logomark__edge--2" x1="12" y1="7" x2="18" y2="15" stroke="currentColor" strokeWidth="1.4" />
      <line className="sonicmap-logomark__edge sonicmap-logomark__edge--3" x1="6" y1="17" x2="18" y2="15" stroke="currentColor" strokeWidth="1.4" opacity="0.5" />
      <circle className="sonicmap-logomark__node sonicmap-logomark__node--1" cx="12" cy="7" r="3" fill="var(--accent-bright)" />
      <circle className="sonicmap-logomark__node sonicmap-logomark__node--2" cx="6" cy="17" r="2.2" fill="var(--text-body)" />
      <circle className="sonicmap-logomark__node sonicmap-logomark__node--3" cx="18" cy="15" r="2.2" fill="var(--text-body)" />
    </svg>
  );
}

export function SonicmapBrand({ href, size = 18 }: { href?: string; size?: number }) {
  const content = <><SonicmapLogomark size={size} /><span>sonicmap<span className="text-[var(--accent)]">.</span></span></>;
  const className = "sonicmap-brand inline-flex items-center gap-2 rounded-[var(--r-sm)] text-[var(--text-primary)] outline-none transition-opacity hover:opacity-80 focus-visible:ring-2 focus-visible:ring-[var(--border-accent)]";
  return href ? <Link href={href} className={className} aria-label="sonicmap — go to home">{content}</Link> : <span className={className}>{content}</span>;
}
