import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bell,
  ChevronRight,
  Download,
  Lock,
  LogOut,
  Mail,
  Plug,
  Upload,
} from "lucide-react";
import { SketchIcon } from "@/components/SketchIcon";
import { BottomSheet } from "@/components/BottomSheet";
import { ImportCsvSheet } from "@/components/ImportCsvSheet";
import { Pill } from "@/components/Pill";
import { signOut } from "@/lib/auth";
import { initialTokens } from "@/lib/claudeTokens";
import { useAppStore } from "@/store";
import { cn } from "@/lib/utils";

type SheetKey = "import" | "notifications" | "export" | "signout" | null;

export default function MorePage() {
  // Mock connected-state for the Claude row chip.
  const claudeConnected = initialTokens.length > 0;
  const [openSheet, setOpenSheet] = useState<SheetKey>(null);
  const email = useAppStore((s) => s.user?.email ?? "");
  const handle = email.split("@")[0] || "you";
  const avatar = (email[0] ?? "t").toUpperCase();

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-8 pb-24">
      {/* Identity */}
      <section className="flex items-center gap-3 px-1">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-moss-wash text-moss-deep font-serif text-lg">
          {avatar}
        </div>
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-[0.95rem] text-ink">{handle}</span>
          <span className="truncate text-[0.78rem] text-ink-tertiary">
            {email}
          </span>
        </div>
      </section>

      {/* Primary section */}
      <ul className="mt-6 divide-y divide-hairline rounded-2xl border border-hairline bg-surface">
        <RowLink
          to="/cards"
          label="my cards"
          icon={<SketchIcon kind="card" size={18} seed={23} />}
        />
        <RowLink
          to="/subscriptions"
          label="subscriptions"
          icon={<SketchIcon kind="repeat" size={18} seed={37} />}
        />
        <RowLink
          to="/goals"
          label="goals"
          icon={<SketchIcon kind="seedling" size={18} seed={47} />}
        />
        <RowLink
          to="/memory"
          label="ai memory"
          icon={<SketchIcon kind="sparkle" size={16} seed={53} />}
        />
        <RowLink
          to="/connections"
          label="claude connections"
          icon={<Plug className="h-4 w-4" />}
          chip={
            claudeConnected ? (
              <Pill tone="moss">connected</Pill>
            ) : (
              <Pill tone="neutral">not connected</Pill>
            )
          }
        />
      </ul>

      {/* Secondary section */}
      <p className="mt-7 px-2 text-[0.7rem] uppercase tracking-wider text-ink-tertiary">
        account & data
      </p>
      <ul className="mt-2 divide-y divide-hairline rounded-2xl border border-hairline bg-surface">
        <RowButton
          label="import data"
          icon={<Upload className="h-4 w-4" />}
          onClick={() => setOpenSheet("import")}
        />
        <RowButton
          label="notifications"
          icon={<Bell className="h-4 w-4" />}
          onClick={() => setOpenSheet("notifications")}
        />
        <RowLink
          to="/privacy"
          label="privacy"
          icon={<Lock className="h-4 w-4" />}
        />
        <RowButton
          label="export data"
          icon={<Download className="h-4 w-4" />}
          onClick={() => setOpenSheet("export")}
        />
        <RowButton
          label="sign out"
          icon={<LogOut className="h-4 w-4 text-over" />}
          tone="over"
          onClick={() => setOpenSheet("signout")}
        />
      </ul>

      <ImportCsvSheet
        open={openSheet === "import"}
        onClose={() => setOpenSheet(null)}
      />
      <NotificationsSheet
        open={openSheet === "notifications"}
        onClose={() => setOpenSheet(null)}
      />
      <ExportSheet
        open={openSheet === "export"}
        onClose={() => setOpenSheet(null)}
      />
      <SignOutDialog
        open={openSheet === "signout"}
        onClose={() => setOpenSheet(null)}
      />
    </div>
  );
}

/* ─── Rows ────────────────────────────────────────────────────── */

function RowLink({
  to,
  label,
  icon,
  chip,
}: {
  to: string;
  label: string;
  icon: React.ReactNode;
  chip?: React.ReactNode;
}) {
  return (
    <li>
      <Link
        to={to}
        className="flex items-center gap-3 px-4 py-3.5 text-[0.95rem] text-ink hover:bg-elevated"
      >
        <span className="text-ink-tertiary">{icon}</span>
        <span className="flex-1 lowercase">{label}</span>
        {chip}
        <ChevronRight className="h-4 w-4 text-ink-quaternary" />
      </Link>
    </li>
  );
}

function RowButton({
  label,
  icon,
  onClick,
  tone,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  tone?: "over";
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "flex w-full items-center gap-3 px-4 py-3.5 text-left text-[0.95rem] hover:bg-elevated",
          tone === "over" ? "text-over" : "text-ink"
        )}
      >
        <span className={tone === "over" ? "text-over" : "text-ink-tertiary"}>
          {icon}
        </span>
        <span className="flex-1 lowercase">{label}</span>
        <ChevronRight
          className={cn(
            "h-4 w-4",
            tone === "over" ? "text-over/60" : "text-ink-quaternary"
          )}
        />
      </button>
    </li>
  );
}

/* ─── Notifications sheet ─────────────────────────────────────── */

function NotificationsSheet({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [digest, setDigest] = useState(true);
  const [nudges, setNudges] = useState(true);

  return (
    <BottomSheet open={open} onClose={onClose} ariaLabel="notifications">
      <h2 className="font-serif text-2xl text-ink lowercase-title">
        notifications
      </h2>
      <p className="mt-1 text-[0.85rem] text-ink-tertiary">
        tameru speaks softly. you choose how often.
      </p>

      <div className="mt-5 divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
        <ToggleRow
          label="weekly digest"
          desc="a quiet recap every sunday morning."
          checked={digest}
          onChange={setDigest}
        />
        <ToggleRow
          label="entry nudges"
          desc="a gentle ping if you've not logged anything for a few days."
          checked={nudges}
          onChange={setNudges}
        />
      </div>

      <button
        type="button"
        onClick={onClose}
        className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-2xl bg-moss px-5 text-sm font-medium text-surface hover:bg-moss-deep"
      >
        save
      </button>
    </BottomSheet>
  );
}

function ToggleRow({
  label,
  desc,
  checked,
  onChange,
}: {
  label: string;
  desc: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-3.5">
      <div className="min-w-0">
        <p className="text-[0.95rem] text-ink lowercase-title">{label}</p>
        <p className="mt-0.5 text-[0.78rem] text-ink-tertiary">{desc}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-6 w-10 flex-shrink-0 rounded-full transition-colors",
          checked ? "bg-moss" : "bg-sunken"
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-5 w-5 rounded-full bg-surface transition-all",
            checked ? "left-[1.125rem]" : "left-0.5"
          )}
        />
      </button>
    </div>
  );
}

/* ─── Export sheet ────────────────────────────────────────────── */

function ExportSheet({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  // Tiny estimation — based on a typical mock ledger size.
  const estKb = 42;

  return (
    <BottomSheet open={open} onClose={onClose} ariaLabel="export data">
      <h2 className="font-serif text-2xl text-ink lowercase-title">
        export data
      </h2>
      <p className="mt-2 text-[0.9rem] leading-relaxed text-ink-secondary">
        a single <span className="text-ink">json</span> file with every
        transaction, card, and subscription. readable by any tool, owned by
        you — no lock-in.
      </p>

      <div className="mt-4 flex items-center justify-between rounded-xl border border-hairline bg-sunken/50 px-3 py-2.5 text-[0.82rem]">
        <span className="text-ink-tertiary">estimated size</span>
        <span className="tabular text-ink">~{estKb} kb</span>
      </div>

      <div className="mt-5 flex flex-col gap-2">
        <button
          type="button"
          onClick={onClose}
          className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl bg-moss px-5 text-sm font-medium text-surface hover:bg-moss-deep"
        >
          <Download className="h-4 w-4" /> download json
        </button>
        <button
          type="button"
          onClick={onClose}
          className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-2xl border border-hairline bg-surface text-sm text-ink hover:bg-elevated"
        >
          <Mail className="h-4 w-4" /> email it to me
        </button>
      </div>
    </BottomSheet>
  );
}

/* ─── Sign-out dialog (centered alert, not a sheet) ───────────── */

function SignOutDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="sign-out-title"
      className="fixed inset-0 z-[150] flex items-center justify-center px-6"
    >
      <button
        type="button"
        aria-label="close dialog"
        onClick={onClose}
        className="absolute inset-0 bg-ink/40 backdrop-blur-[2px] animate-scrim-in"
      />
      <div className="relative w-full max-w-sm rounded-3xl border border-hairline bg-elevated px-6 py-6 text-center animate-slide-up-in">
        <h2
          id="sign-out-title"
          className="font-serif text-2xl text-ink lowercase-title"
        >
          sign out?
        </h2>
        <p className="mt-2 text-[0.9rem] leading-relaxed text-ink-secondary">
          your data stays exactly where it is. you can sign back in anytime —
          nothing is deleted.
        </p>
        <div className="mt-6 grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-11 items-center justify-center rounded-2xl border border-hairline bg-surface text-sm font-medium text-ink hover:bg-sunken"
          >
            cancel
          </button>
          <button
            type="button"
            onClick={async () => {
              // The supabase onAuthStateChange listener (lib/auth.ts) clears
              // the store and the route gate then bounces back to /onboarding;
              // we close the dialog optimistically so the user sees motion.
              onClose();
              await signOut();
            }}
            className="inline-flex h-11 items-center justify-center rounded-2xl bg-over text-sm font-medium text-surface hover:opacity-90"
          >
            sign out
          </button>
        </div>
      </div>
    </div>
  );
}
