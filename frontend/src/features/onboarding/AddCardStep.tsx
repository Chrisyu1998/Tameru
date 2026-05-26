import { useEffect, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { Button } from "@/components/Button";
import { MultipliersEditor } from "@/components/MultipliersEditor";
import { StepDots } from "./StepDots";
import {
  ISSUER_LABELS,
  ISSUERS,
  confirmCard,
  isActiveCardExistsError,
  lookupCard,
  type CardIssuer,
  type CardLookupResult,
  type CardNetwork,
  type CardProgram,
} from "@/lib/cardsApi";
import { track } from "@/lib/analytics";

/*
 * Day 14 onboarding step — UX frame 4 "Add First Card."
 *
 * Pre-lookup form: just the card name (Day 14 follow-up — we no longer
 * ask the user for network / issuer / last 4 upfront, since the lookup
 * derives network + issuer from the card name, and last 4 belongs on the
 * editable preview where the user has visual context).
 *
 * Post-lookup preview: network / issuer / last 4 / program / multipliers /
 * annual fee all pre-filled from the lookup and editable. "add card" stays
 * disabled until last 4 is a valid 4-digit number.
 *
 * 409 collision (DESIGN.md §8.1): when the user re-types an
 * (issuer, last_four) that's already active, the confirm endpoint returns
 * active_card_exists and we surface an inline banner.
 */

const SUGGESTIONS = ["Chase Sapphire Preferred", "Amex Gold", "Citi Double Cash"];

const NETWORKS: { value: CardNetwork; label: string }[] = [
  { value: "visa", label: "Visa" },
  { value: "mastercard", label: "Mastercard" },
  { value: "amex", label: "Amex" },
  { value: "discover", label: "Discover" },
  { value: "other", label: "Other" },
];

interface AddCardStepProps {
  onSaved: () => void;
  onSkip: () => void;
}

export function AddCardStep({ onSaved, onSkip }: AddCardStepProps) {
  const [name, setName] = useState("");
  const [submittedName, setSubmittedName] = useState<string | null>(null);
  const [lookup, setLookup] = useState<CardLookupResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Editable preview-tile state — populated when a lookup completes.
  // `network` and `issuer` are NULLABLE on purpose: silently defaulting
  // them to "visa"/"other" when the lookup couldn't determine them would
  // save the card with wrong identity metadata (especially harmful now
  // that the partial unique index is keyed on issuer — DESIGN.md §8.1).
  // When either is null the PreviewTile renders a "select…" placeholder
  // option and blocks confirm until the user picks explicitly.
  const [network, setNetwork] = useState<CardNetwork | null>(null);
  const [issuer, setIssuer] = useState<CardIssuer | null>(null);
  const [lastFour, setLastFour] = useState("");
  const [program, setProgram] = useState<CardProgram>("Other");
  const [multipliers, setMultipliers] = useState<Record<string, number>>({});
  const [annualFee, setAnnualFee] = useState<string>("");
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
        // Pass network/issuer through verbatim — DO NOT fall back to
        // "visa"/"other" silently. Null state forces the PreviewTile to
        // render a "select…" placeholder and disables confirm until the
        // user picks. See Codex P2 above and DESIGN.md §8.1 (issuer is
        // the uniqueness key — wrong-but-valid defaults are a real bug).
        setNetwork(resp.lookup.network);
        setIssuer(resp.lookup.issuer);
        setProgram(resp.lookup.program ?? "Other");
        setMultipliers(resp.lookup.multipliers);
        setAnnualFee(resp.lookup.annual_fee ?? "");
        // Don't pre-fill last 4 — the user must enter it explicitly. Empty
        // forces them to look at their card and type the right digits
        // rather than accidentally accepting a stale value.
        setLastFour("");
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
          network: null,
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

  // Codex P2: if the user edits the card name post-lookup, the preview's
  // multipliers/source_urls belong to the OLD name. Block confirm until
  // the user re-runs the lookup against the new name.
  const nameMatchesLookup =
    submittedName !== null && name.trim() === submittedName;
  const effectiveLookup = nameMatchesLookup ? lookup : null;

  const lastFourValid = /^\d{4}$/.test(lastFour);
  const canStartLookup = name.trim().length > 0;
  // Codex P2: require issuer + network to be explicitly resolved before
  // confirm. When the lookup returns null for either, the user has to
  // pick from the dropdown — silently saving with `other`/`visa` would
  // create wrong identity metadata, especially harmful now that the
  // uniqueness index is keyed on issuer.
  const canConfirm =
    nameMatchesLookup &&
    lastFourValid &&
    effectiveLookup !== null &&
    issuer !== null &&
    network !== null &&
    !confirming;

  const startLookup = () => {
    setSubmittedName(name.trim());
  };

  const handleConfirm = async () => {
    if (
      !effectiveLookup ||
      !submittedName ||
      !lastFourValid ||
      issuer === null ||
      network === null
    ) {
      return;
    }
    setConfirming(true);
    setError(null);
    try {
      await confirmCard({
        network,
        last_four: lastFour,
        name: submittedName,
        issuer,
        program,
        multipliers,
        annual_fee: annualFee.trim() === "" ? null : annualFee.trim(),
        source_urls: effectiveLookup.source_urls,
        needs_manual: effectiveLookup.needs_manual,
        // Onboarding-added cards have no chat-side proposal block to
        // join back to, but the column is NOT NULL — mint a fresh UUID
        // so the schema invariant holds. The crid never gets used as a
        // join key for these rows; it's structural padding.
        client_request_id: crypto.randomUUID(),
      });
      track("feature_used", { feature: "card_added" });
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
        type the card name — we'll figure out the rest.
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
              network={network}
              onNetwork={setNetwork}
              issuer={issuer}
              onIssuer={setIssuer}
              lastFour={lastFour}
              onLastFour={setLastFour}
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
  network: CardNetwork | null;
  onNetwork: (v: CardNetwork) => void;
  issuer: CardIssuer | null;
  onIssuer: (v: CardIssuer) => void;
  lastFour: string;
  onLastFour: (v: string) => void;
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
  // Codex P2: when the lookup couldn't determine network/issuer we show
  // the user a "select…" placeholder + amber border instead of silently
  // defaulting. Confirm stays disabled (in the parent's `canConfirm`)
  // until both are picked. Issuer is the load-bearing one — it's the
  // uniqueness tiebreaker (DESIGN.md §8.1) — but network gets the same
  // treatment for symmetry and to surface the lookup miss honestly.
  const issuerUnresolved = props.issuer === null;
  const networkUnresolved = props.network === null;
  const unresolvedAny = issuerUnresolved || networkUnresolved;

  return (
    <div className="rounded-3xl border border-hairline bg-elevated px-4 py-4">
      <div className="flex items-baseline justify-between">
        <span className="font-serif text-lg text-ink lowercase-title">
          {props.name}
        </span>
        <span className="text-xs text-ink-tertiary tabular">
          {props.issuer ? ISSUER_LABELS[props.issuer] : "—"} ·{" "}
          {props.network ?? "—"}
          {props.lastFour ? ` · ···· ${props.lastFour}` : ""}
        </span>
      </div>

      {unresolvedAny && (
        <p className="mt-2 text-xs text-amber-deep">
          lookup couldn't determine
          {issuerUnresolved && networkUnresolved
            ? " issuer or network"
            : issuerUnresolved
              ? " the issuing bank"
              : " the card network"}{" "}
          — pick below to continue.
        </p>
      )}
      {props.needsManual && !unresolvedAny && (
        <p className="mt-2 text-xs text-amber-deep">
          couldn't auto-fill — tweak the fields below.
        </p>
      )}

      <div className="mt-4 grid grid-cols-2 gap-3">
        <label className="flex flex-col text-xs text-ink-tertiary">
          issuer
          <select
            value={props.issuer ?? ""}
            onChange={(e) => props.onIssuer(e.target.value as CardIssuer)}
            className={
              "mt-1 rounded-lg border bg-surface px-2 py-1 text-sm text-ink focus:outline-none " +
              (issuerUnresolved
                ? "border-amber-soft ring-1 ring-amber-soft/40"
                : "border-hairline")
            }
          >
            {issuerUnresolved && (
              <option value="" disabled>
                select…
              </option>
            )}
            {ISSUERS.map((i) => (
              <option key={i} value={i}>
                {ISSUER_LABELS[i]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-ink-tertiary">
          network
          <select
            value={props.network ?? ""}
            onChange={(e) => props.onNetwork(e.target.value as CardNetwork)}
            className={
              "mt-1 rounded-lg border bg-surface px-2 py-1 text-sm text-ink focus:outline-none " +
              (networkUnresolved
                ? "border-amber-soft ring-1 ring-amber-soft/40"
                : "border-hairline")
            }
          >
            {networkUnresolved && (
              <option value="" disabled>
                select…
              </option>
            )}
            {NETWORKS.map((n) => (
              <option key={n.value} value={n.value}>
                {n.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <label className="flex flex-col text-xs text-ink-tertiary">
          last 4
          <input
            type="text"
            inputMode="numeric"
            pattern="\d{4}"
            maxLength={4}
            value={props.lastFour}
            onChange={(e) =>
              props.onLastFour(e.target.value.replace(/\D/g, "").slice(0, 4))
            }
            placeholder="1234"
            className="mt-1 rounded-lg border border-hairline bg-surface px-2 py-1 text-sm text-ink placeholder:text-ink-quaternary focus:outline-none"
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


