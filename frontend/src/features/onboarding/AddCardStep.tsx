import { useEffect, useState } from "react";
import { Search, Loader2 } from "lucide-react";
import { Button } from "@/components/Button";
import { StepDots } from "./StepDots";
import { CardReviewTile } from "./CardReviewTile";
import { fetchCardPreview, type CardPreview } from "./cardFixtures";

const SUGGESTIONS = ["Chase Sapphire Preferred", "Amex Gold", "Citi Double Cash"];

interface AddCardStepProps {
  onSaved: () => void;
  onSkip: () => void;
}

export function AddCardStep({ onSaved, onSkip }: AddCardStepProps) {
  const [name, setName] = useState("");
  const [submittedName, setSubmittedName] = useState<string | null>(null);
  const [preview, setPreview] = useState<CardPreview | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!submittedName) return;
    let cancelled = false;
    setLoading(true);
    setPreview(null);
    fetchCardPreview(submittedName).then((p) => {
      if (cancelled) return;
      setPreview(p);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [submittedName]);

  const canAdd = name.trim().length > 0;

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

      {submittedName && (
        <div className="mt-6">
          {loading ? (
            <div className="flex items-center justify-center gap-2 rounded-3xl border border-hairline bg-elevated py-10 text-sm text-ink-tertiary">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>drafting your reward structure…</span>
            </div>
          ) : preview ? (
            <CardReviewTile
              preview={preview}
              onSave={onSaved}
              onTryAgain={() => setSubmittedName(preview.name)}
              onDiscard={() => {
                setSubmittedName(null);
                setPreview(null);
              }}
            />
          ) : null}
        </div>
      )}

      <div className="flex-1" />

      {!submittedName && (
        <div className="mt-10 flex flex-col items-center gap-3">
          <Button
            fullWidth
            size="lg"
            disabled={!canAdd}
            onClick={() => setSubmittedName(name.trim())}
          >
            add card
          </Button>
          <button
            type="button"
            onClick={onSkip}
            className="text-sm text-ink-tertiary underline-offset-4 hover:text-ink-secondary hover:underline"
          >
            skip for now
          </button>
        </div>
      )}
    </div>
  );
}
