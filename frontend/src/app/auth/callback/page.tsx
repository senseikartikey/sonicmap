"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { setSessionToken } from "@/lib/api";

export const JUST_SIGNED_IN_KEY = "sonicmap_just_signed_in";

/** Pure handoff: stores the token and redirects immediately — no animation lives here
 * anymore, since playing it on this page meant a real navigation (and a second, separate
 * loading state) had to happen *after* it finished. The cinematic intro now lives inside
 * page.tsx itself, as an overlay on top of the same mount that loads the real data, triggered
 * by the sessionStorage flag set below. */
export default function AuthCallbackPage() {
  const router = useRouter();

  useEffect(() => {
    // Belt-and-suspenders alongside Landing.tsx's own prefetch of the same chunk: covers a
    // visitor who reached this callback without having sat on Landing first (e.g. a stale tab
    // resuming an in-flight OAuth redirect), so the intro's chunk still has a head start on the
    // replace() below rather than starting from zero once "/" asks for it.
    void import("@/components/SignInBrainIntro");
    const hash = new URLSearchParams(window.location.hash.slice(1));
    const token = hash.get("token");
    if (token) {
      setSessionToken(token);
      sessionStorage.setItem(JUST_SIGNED_IN_KEY, "1");
    }
    router.replace("/");
  }, [router]);

  return <div className="fixed inset-0 bg-[var(--bg-base)]" />;
}
