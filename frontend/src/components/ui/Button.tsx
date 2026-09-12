import { ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "primary" | "secondary" | "ghost";
type Size = "sm" | "default";

const base =
  "inline-flex items-center justify-center gap-2 rounded-[var(--r-md)] font-semibold " +
  "transition-[box-shadow,border-color,background,transform,color] duration-200 " +
  "disabled:opacity-40 disabled:cursor-not-allowed disabled:pointer-events-none " +
  "cursor-pointer select-none";

const sizes: Record<Size, string> = {
  sm: "px-[18px] py-2 text-[13px]",
  default: "px-6 py-3 text-[14px]",
};

const variants: Record<Variant, string> = {
  primary:
    "text-[var(--text-primary)] bg-[var(--bg-card)] border border-[var(--accent)] " +
    "shadow-[0_0_8px_rgba(212,160,60,0.5),0_0_20px_rgba(212,160,60,0.22),0_0_40px_rgba(212,160,60,0.09)] " +
    "animate-[btn-pulse_2.8s_ease-in-out_infinite] " +
    "hover:animate-none hover:border-[var(--accent-bright)] hover:-translate-y-px " +
    "hover:shadow-[0_0_10px_rgba(212,160,60,0.75),0_0_28px_rgba(212,160,60,0.4),0_0_55px_rgba(212,160,60,0.18)] " +
    "active:translate-y-0",
  secondary:
    "text-[var(--text-primary)] bg-[var(--bg-card)] border border-[var(--border-medium)] " +
    "hover:border-[rgba(255,248,230,0.28)] hover:bg-white/[0.03]",
  ghost:
    "text-[var(--text-body)] bg-transparent border border-transparent " +
    "hover:text-[var(--text-primary)] hover:bg-white/[0.04]",
};

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}>(function Button({ variant = "secondary", size = "default", loading, className = "", children, disabled, ...props }, ref) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={`${base} ${sizes[size]} ${variants[variant]} ${className}`}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && (
        <span
          className="h-3.5 w-3.5 shrink-0 rounded-full border-2 border-current border-t-transparent opacity-70 animate-[spin_0.7s_linear_infinite]"
          aria-hidden
        />
      )}
      {children}
    </button>
  );
});
