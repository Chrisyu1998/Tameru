import { useState } from 'react';

import { signInWithGoogle, signInWithMagicLink } from '../lib/auth';

/*
 * Sign-in screen (UX frame 3). Single primary action — Google. Magic link
 * is a "more options" disclosure; password sign-in is forbidden by the
 * Day 7 prompt and never gets a surface.
 *
 * After Google OAuth, Supabase redirects to `/` with the session in the
 * URL hash. Splash picks it up, calls /me, and dispatches to either
 * ConfirmHomeCurrency (new user) or claim_device → /home (returning user).
 */
export function SignIn() {
  const [moreOptions, setMoreOptions] = useState(false);
  const [email, setEmail] = useState('');
  const [magicSent, setMagicSent] = useState(false);
  const [busy, setBusy] = useState<'google' | 'magic' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleGoogle = async () => {
    setBusy('google');
    setError(null);
    try {
      await signInWithGoogle();
      // Supabase navigates the page to Google, so this resolves only on
      // failure (popup blocker, network error, etc.) — success unloads.
    } catch (err) {
      setBusy(null);
      setError(
        err instanceof Error ? err.message : 'Could not start Google sign-in.',
      );
    }
  };

  const handleMagic = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setBusy('magic');
    setError(null);
    try {
      await signInWithMagicLink(email.trim());
      setMagicSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send magic link.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="flex min-h-dvh items-center justify-center bg-canvas px-6">
      <div className="flex w-full max-w-sm flex-col gap-8">
        <div className="flex flex-col items-center gap-2">
          <h1 className="font-display text-4xl text-primary">tameru</h1>
          <p className="font-sans text-sm text-tertiary">
            spending intelligence, quietly.
          </p>
        </div>

        <button
          type="button"
          onClick={handleGoogle}
          disabled={busy !== null}
          className="rounded-2xl bg-accent-emphasis px-5 py-3 font-sans text-base text-elevated transition-opacity disabled:opacity-60"
        >
          {busy === 'google' ? 'opening google…' : 'sign in with Google'}
        </button>

        <div className="flex flex-col gap-3">
          <button
            type="button"
            onClick={() => setMoreOptions((v) => !v)}
            className="font-sans text-xs text-tertiary underline-offset-2 hover:underline"
          >
            {moreOptions ? 'less options' : 'more options'}
          </button>

          {moreOptions && !magicSent && (
            <form onSubmit={handleMagic} className="flex flex-col gap-2">
              <label className="font-sans text-xs text-secondary" htmlFor="email">
                email for magic link
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="rounded-xl border border-soft bg-surface px-3 py-2 font-sans text-sm text-primary outline-none focus:border-accent"
              />
              <button
                type="submit"
                disabled={busy !== null || !email.trim()}
                className="rounded-xl border border-soft bg-elevated px-3 py-2 font-sans text-sm text-primary disabled:opacity-60"
              >
                {busy === 'magic' ? 'sending…' : 'send magic link'}
              </button>
            </form>
          )}

          {magicSent && (
            <p className="font-sans text-xs text-secondary">
              we sent a sign-in link to {email}. open it on this device.
            </p>
          )}
        </div>

        {error && (
          <p role="alert" className="font-sans text-xs text-over">
            {error}
          </p>
        )}
      </div>
    </main>
  );
}
