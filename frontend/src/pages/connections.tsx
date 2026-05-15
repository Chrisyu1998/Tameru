import { useState } from "react";
import { AlertTriangle, Check, Copy } from "lucide-react";
import { BottomSheet } from "@/components/BottomSheet";
import {
  formatLastUsed,
  generateTokenSecret,
  initialTokens,
  type ClaudeToken,
} from "@/lib/claudeTokens";
import { cn } from "@/lib/utils";

export default function ConnectionsPage() {
  const [tokens, setTokens] = useState<ClaudeToken[]>(initialTokens);
  const [name, setName] = useState("");
  const [revealed, setRevealed] = useState<{
    secret: string;
    name: string;
  } | null>(null);

  const canGenerate = name.trim().length > 0;

  const handleGenerate = () => {
    if (!canGenerate) return;
    const secret = generateTokenSecret();
    const tok: ClaudeToken = {
      id: `tok-${Date.now()}`,
      name: name.trim(),
      lastUsedAt: null,
      createdAt: new Date().toISOString(),
    };
    setTokens((prev) => [tok, ...prev]);
    setRevealed({ secret, name: tok.name });
    setName("");
  };

  const revoke = (id: string) => {
    setTokens((prev) => prev.filter((t) => t.id !== id));
  };

  return (
    <div className="mx-auto w-full max-w-2xl px-5 pt-8 pb-20">
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          claude connections
        </h1>
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-ink-secondary">
          generate a token to ask claude about your spending — from{" "}
          <span className="text-ink">claude.ai</span> or{" "}
          <span className="text-ink">claude code</span>. tokens are{" "}
          <span className="text-ink">read-only</span>: claude can see your
          ledger but can't add, edit, or delete anything.
        </p>
      </header>

      {/* Generate form */}
      <section className="mt-7 rounded-2xl border border-hairline bg-surface px-4 py-4">
        <label
          htmlFor="token-name"
          className="block text-[0.78rem] uppercase tracking-wider text-ink-tertiary"
        >
          name this token
        </label>
        <input
          id="token-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. claude.ai laptop"
          className="mt-1.5 block w-full bg-transparent text-[0.95rem] text-ink placeholder:text-ink-quaternary focus:outline-none"
        />
        <button
          type="button"
          onClick={handleGenerate}
          disabled={!canGenerate}
          className={cn(
            "mt-4 inline-flex h-11 items-center justify-center rounded-2xl px-5 text-sm font-medium transition-colors",
            canGenerate
              ? "bg-moss text-surface hover:bg-moss-deep"
              : "bg-sunken text-ink-quaternary cursor-not-allowed"
          )}
        >
          generate token
        </button>
      </section>

      {/* Existing tokens */}
      <div className="mt-10 border-t border-hairline pt-5">
        <h2 className="text-[0.78rem] uppercase tracking-wider text-ink-tertiary">
          existing tokens
        </h2>
        {tokens.length === 0 ? (
          <p className="mt-4 text-sm text-ink-tertiary">no tokens yet.</p>
        ) : (
          <ul className="mt-3 divide-y divide-hairline">
            {tokens.map((t) => (
              <li
                key={t.id}
                className="flex items-center justify-between gap-3 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[0.95rem] text-ink">{t.name}</p>
                  <p className="text-[0.75rem] text-ink-tertiary">
                    {formatLastUsed(t.lastUsedAt)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => revoke(t.id)}
                  className="text-[0.85rem] font-medium text-over hover:underline"
                >
                  revoke
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <RevealSheet
        revealed={revealed}
        onConfirmCopied={() => setRevealed(null)}
      />
    </div>
  );
}

function RevealSheet({
  revealed,
  onConfirmCopied,
}: {
  revealed: { secret: string; name: string } | null;
  onConfirmCopied: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!revealed) return;
    try {
      await navigator.clipboard.writeText(revealed.secret);
    } catch {
      // ignore — user can still select manually
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  return (
    <BottomSheet
      open={revealed !== null}
      // Block all dismiss paths until the user confirms they've copied it.
      onClose={() => {}}
      blockDismiss
      ariaLabel="token generated"
    >
      {revealed && (
        <div className="pb-2">
          <h2 className="font-serif text-2xl text-ink lowercase-title">
            token generated
          </h2>
          <p className="mt-1 text-[0.8rem] text-ink-tertiary">
            for <span className="text-ink-secondary">{revealed.name}</span>
          </p>

          <div className="mt-4 flex items-start gap-2 rounded-xl bg-warn-wash px-3 py-2.5">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-warn" />
            <p className="text-[0.82rem] leading-snug text-ink-secondary">
              copy this now — you won't be able to see it again.
            </p>
          </div>

          <div className="mt-4 rounded-xl border border-hairline bg-sunken px-3 py-3">
            <p className="break-all font-mono text-[0.82rem] leading-relaxed text-ink">
              {revealed.secret}
            </p>
          </div>

          <button
            type="button"
            onClick={copy}
            className={cn(
              "mt-3 inline-flex h-10 items-center gap-2 rounded-2xl border px-4 text-sm font-medium transition-colors",
              copied
                ? "border-moss bg-moss-wash text-moss-deep"
                : "border-hairline bg-surface text-ink hover:bg-elevated"
            )}
          >
            {copied ? (
              <>
                <Check className="h-4 w-4" /> copied
              </>
            ) : (
              <>
                <Copy className="h-4 w-4" /> copy token
              </>
            )}
          </button>

          <p className="mt-4 text-[0.78rem] text-ink-tertiary">
            lost it? revoke this token and generate a new one.
          </p>

          <div className="mt-5 rounded-xl border border-hairline bg-surface px-3 py-3">
            <p className="text-[0.72rem] uppercase tracking-wider text-ink-tertiary">
              setup
            </p>
            <ol className="mt-1.5 space-y-1 text-[0.78rem] leading-relaxed text-ink-tertiary">
              <li>
                1. in claude.ai or claude code, add a new mcp connection.
              </li>
              <li>
                2. server url:{" "}
                <span className="font-mono text-ink-secondary">
                  https://api.tameru.app/mcp
                </span>
              </li>
              <li>3. paste the token above as the bearer credential.</li>
            </ol>
          </div>

          <button
            type="button"
            onClick={onConfirmCopied}
            className="mt-6 inline-flex h-12 w-full items-center justify-center rounded-2xl bg-moss px-5 text-sm font-medium text-surface hover:bg-moss-deep"
          >
            i've copied this — done
          </button>
        </div>
      )}
    </BottomSheet>
  );
}
