import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

import { claimDevice, fetchMe, getOrCreateDeviceId } from '../lib/auth';
import { useAppStore } from '../store';

/*
 * Day 7 — splash + post-auth dispatcher.
 *
 *   no jwt           → /signin
 *   jwt, no currency → /confirm-currency  (new user, hasn't bootstrapped yet)
 *   jwt + currency   → claim_device, then /home
 *
 * The actual splash UI is the visible artifact while the dispatch runs;
 * it doubles as a placeholder for the Day 21 first-launch philosophy
 * screen. We stay on this route on the OAuth-callback hop, where Supabase
 * detects the URL hash and surfaces the new session via onAuthStateChange.
 */
export function Splash() {
  const navigate = useNavigate();
  const jwt = useAppStore((s) => s.jwt);
  const ranRef = useRef(false);

  useEffect(() => {
    if (jwt === null) {
      navigate('/signin', { replace: true });
      return;
    }

    // Re-running the dispatch on a token refresh would re-call /me and
    // claim_device for no benefit; the ref guards against that.
    if (ranRef.current) return;
    ranRef.current = true;

    let cancelled = false;
    (async () => {
      try {
        const me = await fetchMe();
        if (cancelled) return;
        if (me.home_currency === null) {
          navigate('/confirm-currency', { replace: true });
        } else {
          await claimDevice(getOrCreateDeviceId());
          if (cancelled) return;
          navigate('/home', { replace: true });
        }
      } catch {
        // /me failing while we have a JWT means either the backend is
        // down or the JWT is stale in a way our local check can't see.
        // Drop to /signin and let the user start over rather than wedge
        // them on a blank splash.
        if (!cancelled) navigate('/signin', { replace: true });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [jwt, navigate]);

  return (
    <main className="flex min-h-dvh items-center justify-center bg-canvas px-6">
      <div className="flex flex-col items-center gap-3">
        <h1 className="font-display text-5xl text-primary">tameru</h1>
        <p className="font-sans text-sm text-tertiary">
          spending intelligence, quietly.
        </p>
      </div>
    </main>
  );
}
