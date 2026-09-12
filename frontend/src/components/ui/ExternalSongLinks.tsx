import { SiApplemusic, SiSpotify, SiYoutube } from "react-icons/si";

/** No platform IDs are stored per song (iTunes resolution gives a preview clip, not a
 * Spotify/YouTube/Apple Music id) — these are search-result deep links, not canonical track
 * pages. Reliable across every song regardless of ingestion source, and needs no extra API
 * calls or backend storage. */
function searchLinks(title: string, artist: string) {
  const q = encodeURIComponent(`${artist} ${title}`);
  return {
    spotify: `https://open.spotify.com/search/${q}`,
    youtube: `https://www.youtube.com/results?search_query=${q}`,
    appleMusic: `https://music.apple.com/search?term=${q}`,
  };
}

const PLATFORMS = [
  { key: "spotify", label: "Spotify", Icon: SiSpotify, hoverColor: "#1DB954" },
  { key: "youtube", label: "YouTube", Icon: SiYoutube, hoverColor: "#FF0000" },
  { key: "appleMusic", label: "Apple Music", Icon: SiApplemusic, hoverColor: "#FA586A" },
] as const;

export function ExternalSongLinks({
  title,
  artist,
  size = 13,
  className = "",
}: {
  title: string;
  artist: string;
  size?: number;
  className?: string;
}) {
  const links = searchLinks(title, artist);
  return (
    <div className={`flex items-center gap-2.5 ${className}`} onClick={(e) => e.stopPropagation()}>
      {PLATFORMS.map(({ key, label, Icon, hoverColor }) => (
        <a
          key={key}
          href={links[key]}
          target="_blank"
          rel="noreferrer noopener"
          aria-label={`Find "${title}" by ${artist} on ${label}`}
          title={`Find on ${label}`}
          className="text-[var(--text-tertiary)] transition-colors"
          style={{ ["--hover" as string]: hoverColor }}
          onMouseEnter={(e) => (e.currentTarget.style.color = hoverColor)}
          onMouseLeave={(e) => (e.currentTarget.style.color = "")}
        >
          <Icon size={size} />
        </a>
      ))}
    </div>
  );
}
