"use client";

import { useMemo, useRef, useState, WheelEvent, PointerEvent as ReactPointerEvent } from "react";
import { Minus, Plus, LocateFixed } from "lucide-react";
import { MapPoint } from "@/lib/types";
import { MetaLine } from "@/components/ui/MetaLine";

const VIEW_W = 1000;
const VIEW_H = 680;
const PAD = 60;

/** Desaturated warm palette for cluster identity — the accent stays surgical (selection/hover
 * only), so cluster grouping reads through muted hue, not saturation. */
const CLUSTER_COLORS = [
  "#8fa3ad", "#9fae8a", "#a893a8", "#ad8f8f", "#8f97b9", "#8fb0a0", "#a0a08f", "#8fa0c0",
];
const NOISE_COLOR = "#524c42";

function clusterColor(label: number | null): string {
  if (label === null || label < 0) return NOISE_COLOR;
  return CLUSTER_COLORS[label % CLUSTER_COLORS.length];
}

export function TasteMap({
  points,
  selectedId,
  onSelect,
}: {
  points: MapPoint[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  const placed = useMemo(() => points.filter((p) => p.x !== null && p.y !== null), [points]);

  const scaled = useMemo(() => {
    if (placed.length === 0) return [];
    const xs = placed.map((p) => p.x as number);
    const ys = placed.map((p) => p.y as number);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;
    return placed.map((p) => ({
      point: p,
      cx: PAD + (((p.x as number) - minX) / spanX) * (VIEW_W - PAD * 2),
      cy: PAD + (((p.y as number) - minY) / spanY) * (VIEW_H - PAD * 2),
    }));
  }, [placed]);

  const clusterCount = useMemo(
    () => new Set(placed.map((p) => p.cluster_label).filter((c) => c !== null && c >= 0)).size,
    [placed]
  );

  const clusterLabels = useMemo(
    () =>
      Array.from(
        new Set(placed.map((p) => p.cluster_label).filter((c): c is number => c !== null && c >= 0))
      ).sort((a, b) => a - b),
    [placed]
  );

  function clampZoom(z: number) {
    return Math.min(6, Math.max(0.6, z));
  }

  function handleWheel(e: WheelEvent<SVGSVGElement>) {
    e.preventDefault();
    const next = clampZoom(zoom * (e.deltaY > 0 ? 0.9 : 1.1));
    setZoom(next);
  }

  function handlePointerDown(e: ReactPointerEvent<SVGSVGElement>) {
    dragRef.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y };
    (e.target as Element).setPointerCapture(e.pointerId);
  }

  function handlePointerMove(e: ReactPointerEvent<SVGSVGElement>) {
    if (containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect();
      setTooltipPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    }
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.x;
    const dy = e.clientY - dragRef.current.y;
    setPan({ x: dragRef.current.panX + dx / zoom, y: dragRef.current.panY + dy / zoom });
  }

  function handlePointerUp(e: ReactPointerEvent<SVGSVGElement>) {
    dragRef.current = null;
    (e.target as Element).releasePointerCapture(e.pointerId);
  }

  function resetView() {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }

  const hovered = scaled.find((s) => s.point.song.id === hoveredId);

  return (
    <div ref={containerRef} className="relative h-full min-h-[420px] w-full overflow-hidden rounded-[var(--r-lg)] bg-[var(--bg-media)]">
      <div
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          backgroundImage: "radial-gradient(rgba(255,248,230,0.05) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
        aria-hidden
      />

      <div className="pointer-events-none absolute left-4 top-4 z-10 font-mono text-[11px] tracking-[0.04em] text-[var(--text-tertiary)] tabular">
        [{placed.length} PLACED] // [{clusterCount} CLUSTERS]
      </div>

      {clusterLabels.length > 0 && (
        <div className="pointer-events-none absolute right-4 top-4 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[var(--r-md)] bg-[var(--bg-card)] px-2.5 py-1.5 font-mono text-[10px] tracking-[0.02em] text-[var(--text-tertiary)]">
          {clusterLabels.map((label) => (
            <span key={label} className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: clusterColor(label) }} />
              {label}
            </span>
          ))}
        </div>
      )}

      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        className="h-full w-full touch-none"
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={() => setHoveredId(null)}
        role="group"
        aria-label={`Taste map with ${placed.length} placed songs across ${clusterCount} clusters. Tab through songs, Enter or Space to select.`}
      >
        <g transform={`translate(${VIEW_W / 2}, ${VIEW_H / 2}) scale(${zoom}) translate(${-VIEW_W / 2 + pan.x}, ${-VIEW_H / 2 + pan.y})`}>
          {scaled.map(({ point, cx, cy }) => {
            const isSelected = selectedId === point.song.id;
            const isHovered = hoveredId === point.song.id;
            const color = clusterColor(point.cluster_label);
            return (
              <g key={point.song.id}>
                {/* Decorative — the visible dot stays small and precise on purpose. */}
                <circle
                  cx={cx}
                  cy={cy}
                  r={isSelected ? 8 : isHovered ? 7 : 5}
                  fill={isSelected || isHovered ? "var(--accent)" : color}
                  fillOpacity={point.cluster_label === null || point.cluster_label < 0 ? 0.45 : 0.85}
                  stroke={isSelected ? "var(--accent-bright)" : "transparent"}
                  strokeWidth={isSelected ? 2 : 0}
                  style={isSelected ? { animation: "node-glow-pulse 1.8s ease-in-out infinite" } : undefined}
                  className="pointer-events-none transition-[r,fill] duration-150"
                />
                {/* Interactive — a larger invisible hit target so selecting a song doesn't
                    require pinpoint precision (the visible dot alone is well under the
                    24x24px WCAG 2.2 minimum at typical map widths). */}
                <circle
                  cx={cx}
                  cy={cy}
                  r={14}
                  fill="transparent"
                  className="cursor-pointer"
                  tabIndex={0}
                  role="button"
                  aria-label={`${point.song.title} by ${point.song.artist}${isSelected ? ", selected" : ""}`}
                  onPointerEnter={() => setHoveredId(point.song.id)}
                  onPointerLeave={() => setHoveredId((id) => (id === point.song.id ? null : id))}
                  onFocus={() => setHoveredId(point.song.id)}
                  onBlur={() => setHoveredId((id) => (id === point.song.id ? null : id))}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelect(isSelected ? null : point.song.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(isSelected ? null : point.song.id);
                    }
                  }}
                />
              </g>
            );
          })}
        </g>
      </svg>

      {hovered && (
        <div
          className="pointer-events-none absolute z-20 max-w-[220px] rounded-[var(--r-md)] bg-[var(--bg-card)] px-3 py-2 shadow-[inset_0_1px_0_rgba(255,248,230,0.08),0_4px_24px_rgba(0,0,0,0.45)]"
          style={{ left: tooltipPos.x + 14, top: tooltipPos.y + 14 }}
        >
          <p className="truncate text-[13px] font-medium text-[var(--text-primary)]">{hovered.point.song.title}</p>
          <p className="truncate text-[12px] text-[var(--text-body)]">{hovered.point.song.artist}</p>
          <MetaLine song={hovered.point.song} className="mt-1" />
        </div>
      )}

      <div className="absolute bottom-4 right-4 z-10 flex gap-1 rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--bg-card)] p-1">
        <button
          onClick={() => setZoom((z) => clampZoom(z * 1.2))}
          className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-body)] transition-colors hover:bg-white/[0.05] hover:text-[var(--text-primary)]"
          aria-label="Zoom in"
        >
          <Plus size={14} strokeWidth={1.5} />
        </button>
        <button
          onClick={() => setZoom((z) => clampZoom(z / 1.2))}
          className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-body)] transition-colors hover:bg-white/[0.05] hover:text-[var(--text-primary)]"
          aria-label="Zoom out"
        >
          <Minus size={14} strokeWidth={1.5} />
        </button>
        <button
          onClick={resetView}
          className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-body)] transition-colors hover:bg-white/[0.05] hover:text-[var(--text-primary)]"
          aria-label="Reset view"
        >
          <LocateFixed size={14} strokeWidth={1.5} />
        </button>
      </div>
    </div>
  );
}
