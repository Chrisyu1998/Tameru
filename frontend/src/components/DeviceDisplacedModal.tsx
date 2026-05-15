import { signOut } from '../lib/auth';
import { useAppStore } from '../store';

/*
 * Full-screen displacement modal — DESIGN.md §9.1, Day 7 prompt.
 *
 * Latched in two places: api.ts on a 401 with `code === 'DEVICE_DISPLACED'`
 * (the per-request gate), and lib/auth.ts on a `is_active=false` from the
 * 60s /auth/check_device poll (the idle path). Once visible, the only
 * action is "Sign in again" — we explicitly clear the Supabase session,
 * unlatch the flag, and kick the browser back to /signin so the
 * subsequent flow is identical to a normal first sign-in.
 */
export function DeviceDisplacedModal() {
  const displaced = useAppStore((s) => s.displaced);
  if (!displaced) return null;

  const handleSignInAgain = async () => {
    try {
      await signOut();
    } finally {
      // Whether signOut hits the network or not, we want to release the
      // user from the modal. clearSession() wipes the JWT so the onboarding
      // gate renders cleanly without a stale identity flicker.
      useAppStore.getState().clearSession();
      useAppStore.getState().setDisplaced(false);
      // The dedicated /signin page is gone (step 1 import collapsed it into
      // the onboarding wizard); / is the canonical sign-in entry now.
      window.location.replace('/');
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim px-6"
    >
      <div className="flex w-full max-w-sm flex-col gap-5 rounded-2xl bg-elevated p-6">
        <h2 className="font-serif text-2xl text-ink lowercase-title">
          you signed in on another device.
        </h2>
        <p className="text-sm text-ink-secondary">
          this session has ended.
        </p>
        <button
          type="button"
          onClick={handleSignInAgain}
          className="rounded-2xl bg-moss-deep px-5 py-3 text-base text-surface hover:bg-moss"
        >
          sign in again
        </button>
      </div>
    </div>
  );
}
