import { useEffect, useState } from "react";
import { Loader2, Plus, Search, X } from "lucide-react";
import { Button } from "@/components/Button";
import { CATEGORIES } from "@/lib/categories";
import { StepDots } from "./StepDots";
import {
  confirmCard,
  isActiveCardExistsError,
  lookupCard,
  type CardLookupResult,
  type CardNetwork,
  type CardProgram,
} from "@/lib/cardsApi";

/*
 * Day 14 onboarding step — UX frame 4 "Add First Card."
 *
 * Two-stage flow:
 *   1. User enters name + network + last_four; suggestion chips set the name.
 *   2. We call POST /cards/lookup; the response renders an editable preview
 *      with multipliers; tap "add card" → POST /cards/confirm.
 *
 * 409 collision (DESIGN.md §8.1): if the user re-types a (network, last_four)
 * that's already active, the confirm endpoint returns active_card_exists and
 * we surface an inline banner pointing at the existing card. The "edit it
 * instead" deep-link is wired in cards.tsx (the post-onboarding surface).
 */

const SUGGESTIONS = ["Chase Sapphire Preferred", "Amex Gold", "Citi Double Cash"];

const NETWORKS: { value: CardNetwork; label: string }[] = [
  { value: "visa", label: "Visa" },
  { value: "mastercard", label: "MC" },
  { value: "amex", label: "Amex" },
  { value: "discover", label: "Disc" },
  { value: "other", label: "Other" },
];

interface AddCardStepProps {
  onSaved: () => void;
  onSkip: () => void;
}

export function AddCardStep({ onSaved, onSkip }: AddCardStepProps) {
  const [name, setName] = useState("");
  const [network, setNetwork] = useState<CardNetwork>("visa");
  const [lastFour, setLastFour] = useState("");
  const [submittedName, setSubmittedName] = useState<string | null>(null);
  const [lookup, setLookup] = useState<CardLookupResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Editable copies of the lookup result — the parse-card preview lets the
  // user tweak multipliers / annual fee / issuer before commit.
  const [multipliers, setMultipliers] = useState<Record<string, number>>({});
  const [annualFee, setAnnualFee] = useState<string>("");
  const [issuer, setIssuer] = useState<string>("");
  const [program, setProgram] = useState<CardProgram>("Other");
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!submittedName) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setLookup(null);
    lookupCard(submittedName)
      .then((resp) => {
        if (cancelled) return;
        setLookup(resp.lookup);
        setMultipliers(resp.lookup.multipliers);
        setAnnualFee(resp.lookup.annual_fee ?? "");
        setIssuer(resp.lookup.issuer ?? "");
        setProgram(resp.lookup.program ?? "Other");
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(
          e instanceof Error
            ? e.message
            : "lookup failed — fill in the details manually.",
        );
        setLookup({
          program: null,
          multipliers: {},
          annual_fee: null,
          issuer: null,
          source_urls: [],
          needs_manual: true,
        });
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [submittedName]);

  const lastFourValid = /^\d{4}$/.test(lastFour);
  const canStartLookup = name.trim().length > 0 && lastFourValid;
  // Codex P2: confirming with a name that diverged from the looked-up name
  // would save card-X under name-Y's rewards/sources. Block confirm when the
  // current name doesn't match the one we ran the lookup against — the user
  // sees the preview disappear (via `effectiveLookup` below) and the button
  // reverts to "look it up" so the data round-trips through a fresh lookup.
  const nameMatchesLookup =
    submittedName !== null && name.trim() === submittedName;
  const effectiveLookup = nameMatchesLookup ? lookup : null;
  const canConfirm =
    canStartLookup &&
    issuer.trim().length > 0 &&
    !confirming &&
    effectiveLookup !== null;

  const startLookup = () => {
    setSubmittedName(name.trim());
  };

  const handleConfirm = async () => {
    // Belt-and-braces beyond the button-disabled state above: only commit
    // when the current name matches the looked-up name. Otherwise the saved
    // row would mix one card's name with another card's rewards/sources.
    if (!effectiveLookup || !submittedName) return;
    setConfirming(true);
    setError(null);
    try {
      await confirmCard({
        network,
        last_four: lastFour,
        name: submittedName,
        issuer: issuer.trim(),
        program,
        multipliers,
        annual_fee: annualFee.trim() === "" ? null : annualFee.trim(),
        source_urls: effectiveLookup.source_urls,
        needs_manual: effectiveLookup.needs_manual,
      });
      onSaved();
    } catch (e) {
      if (isActiveCardExistsError(e)) {
        const detail = e.body.detail;
        setError(
          `you already have ${detail.existing_card_name} ending ${
            detail.existing_card_last_four ?? lastFour
          } — edit that one from the cards page.`,
        );
      } else if (e instanceof Error) {
        setError(e.message);
      } else {
        setError("couldn't add the card. try again.");
      }
      setConfirming(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col px-6 pb-10 pt-16 animate-fade-up">
      <StepDots current={1} total={2} label="step 1 of 2" />

      <h1 className="mt-6 font-serif text-3xl text-ink lowercase-title">
        add your first card
      </h1>
      <p className="mt-2 text-sm text-ink-secondary">
        we'll fetch the reward structure so you don't have to.
      </p>

      <div className="mt-8 flex items-center gap-3 rounded-2xl border border-hairline bg-elevated px-4 py-3">
        <Search className="h-4 w-4 text-ink-tertiary" />
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="card name"
          className="flex-1 bg-transparent text-[0.95rem] text-ink placeholder:text-ink-quaternary focus:outline-none"
        />
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setName(s)}
            className="rounded-full border border-hairline bg-surface px-3 py-1 text-xs text-ink-secondary transition-colors hover:bg-sunken/60 hover:text-ink"
          >
            {s}
          </button>
        ))}
      </div>

      <div className="mt-4 flex items-center gap-3">
        <div className="flex flex-1 flex-wrap gap-1.5">
          {NETWORKS.map((n) => (
            <button
              key={n.value}
              type="button"
              onClick={() => setNetwork(n.value)}
              className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                network === n.value
                  ? "border-moss bg-moss text-surface"
                  : "border-hairline bg-surface text-ink-secondary hover:bg-sunken/60"
              }`}
            >
              {n.label}
            </button>
          ))}
        </div>
        <input
          type="text"
          inputMode="numeric"
          pattern="\d{4}"
          maxLength={4}
          value={lastFour}
          onChange={(e) =>
            setLastFour(e.target.value.replace(/\D/g, "").slice(0, 4))
          }
          placeholder="last 4"
          className="w-20 rounded-xl border border-hairline bg-elevated px-3 py-2 text-center text-sm text-ink placeholder:text-ink-quaternary focus:outline-none"
        />
      </div>

      {submittedName && (
        <div className="mt-6">
          {loading ? (
            <div className="flex items-center justify-center gap-2 rounded-3xl border border-hairline bg-elevated py-10 text-sm text-ink-tertiary">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>looking up multipliers…</span>
            </div>
          ) : !nameMatchesLookup ? (
            <div className="rounded-3xl border border-dashed border-amber-soft bg-amber/5 px-4 py-3 text-xs text-amber-deep">
              card name changed — tap “look it up” to refresh the multipliers
              for {name.trim() || "this card"}.
            </div>
          ) : effectiveLookup ? (
            <PreviewTile
              name={submittedName}
              networkLabel={
                NETWORKS.find((n) => n.value === network)?.label ?? "—"
              }
              lastFour={lastFour}
              issuer={issuer}
              onIssuer={setIssuer}
              program={program}
              onProgram={setProgram}
              annualFee={annualFee}
              onAnnualFee={setAnnualFee}
              multipliers={multipliers}
              onMultipliers={setMultipliers}
              needsManual={effectiveLookup.needs_manual}
              sourceUrls={effectiveLookup.source_urls}
            />
          ) : null}
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-xl border border-terracotta-soft bg-terracotta/5 px-3 py-2 text-xs text-terracotta-deep">
          {error}
        </div>
      )}

      <div className="flex-1" />

      <div className="mt-10 flex flex-col items-center gap-3">
        {!submittedName || !nameMatchesLookup ? (
          <Button
            fullWidth
            size="lg"
            disabled={!canStartLookup}
            onClick={startLookup}
          >
            look it up
          </Button>
        ) : (
          <Button
            fullWidth
            size="lg"
            disabled={!canConfirm}
            onClick={handleConfirm}
          >
            {confirming ? "adding…" : "add card"}
          </Button>
        )}
        <button
          type="button"
          onClick={onSkip}
          className="text-sm text-ink-tertiary underline-offset-4 hover:text-ink-secondary hover:underline"
        >
          skip for now
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------

interface PreviewTileProps {
  name: string;
  networkLabel: string;
  lastFour: string;
  issuer: string;
  onIssuer: (v: string) => void;
  program: CardProgram;
  onProgram: (v: CardProgram) => void;
  annualFee: string;
  onAnnualFee: (v: string) => void;
  multipliers: Record<string, number>;
  onMultipliers: (v: Record<string, number>) => void;
  needsManual: boolean;
  sourceUrls: string[];
}

function PreviewTile(props: PreviewTileProps) {
  return (
    <div className="rounded-3xl border border-hairline bg-elevated px-4 py-4">
      <div className="flex items-baseline justify-between">
        <span className="font-serif text-lg text-ink lowercase-title">
          {props.name}
        </span>
        <span className="text-xs text-ink-tertiary tabular">
          {props.networkLabel} · ···· {props.lastFour}
        </span>
      </div>

      {props.needsManual && (
        <p className="mt-2 text-xs text-amber-deep">
          couldn't auto-fill — tweak the fields below.
        </p>
      )}

      <div className="mt-4 grid grid-cols-2 gap-3">
        <label className="flex flex-col text-xs text-ink-tertiary">
          issuer
          <input
            value={props.issuer}
            onChange={(e) => props.onIssuer(e.target.value)}
            className="mt-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none"
          />
        </label>
        <label className="flex flex-col text-xs text-ink-tertiary">
          annual fee
          <input
            inputMode="decimal"
            value={props.annualFee}
            onChange={(e) =>
              props.onAnnualFee(e.target.value.replace(/[^\d.]/g, ""))
            }
            placeholder="0"
            className="mt-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none"
          />
        </label>
      </div>

      <label className="mt-3 flex flex-col text-xs text-ink-tertiary">
        program
        <select
          value={props.program}
          onChange={(e) => props.onProgram(e.target.value as CardProgram)}
          className="mt-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none"
        >
          <option value="UR">UR — Chase Ultimate Rewards</option>
          <option value="MR">MR — Amex Membership Rewards</option>
          <option value="TYP">TYP — Citi ThankYou Points</option>
          <option value="Bilt">Bilt</option>
          <option value="Other">Other / cashback</option>
        </select>
      </label>

      <MultipliersEditor
        multipliers={props.multipliers}
        onMultipliers={props.onMultipliers}
        needsManual={props.needsManual}
      />


      {props.sourceUrls.length > 0 && (
        <div className="mt-3 border-t border-hairline pt-2 text-[0.7rem] text-ink-quaternary">
          sources:&nbsp;
          {props.sourceUrls.slice(0, 3).map((u, i) => (
            <span key={u}>
              {i > 0 ? ", " : ""}
              <a
                href={u}
                target="_blank"
                rel="noreferrer"
                className="underline decoration-dotted underline-offset-2 hover:text-ink-tertiary"
              >
                {new URL(u).hostname.replace(/^www\./, "")}
              </a>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}


/**
 * MultipliersEditor — editable list with per-row remove + add affordance.
 *
 * Closes Codex P2: when lookup returns `needs_manual=true` or no bonus
 * categories, the user previously had no way to add multipliers; saving
 * here used to land a card with empty `multipliers`, breaking reward
 * recommendations downstream. With this component, the manual-fill path
 * is symmetrical — the user can add, edit, and remove rows regardless of
 * whether the auto-lookup populated anything.
 *
 * Category options come from `CATEGORIES` (Tameru's closed enum). Tameru's
 * spend categories don't map 1:1 onto a bank's bonus categories ("Dining"
 * does, "U.S. supermarkets" doesn't), so the picker also accepts a free-form
 * "custom…" entry that becomes the row key. The backend stores multipliers
 * as `Record<string, number>` — no enum constraint on the key — so a custom
 * label is round-trip safe.
 */
function MultipliersEditor(props: {
  multipliers: Record<string, number>;
  onMultipliers: (m: Record<string, number>) => void;
  needsManual: boolean;
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

