import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Lock, Mail, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { signInWithGoogle, signInWithMagicLink } from "@/lib/auth";
import { supabase } from "@/lib/supabase";
import { useAppStore } from "@/store";
import { cn } from "@/lib/utils";

/*
 * Day 23b — Tameru-hosted OAuth 2.1 consent screen, served at
 * `/oauth/consent`.
 *
 * Supabase's OAuth 2.1 Server (DESIGN.md §7.9) deliberately does NOT
 * ship a hosted consent UI — the docs are explicit that this is a
 * frontend implementation. This page is the entire consent surface.
 *
 * Naming: the OAuth authorize endpoint itself lives at
 * `{SUPABASE_URL}/oauth/authorize` (on Supabase's Auth Server). This
 * page is the consent UI Supabase delegates to — so it lives at
 * `/oauth/consent`, not `/oauth/authorize`, to avoid two same-named
 * URLs on different hosts.
 *
 * Flow:
 *   1. Read `authorization_id` from the URL (Supabase redirects here
 *      from its own /oauth/authorize endpoint).
 *   2. Call supabase.auth.oauth.getAuthorizationDetails(authorization_id).
 *      Two response shapes:
 *        - OAuthAuthorizationDetails (has `authorization_id`): the user
 *          hasn't consented yet; render the consent UI.
 *        - OAuthRedirect (has `redirect_url`): already consented; bounce
 *          the browser at the URL immediately.
 *   3. Allow → approveAuthorization (auto-redirects by default).
 *      Cancel → denyAuthorization (auto-redirects with an error in the
 *      callback URL — the OAuth client sees access_denied).
 *
 * No per-scope picker in v1: a Tameru grant is read-only by construction
 * (the MCP server exposes only read tools, CLAUDE.md invariant 3). One
 * Allow button is the whole decision.
 */
type LoadState =
  | { kind: "loading" }
  | {
      kind: "consent";
      authorizationId: string;
      clientName: string;
      clientUri: string | null;
      userEmail: string;
    }
  | { kind: "error"; message: string };

export default function OauthConsentPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const authorizationId = params.get("authorization_id");
  const jwt = useAppStore((s) => s.jwt);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [submitting, setSubmitting] = useState<"approve" | "deny" | null>(null);

  // Missing authorization_id can only happen if a user lands on this URL
  // directly. Treat it as a hard error rather than guessing.
  const missingId = authorizationId === null || authorizationId.length === 0;

  useEffect(() => {
    // Skip the load when the user isn't signed in yet (the render path
    // shows an inline sign-in CTA instead — see SignInGate below) or
    // when the URL is malformed. Both states resolve in render.
    if (jwt === null) return;
    if (missingId) {
      setState({
        kind: "error",
        message: t("oauth.errorMissingId"),
      });
      return;
    }
    let cancelled = false;
    (async () => {
      const { data, error } = await supabase.auth.oauth.getAuthorizationDetails(
        authorizationId!,
      );
      if (cancelled) return;
      if (error) {
        setState({
          kind: "error",
          message: error.message || t("oauth.errorLoadFailed"),
        });
        return;
      }
      if (data && "redirect_url" in data) {
        // Already consented — Supabase has prepared the redirect URL.
        // Hop to it immediately; the OAuth client picks up from there.
        window.location.href = data.redirect_url;
        return;
      }
      if (data && "authorization_id" in data) {
        setState({
          kind: "consent",
          authorizationId: data.authorization_id,
          clientName: data.client.name,
          clientUri: data.client.uri || null,
          userEmail: data.user.email,
        });
        return;
      }
      setState({
        kind: "error",
        message: t("oauth.errorUnexpected"),
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [authorizationId, missingId, jwt]);

  // If the user lands here without a JWT, render the sign-in surface
  // INLINE — do NOT redirect to /onboarding. Redirecting drops the
  // `?authorization_id=...` query param, and after sign-in the
  // onboarding flow returns the user to `/`, stalling the OAuth dance.
  // The inline gate hands the current URL (including authorization_id)
  // back through Supabase as the redirect target, so the user lands
  // back here mid-flow and the useEffect above re-runs once jwt is set.
  if (jwt === null) {
    return (
      <SignInGate
        missingId={missingId}
        returnTo={typeof window !== "undefined" ? window.location.href : "/"}
      />
    );
  }

  const handleApprove = async () => {
    if (state.kind !== "consent" || submitting !== null) return;
    setSubmitting("approve");
    const { data, error } = await supabase.auth.oauth.approveAuthorization(
      state.authorizationId,
      // Override the default auto-redirect: we want one consistent
      // redirect mechanism (window.location.href) so the loading state
      // stays visible until the browser actually leaves the page.
      { skipBrowserRedirect: true },
    );
    if (error) {
      setSubmitting(null);
      setState({
        kind: "error",
        message: error.message || t("oauth.errorApproveFailed"),
      });
      return;
    }
    if (data && "redirect_url" in data) {
      window.location.href = data.redirect_url;
    }
  };

  const handleDeny = async () => {
    if (state.kind !== "consent" || submitting !== null) return;
    setSubmitting("deny");
    const { data, error } = await supabase.auth.oauth.denyAuthorization(
      state.authorizationId,
      { skipBrowserRedirect: true },
    );
    if (error) {
      setSubmitting(null);
      setState({
        kind: "error",
        message: error.message || t("oauth.errorDenyFailed"),
      });
      return;
    }
    if (data && "redirect_url" in data) {
      window.location.href = data.redirect_url;
    }
  };

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-md flex-col px-5 pt-10 pb-10">
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          {t("oauth.title")}
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-secondary">
          {t("oauth.subtitle")}
        </p>
      </header>

      <section className="mt-7 flex-1">
        {state.kind === "loading" && (
          <p className="text-sm text-ink-tertiary">{t("oauth.loading")}</p>
        )}

        {state.kind === "error" && (
          <div
            role="alert"
            className="rounded-2xl border border-hairline bg-warn-wash px-4 py-4"
          >
            <p className="text-sm leading-relaxed text-ink">{state.message}</p>
          </div>
        )}

        {state.kind === "consent" && (
          <ConsentBody
            clientName={state.clientName}
            clientUri={state.clientUri}
            userEmail={state.userEmail}
          />
        )}
      </section>

      {state.kind === "consent" && (
        <footer className="mt-6 space-y-2">
          <button
            type="button"
            onClick={handleApprove}
            disabled={submitting !== null}
            className={cn(
              "inline-flex h-12 w-full items-center justify-center rounded-2xl px-5 text-sm font-medium transition-colors",
              submitting === null
                ? "bg-moss text-surface hover:bg-moss-deep"
                : "bg-sunken text-ink-quaternary cursor-not-allowed",
            )}
          >
            {submitting === "approve" ? t("oauth.connecting") : t("oauth.allow")}
          </button>
          <button
            type="button"
            onClick={handleDeny}
            disabled={submitting !== null}
            className="inline-flex h-12 w-full items-center justify-center rounded-2xl border border-hairline bg-surface px-5 text-sm font-medium text-ink hover:bg-elevated disabled:cursor-not-allowed disabled:text-ink-quaternary"
          >
            {submitting === "deny" ? t("oauth.cancelling") : t("oauth.cancel")}
          </button>
        </footer>
      )}
    </div>
  );
}

function ConsentBody({
  clientName,
  clientUri,
  userEmail,
}: {
  clientName: string;
  clientUri: string | null;
  userEmail: string;
}) {
  const { t } = useTranslation();
  const clientHost = useMemo(() => {
    if (!clientUri) return null;
    try {
      return new URL(clientUri).host;
    } catch {
      return null;
    }
  }, [clientUri]);

  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-hairline bg-surface px-4 py-4">
        <p className="text-[0.78rem] uppercase tracking-wider text-ink-tertiary">
          {t("oauth.requestingAccess")}
        </p>
        <p className="mt-1.5 text-[1.05rem] font-medium text-ink">
          {clientName}
        </p>
        {clientHost && (
          <p className="text-[0.78rem] text-ink-tertiary">{clientHost}</p>
        )}
        <p className="mt-3 text-[0.78rem] text-ink-tertiary">
          {t("oauth.signedInAs")} <span className="text-ink-secondary">{userEmail}</span>
        </p>
      </div>

      <ul className="space-y-3 text-sm leading-relaxed text-ink-secondary">
        <li className="flex items-start gap-2.5">
          <ShieldCheck className="mt-0.5 h-4 w-4 flex-shrink-0 text-moss-deep" />
          <span>
            <span className="text-ink">{t("oauth.readOnly")}</span>{" "}
            {t("oauth.readOnlyDetail", { clientName })}
          </span>
        </li>
        <li className="flex items-start gap-2.5">
          <Lock className="mt-0.5 h-4 w-4 flex-shrink-0 text-moss-deep" />
          <span>
            {t("oauth.disconnectHint")}{" "}
            <span className="text-ink">{t("oauth.disconnectPath")}</span>.
          </span>
        </li>
      </ul>
    </div>
  );
}

/**
 * Sign-in CTA rendered when the user lands on /oauth/consent without an
 * active Supabase session. The `returnTo` URL is passed back through
 * Supabase's OAuth (Google) and magic-link redirects so the user lands
 * back on /oauth/consent with `?authorization_id=...` intact — supabase-js
 * `detectSessionInUrl` consumes the auth params from the URL hash, which
 * leaves the query string untouched. Once the session lands in the store,
 * the parent useEffect re-runs and the consent UI takes over.
 */
function SignInGate({
  missingId,
  returnTo,
}: {
  missingId: boolean;
  returnTo: string;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"choose" | "email" | "sent">("choose");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If the URL is malformed there's no flow to recover into, so don't
  // offer to sign the user in — they'd land back on the same broken URL.
  if (missingId) {
    return (
      <div className="mx-auto flex min-h-dvh w-full max-w-md flex-col px-5 pt-10 pb-10">
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          {t("oauth.title")}
        </h1>
        <div
          role="alert"
          className="mt-7 rounded-2xl border border-hairline bg-warn-wash px-4 py-4"
        >
          <p className="text-sm leading-relaxed text-ink">
            {t("oauth.errorMissingId")}
          </p>
        </div>
      </div>
    );
  }

  const handleGoogle = async () => {
    setBusy(true);
    setError(null);
    try {
      await signInWithGoogle(returnTo);
    } catch (e) {
      setBusy(false);
      setError(e instanceof Error ? e.message : t("oauth.signIn.errorGoogle"));
    }
  };

  const handleMagicLink = async () => {
    const trimmed = email.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      await signInWithMagicLink(trimmed, returnTo);
      setMode("sent");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("oauth.signIn.errorMagicLink"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-md flex-col px-5 pt-10 pb-10">
      <header>
        <h1 className="font-serif text-3xl text-ink lowercase-title">
          {t("oauth.signIn.title")}
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-secondary">
          {t("oauth.signIn.subtitle")}
        </p>
      </header>

      <section className="mt-7 flex-1">
        {mode === "sent" && (
          <div className="rounded-2xl border border-hairline bg-elevated p-5">
            <p className="font-serif text-lg text-ink lowercase-title">
              {t("oauth.signIn.checkEmail")}
            </p>
            <p className="mt-2 text-sm text-ink-secondary">
              {t("oauth.signIn.sentLinkTo")}{" "}
              <span className="text-ink">{email}</span>.{" "}
              {t("oauth.signIn.sentLinkHint")}
            </p>
            <button
              type="button"
              onClick={() => setMode("email")}
              className="mt-3 text-[0.85rem] text-ink-tertiary hover:text-ink"
            >
              {t("oauth.signIn.useDifferentEmail")}
            </button>
          </div>
        )}

        {mode === "choose" && (
          <div className="space-y-3">
            <button
              type="button"
              onClick={handleGoogle}
              disabled={busy}
              className={cn(
                "inline-flex h-12 w-full items-center justify-center gap-2 rounded-2xl border border-hairline bg-surface px-5 text-sm font-medium text-ink hover:bg-elevated",
                busy && "cursor-not-allowed opacity-60",
              )}
            >
              {busy ? t("oauth.signIn.starting") : t("oauth.signIn.withGoogle")}
            </button>
            <button
              type="button"
              onClick={() => setMode("email")}
              disabled={busy}
              className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-2xl border border-hairline bg-surface px-5 text-sm font-medium text-ink hover:bg-elevated disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Mail className="h-4 w-4" /> {t("oauth.signIn.withEmail")}
            </button>
          </div>
        )}

        {mode === "email" && (
          <div className="space-y-3">
            <label
              htmlFor="signin-email"
              className="block text-[0.78rem] uppercase tracking-wider text-ink-tertiary"
            >
              {t("oauth.signIn.emailLabel")}
            </label>
            <input
              id="signin-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={t("oauth.signIn.emailPlaceholder")}
              className="block w-full rounded-2xl border border-hairline bg-surface px-4 py-3 text-[0.95rem] text-ink placeholder:text-ink-quaternary focus:outline-none"
            />
            <button
              type="button"
              onClick={handleMagicLink}
              disabled={busy || email.trim().length === 0}
              className={cn(
                "inline-flex h-12 w-full items-center justify-center rounded-2xl px-5 text-sm font-medium transition-colors",
                busy || email.trim().length === 0
                  ? "bg-sunken text-ink-quaternary cursor-not-allowed"
                  : "bg-moss text-surface hover:bg-moss-deep",
              )}
            >
              {busy ? t("oauth.signIn.sending") : t("oauth.signIn.sendLink")}
            </button>
            <button
              type="button"
              onClick={() => setMode("choose")}
              disabled={busy}
              className="inline-flex h-10 w-full items-center justify-center text-[0.85rem] text-ink-tertiary hover:text-ink disabled:cursor-not-allowed"
            >
              {t("oauth.signIn.back")}
            </button>
          </div>
        )}

        {error && (
          <p role="alert" className="mt-3 text-[0.82rem] text-over">
            {error}
          </p>
        )}
      </section>
    </div>
  );
}
