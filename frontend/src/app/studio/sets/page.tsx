"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, RefreshCw, Trash2, Wand2 } from "lucide-react";
import { apiFetch, clearSessionToken, downloadSetExport, getSessionToken } from "@/lib/api";
import { CurrentUser, EnergyShape, MashupSet } from "@/lib/types";
import { Nav } from "@/components/Nav";
import { SetPlanner } from "@/components/SetPlanner";
import { Button } from "@/components/ui/Button";
import { Card, SectionLabel } from "@/components/ui/Card";

const SHAPES: { value: EnergyShape; label: string; hint: string }[] = [
  { value: "arc", label: "Arc", hint: "Build to a peak four fifths in, then ease off" },
  { value: "build", label: "Build", hint: "Climb the whole way" },
  { value: "peak", label: "Peak", hint: "Open high and stay there" },
  { value: "wind_down", label: "Wind down", hint: "Bring the room back down" },
  { value: "wave", label: "Wave", hint: "Rise and fall more than once" },
];

const SOURCES: { value: "recommendations" | "map"; label: string; hint: string }[] = [
  { value: "recommendations", label: "My recommendations", hint: "Plan a set from what Sonicmap is suggesting now" },
  { value: "map", label: "My map", hint: "Plan a set from songs already on your map" },
];

export default function SetStudioPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [sets, setSets] = useState<MashupSet[]>([]);
  const [active, setActive] = useState<MashupSet | null>(null);
  const [source, setSource] = useState<"recommendations" | "map">("recommendations");
  const [shape, setShape] = useState<EnergyShape>("arc");
  const [size, setSize] = useState(12);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSets = useCallback(async () => {
    const result = await apiFetch<MashupSet[]>("/mashup/sets");
    setSets(result);
    setActive((current) => current ?? result[0] ?? null);
  }, []);

  useEffect(() => {
    Promise.resolve(getSessionToken())
      .then((token) => {
        if (!token) return;
        return apiFetch<CurrentUser>("/auth/me")
          .then((result) => {
            setUser(result);
            return loadSets();
          })
          .catch(() => clearSessionToken());
      })
      .finally(() => setAuthLoading(false));
  }, [loadSets]);

  async function build() {
    setBusy(true);
    setError(null);
    try {
      const created = await apiFetch<MashupSet>("/mashup/plan", {
        method: "POST",
        body: JSON.stringify({ source, limit: size, shape, save: true }),
      });
      setActive(created);
      await loadSets();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  /** Moving a track replans every blend around it — the server recomputes rather than shifting
   * the old transitions along, because a technique chosen for a pair that no longer sits
   * together is worse than no plan at all. */
  async function move(songId: string, direction: -1 | 1) {
    if (!active?.id) return;
    const ids = active.plan.tracks.map((track) => track.id);
    const from = ids.indexOf(songId);
    const to = from + direction;
    if (from < 0 || to < 0 || to >= ids.length) return;
    [ids[from], ids[to]] = [ids[to], ids[from]];
    setBusy(true);
    setError(null);
    try {
      const updated = await apiFetch<MashupSet>(`/mashup/sets/${active.id}/order`, {
        method: "PATCH",
        body: JSON.stringify({ song_ids: ids }),
      });
      setActive(updated);
      setSets((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function remove(setId: string) {
    setError(null);
    try {
      await apiFetch(`/mashup/sets/${setId}`, { method: "DELETE" });
      setActive((current) => (current?.id === setId ? null : current));
      await loadSets();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function exportSet(format: "rekordbox" | "m3u8" | "cue") {
    if (!active?.id) return;
    setError(null);
    try {
      await downloadSetExport(active.id, format);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function signOut() {
    clearSessionToken();
    router.push("/");
  }

  if (authLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <RefreshCw className="animate-spin text-[var(--accent)]" />
      </div>
    );
  }
  if (!user) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6">
        <Card className="max-w-md p-8 text-center">
          <SectionLabel>Authentication required</SectionLabel>
          <h1 className="mt-3 text-2xl font-semibold">Sign in to plan a set</h1>
          <p className="mt-3 text-[var(--text-body)]">
            Set planning runs against your own map and recommendations, so it needs your account.
          </p>
          <Link className="mt-6 inline-block text-[var(--accent-bright)]" href="/">Return to Sonicmap</Link>
        </Card>
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--bg-base)]">
      <Nav user={user} onSignOut={signOut} />
      <main className="mx-auto max-w-[1100px] px-6 pb-16 pt-24">
        <Link href="/" className="inline-flex items-center gap-2 text-[12px] text-[var(--text-tertiary)] hover:text-[var(--text-primary)]">
          <ArrowLeft size={14} /> Back to your brain
        </Link>

        <div className="mt-5">
          <SectionLabel>Set Studio</SectionLabel>
          <h1 className="mt-2 text-3xl font-semibold tracking-[-0.03em]">Put them in the right order.</h1>
          <p className="mt-2 max-w-2xl text-[var(--text-body)]">
            Sonicmap works out what should follow what — matching tempo, key and texture — then plans the
            blend between every pair: which technique, how many bars, how far to pull the tempo. Export it
            to rekordbox, Serato or any player and mix it against your own files.
          </p>
        </div>

        <Card className="mt-7 p-6">
          <div className="grid gap-5 sm:grid-cols-2">
            <fieldset>
              <legend className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
                Build from
              </legend>
              <div className="mt-2 flex flex-col gap-1.5">
                {SOURCES.map((option) => (
                  <label key={option.value} className="flex cursor-pointer items-start gap-2.5 text-[14px]">
                    <input
                      type="radio"
                      name="source"
                      className="mt-1 accent-[var(--accent)]"
                      checked={source === option.value}
                      onChange={() => setSource(option.value)}
                    />
                    <span>
                      <span className="text-[var(--text-primary)]">{option.label}</span>
                      <span className="block text-[12px] text-[var(--text-tertiary)]">{option.hint}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
                Energy shape
              </legend>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {SHAPES.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    title={option.hint}
                    onClick={() => setShape(option.value)}
                    aria-pressed={shape === option.value}
                    className={`rounded-[var(--r-pill)] border px-3 py-1.5 text-[12px] transition-colors ${
                      shape === option.value
                        ? "border-[var(--accent)] text-[var(--text-primary)]"
                        : "border-[var(--border-subtle)] text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                    }`}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <p className="mt-2 text-[12px] text-[var(--text-tertiary)]">
                {SHAPES.find((option) => option.value === shape)?.hint}.
              </p>
            </fieldset>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-4 border-t border-[var(--border-subtle)] pt-5">
            <label className="flex items-center gap-3 text-[13px] text-[var(--text-body)]">
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
                Tracks
              </span>
              <input
                type="range"
                min={4}
                max={30}
                value={size}
                onChange={(event) => setSize(Number(event.target.value))}
                className="w-40 accent-[var(--accent)]"
                aria-label="How many tracks in the set"
              />
              <span className="w-6 font-mono tabular-nums">{size}</span>
            </label>
            <Button variant="primary" onClick={build} loading={busy} disabled={busy} className="ml-auto">
              <Wand2 size={15} /> Plan the set
            </Button>
          </div>
          {error && <p className="mt-4 text-[13px] text-[var(--error)]">{error}</p>}
        </Card>

        {active && (
          <Card className="mt-6 p-6">
            <SetPlanner
              plan={active.plan}
              name={active.name}
              quality={active.quality}
              saved={Boolean(active.id)}
              busy={busy}
              onMove={move}
              onExport={exportSet}
            />
          </Card>
        )}

        {sets.length > 0 && (
          <section className="mt-8">
            <SectionLabel>Saved sets</SectionLabel>
            <ul className="mt-3 flex flex-col gap-1">
              {sets.map((item) => (
                <li key={item.id} className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setActive(item)}
                    aria-current={active?.id === item.id}
                    className={`flex-1 rounded-[var(--r-md)] px-3 py-2.5 text-left transition-colors hover:bg-white/[0.03] ${
                      active?.id === item.id ? "bg-white/[0.04]" : ""
                    }`}
                  >
                    <span className="text-[14px] text-[var(--text-primary)]">{item.name}</span>
                    <span className="ml-3 font-mono text-[11px] tabular-nums text-[var(--text-tertiary)]">
                      {item.plan.tracks.length} tracks
                      {item.quality !== null ? ` · quality ${Math.round(item.quality * 100)}` : ""}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => item.id && remove(item.id)}
                    aria-label={`Delete ${item.name}`}
                    className="rounded p-2 text-[var(--text-tertiary)] transition-colors hover:bg-white/[0.06] hover:text-[var(--error)]"
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
    </div>
  );
}
