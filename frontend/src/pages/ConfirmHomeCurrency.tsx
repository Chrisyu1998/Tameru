import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  ALLOWED_CURRENCIES,
  AllowedCurrency,
  bootstrap,
  detectDefaultCurrency,
  getOrCreateDeviceId,
} from '../lib/auth';

/*
 * Onboarding step that sits between sign-in and Add First Card (UX frame 4).
 * Renders only when `/me.home_currency` is null (Splash dispatches here).
 *
 * Currency is immutable once set (CLAUDE.md invariant 13). The only place
 * it can be chosen in the entire product is this screen — by design, no
 * settings surface offers a change. The copy makes the "you cannot change
 * this" stance loud, and the picker defaults to the locale-derived choice
 * but always requires a "continue" tap so the decision is deliberate.
 */
export function ConfirmHomeCurrency() {
  const navigate = useNavigate();
  const initial = useMemo(() => detectDefaultCurrency(), []);
  const [currency, setCurrency] = useState<AllowedCurrency>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleContinue = async () => {
    setBusy(true);
    setError(null);
    try {
      await bootstrap(getOrCreateDeviceId(), currency);
      // Day 8 lands the Add First Card flow on /onboarding/card. Until then,
      // /home is the natural landing — the dashboard placeholder.
      navigate('/home', { replace: true });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not save your currency.',
      );
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-dvh items-center justify-center bg-canvas px-6">
      <div className="flex w-full max-w-sm flex-col gap-6">
        <div className="flex flex-col gap-2">
          <h1 className="font-display text-3xl text-primary">
            your home currency
          </h1>
          <p className="font-sans text-sm text-secondary">
            this cannot be changed later.
          </p>
          <p className="font-sans text-xs text-tertiary">
            all your spending stays in this currency; for trips abroad, enter
            the amount that shows on your card statement.
          </p>
        </div>

        <label className="flex flex-col gap-2">
          <span className="font-sans text-xs text-secondary">currency</span>
          <select
            value={currency}
            onChange={(e) => setCurrency(e.target.value as AllowedCurrency)}
            className="rounded-xl border border-soft bg-surface px-3 py-2 font-sans text-base text-primary outline-none focus:border-accent"
          >
            {ALLOWED_CURRENCIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          onClick={handleContinue}
          disabled={busy}
          className="rounded-2xl bg-accent-emphasis px-5 py-3 font-sans text-base text-elevated disabled:opacity-60"
        >
          {busy ? 'saving…' : 'continue'}
        </button>

        {error && (
          <p role="alert" className="font-sans text-xs text-over">
            {error}
          </p>
        )}
      </div>
    </main>
  );
}
