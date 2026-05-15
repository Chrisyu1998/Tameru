/** Mock store of Claude Connection tokens — in-memory only. */

export interface ClaudeToken {
  id: string;
  name: string;
  /** ISO date string of last use, or null if never used. */
  lastUsedAt: string | null;
  createdAt: string;
}

export const initialTokens: ClaudeToken[] = [
  {
    id: "tok-1",
    name: "claude.ai laptop",
    lastUsedAt: "2025-04-22T14:18:00Z",
    createdAt: "2025-03-04T10:00:00Z",
  },
  {
    id: "tok-2",
    name: "claude code · work",
    lastUsedAt: "2025-04-25T09:42:00Z",
    createdAt: "2025-04-01T11:30:00Z",
  },
];

/** Generate a fake but plausible-looking secret. */
export function generateTokenSecret(): string {
  const part = () =>
    Math.random()
      .toString(36)
      .slice(2, 10)
      .padEnd(8, "x");
  return `tmru_live_${part()}${part()}${part()}${part()}`;
}

export function formatLastUsed(iso: string | null): string {
  if (!iso) return "never used";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const day = 24 * 60 * 60 * 1000;
  if (diffMs < day) return "used today";
  if (diffMs < 2 * day) return "used yesterday";
  if (diffMs < 7 * day) return `used ${Math.floor(diffMs / day)} days ago`;
  return `last used ${d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  })}`;
}
