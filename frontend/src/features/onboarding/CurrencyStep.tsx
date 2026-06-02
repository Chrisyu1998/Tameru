import { useState } from "react";
import { ChevronDown, Lock, Check } from "lucide-react";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { BottomSheet } from "@/components/BottomSheet";
import { CURRENCIES, type Currency } from "./types";
import { bootstrap, detectDefaultCurrency, getOrCreateDeviceId } from "@/lib/auth";
import { useAppStore } from "@/store";

interface CurrencyStepProps {
  /**
   * Called after /auth/bootstrap returns successfully. The wizard advances
   * to its visual onboarding follow-ups (add-card, csv import) — the user
   * is already onboarded server-side once this fires.
   */
  onConfirm: (currency: Currency) => void;
}

export function CurrencyStep({ onConfirm }: CurrencyStepProps) {
  const [selected, setSelected] = useState<Currency>(
    () => detectDefaultCurrency() as Currency,
  );
  const [sheetOpen, setSheetOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const deviceId = getOrCreateDeviceId();
      const res = await bootstrap(deviceId, selected);
      useAppStore.getState().setHomeCurrency(res.home_currency);
      // Day 29 Tier 2: bootstrap snapshotted the browser language; mirror it
      // into the store so the rest of onboarding renders in that language
      // without waiting on a /me round trip.
      useAppStore.getState().setUiLanguage(res.ui_language);
      onConfirm(selected);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not set your currency.");
    } finally {
      setBusy(false);
    }
  };

  const selectedOption = CURRENCIES.find((c) => c.code === selected)!;

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col px-6 pb-10 pt-20 animate-fade-up">
      <div className="flex items-center gap-2 text-xs text-ink-tertiary">
        <Lock className="h-3 w-3" />
        <span className="lowercase tracking-wider">irreversible</span>
      </div>

      <h1 className="mt-3 font-serif text-3xl text-ink lowercase-title">
        your home currency
      </h1>
      <p className="mt-2 text-sm text-ink-secondary">
        the lens through which everything is counted.
      </p>

      <button
        type="button"
        onClick={() => setSheetOpen(true)}
        className="mt-8 flex w-full items-center justify-between rounded-2xl border border-hairline bg-elevated px-5 py-4 text-left transition-colors hover:bg-surface"
      >
        <div className="flex items-center gap-3">
          <span className="font-serif text-2xl text-moss-deep tabular">
            {selectedOption.symbol}
          </span>
          <div className="flex flex-col leading-tight">
            <span className="text-base text-ink">{selectedOption.code}</span>
            <span className="text-xs text-ink-tertiary">{selectedOption.name}</span>
          </div>
        </div>
        <ChevronDown className="h-4 w-4 text-ink-tertiary" />
      </button>

      <Card variant="elevated" className="mt-6 border-warn/20">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-warn-wash text-warn">
            <Lock className="h-3 w-3" />
          </div>
          <div className="flex flex-col gap-2">
            <p className="font-serif text-sm text-ink lowercase-title">
              once set, your home currency stays
            </p>
            <ul className="flex flex-col gap-1.5 text-[0.85rem] leading-relaxed text-ink-secondary">
              <li className="flex gap-2">
                <span className="text-ink-quaternary">·</span>
                <span>
                  every transaction, in any currency, is converted into this one
                  for your totals.
                </span>
              </li>
              <li className="flex gap-2">
                <span className="text-ink-quaternary">·</span>
                <span>
                  changing it later would invalidate every historical figure —
                  so we don't allow it.
                </span>
              </li>
            </ul>
          </div>
        </div>
      </Card>

      <div className="flex-1" />

      {error && <p className="mt-6 text-center text-xs text-over">{error}</p>}

      <Button
        fullWidth
        size="lg"
        className="mt-4"
        onClick={() => void handleConfirm()}
        disabled={busy}
      >
        {busy ? "setting…" : `I understand — set ${selected}`}
      </Button>

      <BottomSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        ariaLabel="choose home currency"
      >
        <h2 className="font-serif text-xl text-ink lowercase-title">
          choose your home currency
        </h2>
        <ul className="mt-4 flex flex-col">
          {CURRENCIES.map((c) => {
            const isActive = c.code === selected;
            return (
              <li key={c.code}>
                <button
                  type="button"
                  onClick={() => {
                    setSelected(c.code);
                    setSheetOpen(false);
                  }}
                  className="flex w-full items-center justify-between rounded-xl px-2 py-3 text-left transition-colors hover:bg-sunken/60"
                >
                  <div className="flex items-center gap-3">
                    <span className="w-7 font-serif text-lg text-moss-deep tabular">
                      {c.symbol}
                    </span>
                    <div className="flex flex-col leading-tight">
                      <span className="text-base text-ink">{c.code}</span>
                      <span className="text-xs text-ink-tertiary">{c.name}</span>
                    </div>
                  </div>
                  {isActive && <Check className="h-4 w-4 text-moss" />}
                </button>
              </li>
            );
          })}
        </ul>
      </BottomSheet>
    </div>
  );
}
