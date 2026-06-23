import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { claimDevice, signOut } from '../lib/auth';
import { useAppStore } from '../store';

/*
 * Full-screen displacement modal — DESIGN.md §9.1, Day 7 prompt.
 *
 * Latched in two places: api.ts on a 401 with `code === 'DEVICE_DISPLACED'`
 * (the per-request gate), and lib/auth.ts on a `is_active=false` from the
 * 60s /auth/check_device poll (the idle path). Two exits, both explicit
 * user intent (audit P2-2 — claims are no longer a side effect of token
 * refreshes, so this modal is the ONLY way a displaced device becomes
 * active again short of a fresh sign-in):
 *   - "use here": claim this device back, unlatch, reload so every
 *     surface refetches under the re-claimed slot.
 *   - "sign in again": clear the Supabase session, unlatch, back to the
 *     sign-in entry — identical to a normal first sign-in.
 */
export function DeviceDisplacedModal() {
  const { t } = useTranslation();
  const displaced = useAppStore((s) => s.displaced);
  const [claiming, setClaiming] = useState(false);
  if (!displaced) return null;

  const handleUseHere = async () => {
    setClaiming(true);
    try {
      const { deviceId } = useAppStore.getState();
      if (!deviceId) throw new Error('no device id');
      await claimDevice(deviceId);
      useAppStore.getState().setDisplaced(false);
      // Full reload: every mounted surface refetches under the
      // re-claimed device slot instead of holding 401-poisoned state.
      window.location.reload();
    } catch {
      // Claim failed (network, or the JWT itself is gone) — leave the
      // modal up; "sign in again" remains the way out.
      setClaiming(false);
    }
  };

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
          {t("common.displaced.title")}
        </h2>
        <p className="text-sm text-ink-secondary">
          {t("common.displaced.body")}
        </p>
        <button
          type="button"
          onClick={handleUseHere}
          disabled={claiming}
          className="rounded-2xl bg-moss-deep px-5 py-3 text-base text-surface hover:bg-moss disabled:opacity-60"
        >
          {t("common.displaced.useHere")}
        </button>
        <button
          type="button"
          onClick={handleSignInAgain}
          className="rounded-2xl border border-hairline bg-surface px-5 py-3 text-base text-ink hover:bg-sunken"
        >
          {t("common.displaced.signInAgain")}
        </button>
      </div>
    </div>
  );
}
