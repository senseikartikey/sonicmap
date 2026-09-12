"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AudioLines, Link2, ListOrdered, LogOut } from "lucide-react";
import { CurrentUser } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { apiFetch } from "@/lib/api";
import { SonicmapBrand } from "@/components/SonicmapBrand";

export function Nav({ user, onSignOut }: { user: CurrentUser | null; onSignOut?: () => void }) {
  const [scrolled, setScrolled] = useState(false);
  async function connectSpotify() {
    const result = await apiFetch<{ url: string }>("/auth/spotify/connect", { method: "POST" });
    window.location.assign(result.url);
  }

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-[background,border-color] duration-200 ${
        scrolled ? "border-b border-[var(--border-subtle)] bg-[rgba(10,9,7,0.8)] backdrop-blur-[16px]" : "border-b border-transparent"
      }`}
    >
      <div className="mx-auto flex h-16 max-w-[1400px] items-center justify-between px-6">
        <SonicmapBrand href="/" />

        {user && (
          <div className="flex items-center gap-4">
            <Link
              href="/studio/sets"
              className="hidden items-center gap-2 rounded-[var(--r-md)] px-3 py-2 font-mono text-[11px] tracking-[0.05em] text-[var(--text-body)] transition-colors hover:bg-white/[0.04] hover:text-[var(--text-primary)] sm:flex"
            >
              <ListOrdered size={14} strokeWidth={1.5} />
              SET STUDIO
            </Link>
            <Link
              href="/studio/stems"
              className="hidden items-center gap-2 rounded-[var(--r-md)] px-3 py-2 font-mono text-[11px] tracking-[0.05em] text-[var(--text-body)] transition-colors hover:bg-white/[0.04] hover:text-[var(--text-primary)] sm:flex"
            >
              <AudioLines size={14} strokeWidth={1.5} />
              STEM STUDIO
            </Link>
            <span className="hidden text-[13px] text-[var(--text-body)] sm:inline">
              {user.display_name ?? user.email ?? user.spotify_id ?? "Sonicmap listener"}
            </span>
            {!user.spotify_connected && !user.is_guest && <Button variant="ghost" size="sm" onClick={() => void connectSpotify()}><Link2 size={13} /> Connect Spotify</Button>}
            {user.is_guest && <span className="hidden font-mono text-[9px] text-[var(--accent)] md:inline">EPHEMERAL SESSION</span>}
            {onSignOut && (
              <Button variant="ghost" size="sm" onClick={onSignOut} aria-label="Sign out">
                <LogOut size={14} strokeWidth={1.5} />
              </Button>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
