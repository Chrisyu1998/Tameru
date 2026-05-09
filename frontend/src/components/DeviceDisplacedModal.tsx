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
      // user from the modal. clearSession() also wipes the JWT so the
      // /signin page renders cleanly without a stale identity flicker.
      useAppStore.getState().clearSession();
      useAppStore.getState().setDisplaced(false);
      window.location.replace('/signin');
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim px-6"
    >
      <div className="flex w-full max-w-sm flex-col gap-5 rounded-2xl bg-elevated p-6">
        <h2 className="font-display text-2xl text-primary">
          you signed in on another device.
        </h2>
        <p className="font-sans text-sm text-secondary">
          this session has ended.
        </p>
        <button
          type="button"
          onClick={handleSignInAgain}
          className="rounded-2xl bg-accent-emphasis px-5 py-3 font-sans text-base text-elevated"
        >
          sign in again
        </button>
      </div>
    </div>
  );
}
