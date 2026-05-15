import { useState } from "react";
import { Mail } from "lucide-react";
import { Button } from "@/components/Button";
import { signInWithGoogle, signInWithMagicLink } from "@/lib/auth";

interface SignInStepProps {
  /**
   * Unused once the real auth flow is wired: Google OAuth navigates away
   * from the page, and a magic-link landing redirects back to / where the
   * onboarding gate picks the user up at the currency step. The prop is
   * kept so the wizard's existing call sites compile unchanged.
   */
  onContinue?: () => void;
}

function GoogleLogo() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true">
      <path
        fill="#EA4335"
        d="M12 10.2v3.9h5.5c-.2 1.3-1.6 3.9-5.5 3.9-3.3 0-6-2.7-6-6s2.7-6 6-6c1.9 0 3.1.8 3.8 1.5l2.6-2.5C16.7 3.4 14.6 2.4 12 2.4 6.7 2.4 2.4 6.7 2.4 12s4.3 9.6 9.6 9.6c5.5 0 9.2-3.9 9.2-9.4 0-.6-.1-1.1-.1-1.6H12z"
      />
    </svg>
  );
}

type Mode = "buttons" | "email" | "sent";

export function SignInStep(_props: SignInStepProps) {
  const [mode, setMode] = useState<Mode>("buttons");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleGoogle = async () => {
    setBusy(true);
    setError(null);
    try {
      await signInWithGoogle();
    } catch (e) {
      setBusy(false);
      setError(e instanceof Error ? e.message : "could not start sign-in.");
    }
  };

  const handleMagicLink = async () => {
    const trimmed = email.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      await signInWithMagicLink(trimmed);
      setMode("sent");
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not send the link.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col px-6 pb-10 pt-24 animate-fade-up">
      <h1 className="font-serif text-3xl text-ink lowercase-title">sign in</h1>
      <p className="mt-2 text-sm text-ink-tertiary">
        we'll keep your ledger between us.
      </p>

      {mode === "sent" ? (
        <div className="mt-12 flex flex-col gap-4 rounded-2xl border border-hairline bg-elevated p-5">
          <p className="font-serif text-lg text-ink lowercase-title">
            check your email
          </p>
          <p className="text-sm text-ink-secondary">
            we sent a sign-in link to{" "}
            <span className="text-ink">{email}</span>. open it on this device.
          </p>
          <button
            type="button"
            onClick={() => {
              setMode("email");
              setError(null);
            }}
            className="self-start text-xs text-ink-tertiary hover:text-ink-secondary"
          >
            use a different email
          </button>
        </div>
      ) : (
        <div className="mt-12 flex flex-col gap-4">
          <button
            type="button"
            onClick={handleGoogle}
            disabled={busy}
            className="inline-flex h-12 w-full items-center justify-center gap-3 rounded-2xl border border-hairline bg-elevated text-[0.95rem] font-medium text-ink transition-colors hover:bg-surface disabled:opacity-60"
          >
            <GoogleLogo />
            continue with Google
          </button>

          <div className="flex items-center gap-3 py-1 text-xs text-ink-quaternary">
            <span className="h-px flex-1 bg-hairline" />
            <span className="lowercase tracking-wider">or</span>
            <span className="h-px flex-1 bg-hairline" />
          </div>

          {mode === "buttons" ? (
            <Button
              variant="secondary"
              fullWidth
              onClick={() => setMode("email")}
              disabled={busy}
            >
              <Mail className="h-4 w-4" />
              continue with email
            </Button>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void handleMagicLink();
              }}
              className="flex flex-col gap-3"
            >
              <input
                type="email"
                autoFocus
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                disabled={busy}
                className="h-12 w-full rounded-2xl border border-hairline bg-elevated px-4 text-[0.95rem] text-ink placeholder:text-ink-quaternary focus:border-moss focus:outline-none disabled:opacity-60"
              />
              <Button
                type="submit"
                fullWidth
                disabled={busy || email.trim().length === 0}
              >
                <Mail className="h-4 w-4" />
                {busy ? "sending…" : "send sign-in link"}
              </Button>
            </form>
          )}

          {error && <p className="mt-1 text-xs text-over">{error}</p>}
        </div>
      )}

      <div className="flex-1" />

      <p className="mt-12 text-center text-[0.7rem] leading-relaxed text-ink-quaternary">
        by continuing you agree to our{" "}
        <span className="underline-offset-2 hover:underline">terms</span> and{" "}
        <span className="underline-offset-2 hover:underline">privacy notice</span>.
        we never sell your data.
      </p>
    </div>
  );
}
