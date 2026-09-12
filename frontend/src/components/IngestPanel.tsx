"use client";

import { FormEvent, KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { ListMusic, Search, Ellipsis } from "lucide-react";
import { Card, SectionLabel } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { apiFetch } from "@/lib/api";
import { StatusMessage } from "@/lib/types";

type SearchSuggestion = { title: string; artist: string };

type Tab = "search" | "paste" | "playlist";

const TABS: { id: Tab; label: string; icon: typeof Search }[] = [
  { id: "search", label: "Search", icon: Search },
  { id: "paste", label: "Paste list", icon: ListMusic },
  { id: "playlist", label: "Spotify playlist", icon: Ellipsis },
];

export function IngestPanel({
  onSearch,
  onPaste,
  onPlaylist,
  busy,
  status,
  spotifyEnabled = true,
}: {
  onSearch: (query: string) => Promise<void>;
  onPaste: (raw: string) => Promise<void>;
  onPlaylist: (url: string) => Promise<void>;
  busy: boolean;
  status: StatusMessage;
  spotifyEnabled?: boolean;
}) {
  const [tab, setTab] = useState<Tab>("search");
  // Search is split into `typed` (exactly what the user has authored — the source of truth
  // for fetching suggestions and for submission) and `completion` (an inline ghost suffix,
  // shown selected/highlighted in the input, browser-address-bar style — continuing to type
  // naturally overwrites it since it's a real text selection, no extra key handling needed
  // for that part). The input's displayed value is always `typed + completion`.
  const [typed, setTyped] = useState("");
  const [completion, setCompletion] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [playlistUrl, setPlaylistUrl] = useState("");
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const searchId = useId();
  const pasteId = useId();
  const playlistId = useId();
  const listboxId = useId();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const searchInputRef = useRef<HTMLInputElement>(null);
  // True right after a delete shrinks `typed` — suppresses re-offering an inline completion
  // for one fetch cycle, otherwise backspacing is fought by the ghost text reappearing.
  const suppressCompletionRef = useRef(false);

  const displayValue = typed + completion;

  // Debounced typeahead against GET /ingest/search/suggestions — a lightweight, non-mutating
  // preview of what /ingest/search would resolve to, so the user can see real title/artist
  // matches (and pick one) before spending an actual ingest call on a typo or wrong version.
  // Also drives the inline ghost completion: when the top match's "Artist - Title" starts
  // with what's been typed, the remainder is offered inline rather than only in the list.
  useEffect(() => {
    if (tab !== "search" || typed.trim().length < 2) {
      // Deferred (not called synchronously in the effect body) to avoid the cascading-render
      // lint warning — functionally identical, just fires on the next tick instead of inline.
      const clearHandle = setTimeout(() => {
        setSuggestions([]);
        setActiveSuggestion(-1);
      }, 0);
      return () => clearTimeout(clearHandle);
    }
    const controller = new AbortController();
    const handle = setTimeout(async () => {
      try {
        const results = await apiFetch<SearchSuggestion[]>(
          `/ingest/search/suggestions?q=${encodeURIComponent(typed.trim())}`,
          { signal: controller.signal }
        );
        setSuggestions(results);
        setActiveSuggestion(-1);
        const top = results[0];
        const fullTop = top ? `${top.artist} - ${top.title}` : "";
        if (
          !suppressCompletionRef.current &&
          top &&
          fullTop.length > typed.length &&
          fullTop.toLowerCase().startsWith(typed.toLowerCase())
        ) {
          setCompletion(fullTop.slice(typed.length));
        } else {
          setCompletion("");
        }
      } catch {
        // A stale/aborted request or a transient failure here shouldn't interrupt typing —
        // the user can still submit the raw query, suggestions are a convenience only.
      }
    }, 250);
    return () => {
      clearTimeout(handle);
      controller.abort();
    };
  }, [typed, tab]);

  // Selects the inline completion (browser-address-bar style) once it's rendered into the
  // input's actual DOM value, so the next keystroke naturally overwrites it.
  useEffect(() => {
    const el = searchInputRef.current;
    if (completion && el && document.activeElement === el) {
      el.setSelectionRange(typed.length, typed.length + completion.length);
    }
  }, [completion, typed]);

  // Best-effort guard against the "typed only an artist name, hit enter, got a random song
  // by them" complaint: resolve_track can't tell "just an artist" from "a real query" apart
  // on its own, since it just takes iTunes' top hit either way — but if the trimmed typed
  // text exactly matches a suggestion's artist (and no suggestion's title), that's a strong
  // signal this isn't a specific song yet. Never true while a completion is offered/accepted,
  // since that always carries a real title.
  const trimmedTyped = typed.trim().toLowerCase();
  const queryLooksArtistOnly =
    tab === "search" &&
    completion === "" &&
    trimmedTyped.length > 0 &&
    suggestions.some((s) => s.artist.toLowerCase() === trimmedTyped) &&
    !suggestions.some((s) => s.title.toLowerCase() === trimmedTyped);

  function pickSuggestion(s: SearchSuggestion) {
    setTyped(`${s.artist} - ${s.title}`);
    setCompletion("");
    setSuggestions([]);
    setSuggestionsOpen(false);
    setActiveSuggestion(-1);
  }

  function handleSearchChange(e: React.ChangeEvent<HTMLInputElement>) {
    const newValue = e.target.value;
    // A completion selection spans to the end of the string by construction, so whatever the
    // browser produces here — typing over it, deleting it, or a plain edit with no active
    // completion — is already the correct new authored text; no caret math needed.
    suppressCompletionRef.current = newValue.length < typed.length + completion.length;
    setTyped(newValue);
    setCompletion("");
    setSuggestionsOpen(true);
  }

  function handleSearchKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if ((e.key === "ArrowRight" || e.key === "End") && completion) {
      // Accept the inline completion without submitting, mirroring a browser address bar.
      setTyped(typed + completion);
      setCompletion("");
      return;
    }
    if (e.key === "Escape") {
      setCompletion("");
      setSuggestionsOpen(false);
      return;
    }
    if (!suggestionsOpen || suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveSuggestion((i) => (i + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveSuggestion((i) => (i - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === "Enter" && activeSuggestion >= 0) {
      e.preventDefault();
      pickSuggestion(suggestions[activeSuggestion]);
    }
  }

  function focusTab(index: number) {
    const target = TABS[(index + TABS.length) % TABS.length];
    setTab(target.id);
    tabRefs.current[(index + TABS.length) % TABS.length]?.focus();
  }

  function handleTabKeyDown(e: KeyboardEvent, index: number) {
    if (e.key === "ArrowRight") {
      e.preventDefault();
      focusTab(index + 1);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      focusTab(index - 1);
    } else if (e.key === "Home") {
      e.preventDefault();
      focusTab(0);
    } else if (e.key === "End") {
      e.preventDefault();
      focusTab(TABS.length - 1);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (tab === "search" && queryLooksArtistOnly) {
      // Refuse to silently ingest a random top hit — nudge toward picking an actual song.
      setSuggestionsOpen(true);
      return;
    }
    if (tab === "search" && displayValue.trim()) {
      // An unaccepted inline completion is still a real, specific suggestion — submitting
      // with it visible loads that exact song, same as accepting it and pressing enter again.
      await onSearch(displayValue.trim());
      setTyped("");
      setCompletion("");
    } else if (tab === "paste" && pasteText.trim()) {
      await onPaste(pasteText);
      setPasteText("");
    } else if (tab === "playlist" && playlistUrl.trim()) {
      await onPlaylist(playlistUrl.trim());
      setPlaylistUrl("");
    }
  }

  return (
    <Card className="p-6">
      <div className="flex items-baseline gap-2">
        <SectionLabel>Ingest</SectionLabel>
        <span className="text-[13px] text-[var(--text-tertiary)]">— feed the map</span>
      </div>

      <div
        role="tablist"
        aria-label="Ingestion method"
        className="mt-5 flex gap-1 rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-1"
      >
        {TABS.map((t, index) => {
          const spotifyLocked = t.id === "playlist" && !spotifyEnabled;
          return (
          <button
            key={t.id}
            ref={(el) => {
              tabRefs.current[index] = el;
            }}
            role="tab"
            aria-selected={tab === t.id}
            tabIndex={tab === t.id ? 0 : -1}
            disabled={spotifyLocked}
            title={spotifyLocked ? "Sign in and connect Spotify to import playlists" : undefined}
            onClick={() => setTab(t.id)}
            onKeyDown={(e) => handleTabKeyDown(e, index)}
            className={`flex flex-1 items-center justify-center gap-[6px] rounded-[calc(var(--r-md)-2px)] px-2 py-2 text-[12px] font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-30 ${
              tab === t.id && !spotifyLocked
                ? "bg-[var(--bg-card)] text-[var(--text-primary)] shadow-[inset_0_1px_0_rgba(255,248,230,0.08)]"
                : "text-[var(--text-body)] hover:text-[var(--text-primary)]"
            }`}
          >
            <t.icon size={13} strokeWidth={1.5} />
            <span className="hidden md:inline">{t.label}</span>
          </button>
        );})}
      </div>

      <form onSubmit={handleSubmit} className="mt-4 space-y-3">
        {tab === "search" && (
          <div className="relative">
            <label htmlFor={searchId} className="mb-1.5 block text-[13px] text-[var(--text-body)]">
              Artist &amp; title
            </label>
            <input
              ref={searchInputRef}
              id={searchId}
              role="combobox"
              aria-expanded={suggestionsOpen && suggestions.length > 0}
              aria-controls={listboxId}
              aria-autocomplete="both"
              autoComplete="off"
              className="w-full rounded-[var(--r-md)] border border-[var(--border-medium)] bg-[var(--bg-surface)] px-3 py-2.5 text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] outline-none transition-colors focus:border-[var(--border-accent)]"
              value={displayValue}
              onChange={handleSearchChange}
              onFocus={() => setSuggestionsOpen(true)}
              onBlur={() => setTimeout(() => setSuggestionsOpen(false), 120)}
              onKeyDown={handleSearchKeyDown}
              placeholder="Radiohead - Weird Fishes"
            />
            {suggestionsOpen && suggestions.length > 0 && (
              <ul
                id={listboxId}
                role="listbox"
                aria-label="Search suggestions"
                className="absolute z-20 mt-1.5 w-full overflow-hidden rounded-[var(--r-md)] border border-[var(--border-medium)] bg-[var(--bg-card)] shadow-[0_8px_24px_rgba(0,0,0,0.45)]"
              >
                {suggestions.map((s, i) => (
                  <li key={`${s.artist}-${s.title}-${i}`} role="option" aria-selected={activeSuggestion === i}>
                    <button
                      type="button"
                      // onMouseDown (not onClick) fires before the input's onBlur, so the
                      // pick registers before the listbox unmounts on blur.
                      onMouseDown={(e) => {
                        e.preventDefault();
                        pickSuggestion(s);
                      }}
                      className={`block w-full truncate px-3 py-2 text-left text-[13px] transition-colors ${
                        activeSuggestion === i
                          ? "bg-white/[0.06] text-[var(--text-primary)]"
                          : "text-[var(--text-body)] hover:bg-white/[0.04] hover:text-[var(--text-primary)]"
                      }`}
                    >
                      <span className="text-[var(--text-primary)]">{s.artist}</span>
                      <span className="text-[var(--text-tertiary)]"> — {s.title}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {queryLooksArtistOnly && (
              <p role="alert" className="mt-1.5 text-[11px] leading-[1.5] text-[var(--accent)]">
                That&apos;s just an artist — pick a specific song above, or add a title (e.g.
                &quot;{typed.trim()} - Song Title&quot;).
              </p>
            )}
          </div>
        )}

        {tab === "paste" && (
          <div>
            <label htmlFor={pasteId} className="mb-1.5 block text-[13px] text-[var(--text-body)]">
              One song per line
            </label>
            <textarea
              id={pasteId}
              className="h-28 w-full resize-none rounded-[var(--r-md)] border border-[var(--border-medium)] bg-[var(--bg-surface)] px-3 py-2.5 text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] outline-none transition-colors focus:border-[var(--border-accent)]"
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder={"Artist - Title\nArtist - Title"}
            />
            <p className="mt-1.5 text-[11px] text-[var(--text-tertiary)]">Up to 200 songs per paste.</p>
          </div>
        )}

        {tab === "playlist" && (
          <div>
            <label htmlFor={playlistId} className="mb-1.5 block text-[13px] text-[var(--text-body)]">
              Playlist URL or ID
            </label>
            <input
              id={playlistId}
              className="w-full rounded-[var(--r-md)] border border-[var(--border-medium)] bg-[var(--bg-surface)] px-3 py-2.5 text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] outline-none transition-colors focus:border-[var(--border-accent)]"
              value={playlistUrl}
              onChange={(e) => setPlaylistUrl(e.target.value)}
              placeholder="https://open.spotify.com/playlist/..."
            />
          </div>
        )}

        <Button
          type="submit"
          variant="secondary"
          className="w-full"
          loading={busy}
          disabled={queryLooksArtistOnly}
        >
          {busy ? "Adding…" : "Add to map"}
        </Button>

        {status && (
          <p
            role="status"
            aria-live="polite"
            className={`font-mono text-[11px] leading-[1.5] tracking-[0.02em] ${
              status.tone === "error" ? "text-[var(--error)]" : "text-[var(--text-tertiary)]"
            }`}
          >
            {status.message}
          </p>
        )}
      </form>
    </Card>
  );
}
