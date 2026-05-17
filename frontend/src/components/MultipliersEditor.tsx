import { useState } from "react";
import { Plus, X } from "lucide-react";
import { CATEGORIES } from "@/lib/categories";

/**
 * Editable list of category → multiplier rows with per-row remove and an
 * add affordance. Shared between the onboarding "add card" preview and
 * the post-add "edit card" sheet so manual fill-in works identically in
 * both surfaces.
 *
 * Closes Codex P2 (originally in AddCardStep): when lookup returns
 * `needs_manual=true` or no bonus categories, the user previously had no
 * way to add multipliers; saving used to land a card with empty
 * `multipliers`, breaking reward recommendations downstream. With this
 * component, the manual-fill path is symmetrical — the user can add,
 * edit, and remove rows regardless of whether the auto-lookup populated
 * anything.
 *
 * Category options come from `CATEGORIES` (Tameru's closed enum). Tameru's
 * spend categories don't map 1:1 onto a bank's bonus categories ("Dining"
 * does, "U.S. supermarkets" doesn't), so the picker also accepts a
 * free-form "custom…" entry that becomes the row key. The backend stores
 * multipliers as `Record<string, number>` — no enum constraint on the
 * key — so a custom label is round-trip safe.
 */
export function MultipliersEditor(props: {
  multipliers: Record<string, number>;
  onMultipliers: (m: Record<string, number>) => void;
  needsManual?: boolean;
}) {
  const [adding, setAdding] = useState(false);
  const [draftCat, setDraftCat] = useState<string>(CATEGORIES[0]);
  const [draftCustom, setDraftCustom] = useState("");
  const [draftFactor, setDraftFactor] = useState("");

  const entries = Object.entries(props.multipliers);
  const usedKeys = new Set(entries.map(([k]) => k));

  const commitDraft = () => {
    const factor = parseFloat(draftFactor);
    if (!Number.isFinite(factor) || factor <= 0) return;
    const key = draftCat === "__custom" ? draftCustom.trim() : draftCat;
    if (!key) return;
    props.onMultipliers({ ...props.multipliers, [key]: factor });
    setDraftCat(CATEGORIES[0]);
    setDraftCustom("");
    setDraftFactor("");
    setAdding(false);
  };

  const removeRow = (key: string) => {
    const next = { ...props.multipliers };
    delete next[key];
    props.onMultipliers(next);
  };

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between">
        <div className="text-xs text-ink-tertiary">multipliers</div>
        {props.needsManual && entries.length === 0 && (
          <span className="text-[0.7rem] text-amber-deep">
            add your card's bonus categories below
          </span>
        )}
      </div>

      {entries.length === 0 && !adding && (
        <p className="mt-1 text-xs text-ink-quaternary">
          no bonus categories yet.
        </p>
      )}

      {entries.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {entries.map(([cat, val]) => (
            <li key={cat} className="flex items-center gap-2">
              <span className="flex-1 text-sm text-ink">{cat}</span>
              <input
                inputMode="decimal"
                value={String(val)}
                onChange={(e) => {
                  const n = parseFloat(e.target.value);
                  if (!Number.isFinite(n) || n <= 0) return;
                  props.onMultipliers({ ...props.multipliers, [cat]: n });
                }}
                className="w-16 rounded-lg border border-hairline bg-surface px-2 py-1 text-right text-sm text-ink focus:outline-none"
              />
              <button
                type="button"
                aria-label={`remove ${cat}`}
                onClick={() => removeRow(cat)}
                className="rounded-lg p-1 text-ink-tertiary hover:bg-sunken/60 hover:text-ink"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {adding ? (
        <div className="mt-2 flex flex-col gap-2 rounded-xl border border-hairline bg-surface px-3 py-2">
          <div className="flex items-center gap-2">
            <select
              value={draftCat}
              onChange={(e) => setDraftCat(e.target.value)}
              className="flex-1 rounded-lg border border-hairline bg-elevated px-2 py-1 text-sm text-ink focus:outline-none"
            >
              {CATEGORIES.filter((c) => !usedKeys.has(c)).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
              <option value="__custom">custom…</option>
            </select>
            <input
              inputMode="decimal"
              placeholder="3"
              value={draftFactor}
              onChange={(e) =>
                setDraftFactor(e.target.value.replace(/[^\d.]/g, ""))
              }
              className="w-16 rounded-lg border border-hairline bg-elevated px-2 py-1 text-right text-sm text-ink focus:outline-none"
            />
          </div>
          {draftCat === "__custom" && (
            <input
              type="text"
              maxLength={48}
              placeholder='e.g. "U.S. supermarkets"'
              value={draftCustom}
              onChange={(e) => setDraftCustom(e.target.value)}
              className="rounded-lg border border-hairline bg-elevated px-2 py-1 text-sm text-ink focus:outline-none"
            />
          )}
          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setAdding(false);
                setDraftCustom("");
                setDraftFactor("");
              }}
              className="text-xs text-ink-tertiary hover:text-ink"
            >
              cancel
            </button>
            <button
              type="button"
              onClick={commitDraft}
              disabled={
                !Number.isFinite(parseFloat(draftFactor)) ||
                parseFloat(draftFactor) <= 0 ||
                (draftCat === "__custom" && !draftCustom.trim())
              }
              className="rounded-lg bg-moss px-2.5 py-1 text-xs font-medium text-surface disabled:bg-moss/40"
            >
              add
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="mt-2 inline-flex items-center gap-1 text-xs text-moss-deep hover:text-moss"
        >
          <Plus className="h-3 w-3" /> add category
        </button>
      )}
    </div>
  );
}
