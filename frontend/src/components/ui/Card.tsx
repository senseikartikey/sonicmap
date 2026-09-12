import { HTMLAttributes } from "react";

export function Card({
  featured,
  hoverable,
  className = "",
  ...props
}: HTMLAttributes<HTMLDivElement> & { featured?: boolean; hoverable?: boolean }) {
  return (
    <div
      className={[
        "rounded-[var(--r-lg)] bg-[var(--bg-card)]",
        "shadow-[inset_0_1px_0_rgba(255,248,230,0.08),0_4px_24px_rgba(0,0,0,0.45)]",
        hoverable &&
          "transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-[inset_0_1px_0_rgba(255,248,230,0.1),0_12px_40px_rgba(0,0,0,0.55)]",
        featured &&
          "shadow-[inset_0_1px_0_rgba(255,248,230,0.1),0_4px_24px_rgba(0,0,0,0.45),0_0_0_1px_var(--border-accent),0_0_30px_rgba(212,160,60,0.12)]",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      {...props}
    />
  );
}

/** Renders as a real heading element by default so screen-reader heading navigation reaches
 * panel titles — the `[Bracket]` look comes entirely from CSS, not from being a non-heading
 * span. Pass `as="span"` only for section labels that sit above no content of their own
 * (e.g. a kicker-less inline tag). */
export function SectionLabel({
  children,
  as: Tag = "h2",
}: {
  children: React.ReactNode;
  as?: "h2" | "h3" | "span";
}) {
  return (
    <Tag className="block font-mono text-[11px] font-normal tracking-[0.06em] text-[var(--accent)]">
      [{children}]
    </Tag>
  );
}
