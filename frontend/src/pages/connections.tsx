import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Copy, ExternalLink, RefreshCcw } from "lucide-react";
import { BottomSheet } from "@/components/BottomSheet";
import { supabase } from "@/lib/supabase";
import { cn } from "@/lib/utils";

/*
 * Day 23b — Connected apps.
 *
 * Lists OAuth grants the user has approved (Claude.ai web, Claude Code,
 * Claude Desktop, anything MCP-capable) and lets them disconnect. The
 * data source is `supabase.auth.oauth.listGrants()` — called with the
 * user's session JWT, no FastAPI bridge (DESIGN.md §7.9, CLAUDE.md
 * invariant 1 untouched).
 *
 * Disconnect calls `supabase.auth.oauth.revokeGrant({clientId})`, which
 * deletes the session + invalidates the refresh token at Supabase. The
 * access JWT the client already holds remains valid until its `exp`
 * (≤5min under our `JWT expiry limit = 300s` setting — see
 * supabase/MCP_OAUTH_SETUP.md). The UI copy reflects this honestly.
 */

type Grant = {
  clientId: string;
  clientName: string;
  clientUri: string | null;
  grantedAt: string;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; grants: Grant[] }
  | { kind: "error"; message: string };

const MCP_PATH = "/mcp";

export default function ConnectionsPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [pendingDisconnect, setPendingDisconnect] = useState<Grant | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    const { data, error } = await supabase.auth.oauth.listGrants();
    if (error) {
      setState({
        kind: "error",
        message: error.message || "couldn't load connected apps.",
      });
      return;
    }
    const grants: Grant[] = (data ?? []).map((g) => ({
      clientId: g.client.id,
      clientName: g.client.name,
      clientUri: g.client.uri || null,
      grantedAt: g.granted_at,
    }));
    setState({ kind: "ready", grants });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const confirmDisconnect = async () => {
    if (pendingDisconnect === null || disconnecting) return;
    setDisconnecting(true);
    const { error } = await supabase.auth.oauth.revokeGrant({
      clientId: pendingDisconnect.clientId,
    });
    setDisconnecting(false);
    if (error) {
      // Surface the failure into the list state so the user sees a real
      // message rather than a silently-dismissed sheet.
      setState({
        kind: "error",
        message: error.message || "couldn't disconnect that app.",
      });
      setPendingDisconnect(null);
      return;
    }
    setPendingDisconnect(null);
    await load();
  };

  return (
    <div className="mx-auto w-full max-w-2xl px-5 pt-8 pb-20">
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          connected apps
        </h1>
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-ink-secondary">
          apps you've allowed to read your spending — like{" "}
          <span className="text-ink">claude.ai</span>. access is{" "}
          <span className="text-ink">read-only</span>: connected apps can
          see your ledger but can't add, edit, or delete anything.
        </p>
      </header>

      <SetupInstructions />

      <section className="mt-10 border-t border-hairline pt-5">
        <div className="flex items-center justify-between">
          <h2 className="text-[0.78rem] uppercase tracking-wider text-ink-tertiary">
            current connections
          </h2>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1 text-[0.78rem] text-ink-tertiary hover:text-ink"
            aria-label="refresh"
          >
            <RefreshCcw className="h-3.5 w-3.5" /> refresh
          </button>
        </div>

        <GrantList
          state={state}
          onDisconnect={(grant) => setPendingDisconnect(grant)}
        />
      </section>

      <DisconnectSheet
        grant={pendingDisconnect}
        busy={disconnecting}
        onCancel={() => (disconnecting ? undefined : setPendingDisconnect(null))}
        onConfirm={confirmDisconnect}
      />
    </div>
  );
}

function GrantList({
  state,
  onDisconnect,
}: {
  state: LoadState;
  onDisconnect: (grant: Grant) => void;
}) {
  if (state.kind === "loading") {
    return (
      <p className="mt-4 text-sm text-ink-tertiary">loading connections…</p>
    );
  }
  if (state.kind === "error") {
    return (
      <div
        role="alert"
        className="mt-4 rounded-2xl border border-hairline bg-warn-wash px-3 py-2.5"
      >
        <p className="text-sm leading-snug text-ink">{state.message}</p>
      </div>
    );
  }
  if (state.grants.length === 0) {
    return (
      <p className="mt-4 text-sm text-ink-tertiary">
        no apps connected yet.
      </p>
    );
  }
  return (
    <ul className="mt-3 divide-y divide-hairline">
      {state.grants.map((g) => (
        <li
          key={g.clientId}
          className="flex items-center justify-between gap-3 py-3"
        >
          <div className="min-w-0 flex-1">
            <p className="truncate text-[0.95rem] text-ink">{g.clientName}</p>
            <p className="text-[0.75rem] text-ink-tertiary">
              {formatGrantedAt(g.grantedAt)}
            </p>
          </div>
          <button
            type="button"
            onClick={() => onDisconnect(g)}
            className="text-[0.85rem] font-medium text-over hover:underline"
          >
            disconnect
          </button>
        </li>
      ))}
    </ul>
  );
}

function SetupInstructions() {
  const [copied, setCopied] = useState(false);
  const mcpUrl = resolveMcpUrl();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(mcpUrl);
    } catch {
      // ignore — user can still select manually
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  return (
    <section className="mt-7 rounded-2xl border border-hairline bg-surface px-4 py-4">
      <p className="text-[0.78rem] uppercase tracking-wider text-ink-tertiary">
        add tameru in claude.ai
      </p>
      <ol className="mt-2 space-y-1.5 text-[0.85rem] leading-relaxed text-ink-secondary">
        <li>
          1. in claude.ai, open <span className="text-ink">settings → connectors</span>
          {" "}and choose <span className="text-ink">add custom connector</span>.
        </li>
        <li>
          2. paste this server url:
        </li>
      </ol>
      <div className="mt-2 flex items-center gap-2 rounded-xl border border-hairline bg-sunken px-3 py-2.5">
        <p className="flex-1 truncate font-mono text-[0.82rem] text-ink">
          {mcpUrl}
        </p>
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1 text-[0.78rem] text-ink-secondary hover:text-ink"
        >
          <Copy className="h-3.5 w-3.5" /> {copied ? "copied" : "copy"}
        </button>
      </div>
      <p className="mt-3 text-[0.78rem] text-ink-tertiary">
        claude.ai will open a tameru consent page — approve to finish.
        nothing to paste back here.
      </p>
    </section>
  );
}

function DisconnectSheet({
  grant,
  busy,
  onCancel,
  onConfirm,
}: {
  grant: Grant | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <BottomSheet
      open={grant !== null}
      onClose={onCancel}
      ariaLabel="disconnect app"
    >
      {grant && (
        <div className="pb-2">
          <h2 className="font-serif text-2xl text-ink lowercase-title">
            disconnect {grant.clientName.toLowerCase()}?
          </h2>

          <div className="mt-4 flex items-start gap-2 rounded-xl bg-warn-wash px-3 py-2.5">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-warn" />
            <p className="text-[0.82rem] leading-snug text-ink-secondary">
              the app loses access within a few minutes. an in-flight
              session may stay readable until its token expires.
            </p>
          </div>

          {grant.clientUri && (
            <p className="mt-3 inline-flex items-center gap-1 text-[0.78rem] text-ink-tertiary">
              <ExternalLink className="h-3 w-3" />
              <span className="truncate">{safeHost(grant.clientUri)}</span>
            </p>
          )}

          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={cn(
              "mt-6 inline-flex h-12 w-full items-center justify-center rounded-2xl px-5 text-sm font-medium transition-colors",
              busy
                ? "bg-sunken text-ink-quaternary cursor-not-allowed"
                : "bg-over text-surface hover:opacity-90",
            )}
          >
            {busy ? "disconnecting…" : "disconnect"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="mt-2 inline-flex h-11 w-full items-center justify-center rounded-2xl border border-hairline bg-surface px-5 text-sm text-ink hover:bg-elevated disabled:cursor-not-allowed disabled:text-ink-quaternary"
          >
            cancel
          </button>
        </div>
      )}
    </BottomSheet>
  );
}

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------

/**
 * Resolve the public MCP endpoint URL the user pastes into Claude.ai.
 * Derived from VITE_API_URL (the repo's existing convention — see
 * `lib/api.ts` and `.env.example`) + `/mcp`. Don't hardcode `tameru.app`
 * — the real prod host is the Railway URL, dev is localhost
 * (DESIGN.md §10.1 deployment URLs).
 */
function resolveMcpUrl(): string {
  const base = (import.meta.env.VITE_API_URL as string | undefined) ?? "";
  if (!base) return MCP_PATH;
  return `${base.replace(/\/$/, "")}${MCP_PATH}`;
}

function formatGrantedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "connected";
  return `connected ${d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  })}`;
}

function safeHost(uri: string): string {
  try {
    return new URL(uri).host;
  } catch {
    return uri;
  }
}
