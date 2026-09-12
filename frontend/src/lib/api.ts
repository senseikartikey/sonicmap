const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const SESSION_TOKEN_KEY = "sonicmap_session_token";
const GUEST_TOKEN_KEY = "sonicmap_guest_session_token";

export function getSessionToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(SESSION_TOKEN_KEY) ?? sessionStorage.getItem(GUEST_TOKEN_KEY);
}

export function setSessionToken(token: string) {
  sessionStorage.removeItem(GUEST_TOKEN_KEY);
  localStorage.setItem(SESSION_TOKEN_KEY, token);
}

export function setGuestSessionToken(token: string) {
  localStorage.removeItem(SESSION_TOKEN_KEY);
  sessionStorage.setItem(GUEST_TOKEN_KEY, token);
}

export function clearSessionToken() {
  localStorage.removeItem(SESSION_TOKEN_KEY);
  sessionStorage.removeItem(GUEST_TOKEN_KEY);
}

export function spotifyLoginUrl(): string {
  return `${API_BASE_URL}/auth/spotify/login`;
}

export function googleLoginUrl(): string {
  return `${API_BASE_URL}/auth/google/login`;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getSessionToken();
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === "AbortError") throw reason;
    // A rejected fetch() means the request never reached the server (offline, DNS, CORS,
    // the backend is down) — the raw TypeError message ("Failed to fetch") isn't written
    // for a human, so replace it with something a user can actually act on.
    throw new Error("Can't reach the server. Check your connection and try again.");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    let rawDetail: unknown = body;
    try {
      rawDetail = JSON.parse(body).detail ?? body;
    } catch {
      // not JSON, use raw body
    }
    // What the user sees stays plain language — no HTTP verb, path, or status code, and
    // never FastAPI's own validation-error shape (`detail` as an array of {msg, loc, ...}
    // objects for malformed requests) — that's not a string a person should ever read, so
    // it falls back to the generic message same as any other non-string detail.
    const userMessage =
      typeof rawDetail === "string" && rawDetail
        ? rawDetail
        : "Something went wrong on our end. Try again in a moment.";
    console.error(`${init?.method ?? "GET"} ${path} failed (${res.status}):`, rawDetail || res.statusText);
    throw new Error(userMessage);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

/** Downloads an export of a saved set.
 *
 * A plain <a download> can't be used: the export endpoints are authenticated, and the browser
 * sends no Authorization header on a navigation. So the file is fetched with the session
 * token like any other call and handed to the user as an object URL.
 */
export async function downloadSetExport(setId: string, format: "rekordbox" | "m3u8" | "cue") {
  const token = getSessionToken();
  const res = await fetch(`${API_BASE_URL}/mashup/sets/${setId}/export/${format}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error("That export could not be generated.");
  const disposition = res.headers.get("content-disposition") ?? "";
  const named = /filename="([^"]+)"/.exec(disposition);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = named?.[1] ?? `sonicmap-set.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoked on a later tick so the click has definitely been dispatched first.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Ask the backend to re-resolve a song's preview clip.
 *
 * Deezer-sourced previews are signed links that expire to a permanent 403, so a stored URL
 * failing usually means a stale signature rather than a missing track. Returns null instead of
 * throwing: the caller is already on a failure path and a refusal here is just "still no clip".
 */
export async function refreshPreviewUrl(songId: string): Promise<string | null> {
  try {
    const result = await apiFetch<{ preview_url: string }>(
      `/catalog/songs/${songId}/refresh-preview`,
      { method: "POST" }
    );
    return result.preview_url ?? null;
  } catch {
    return null;
  }
}
