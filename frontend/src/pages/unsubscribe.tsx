import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { apiBaseUrl } from "@/lib/api";

/*
 * Unsubscribe confirm page (audit P3-10).
 *
 * The digest email's visible unsubscribe link GETs the backend, which
 * verifies the HMAC token and 302s here with the token intact in the
 * query string. Nothing has been mutated at that point — corporate link
 * scanners GET every URL in an email body, so the mutation must wait for
 * a human tap. The button POSTs the token back to the backend's RFC 8058
 * endpoint (the same one Gmail's automated one-click flow uses).
 *
 * Not behind RequireOnboarded and uses no session: the recipient may
 * open the link on a device that has never signed in to Tameru. The
 * POST is a simple cross-origin request — no auth header, no preflight;
 * the HMAC token is the authorization.
 */

type PageState = "confirm" | "working" | "done" | "error";

export default function UnsubscribePage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const [state, setState] = useState<PageState>("confirm");

  const user = params.get("user");
  const kind = params.get("kind") ?? "digest";
  const token = params.get("token");
  const linkValid = Boolean(user && token);

  const confirm = async () => {
    if (!user || !token) return;
    setState("working");
    try {
      const query = new URLSearchParams({ user, kind, token });
      const resp = await fetch(`${apiBaseUrl}/unsubscribe?${query.toString()}`, {
        method: "POST",
      });
      if (!resp.ok) throw new Error(`unsubscribe POST ${resp.status}`);
      setState("done");
    } catch {
      setState("error");
    }
  };

  return (
    <div className="mx-auto w-full max-w-md px-5 pt-16 pb-12">
      <div className="rounded-3xl border border-hairline bg-elevated px-6 py-8 text-center">
        {!linkValid ? (
          <>
            <h1 className="font-serif text-2xl text-ink lowercase-title">
              {t("unsubscribe.invalidTitle")}
            </h1>
            <p className="mt-3 text-sm leading-relaxed text-ink-secondary">
              {t("unsubscribe.invalidBody")}
            </p>
          </>
        ) : state === "done" ? (
          <>
            <h1 className="font-serif text-2xl text-ink lowercase-title">
              {t("unsubscribe.doneTitle")}
            </h1>
            <p className="mt-3 text-sm leading-relaxed text-ink-secondary">
              {t("unsubscribe.doneBody")}
            </p>
          </>
        ) : state === "error" ? (
          <>
            <h1 className="font-serif text-2xl text-ink lowercase-title">
              {t("unsubscribe.errorTitle")}
            </h1>
            <p className="mt-3 text-sm leading-relaxed text-ink-secondary">
              {t("unsubscribe.errorBody")}
            </p>
            <button
              type="button"
              onClick={confirm}
              className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-2xl bg-moss-deep text-sm font-medium text-surface hover:bg-moss"
            >
              {t("unsubscribe.retry")}
            </button>
          </>
        ) : (
          <>
            <h1 className="font-serif text-2xl text-ink lowercase-title">
              {t("unsubscribe.title")}
            </h1>
            <p className="mt-3 text-sm leading-relaxed text-ink-secondary">
              {t("unsubscribe.body")}
            </p>
            <button
              type="button"
              onClick={confirm}
              disabled={state === "working"}
              className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-2xl bg-moss-deep text-sm font-medium text-surface hover:bg-moss disabled:opacity-60"
            >
              {state === "working"
                ? t("unsubscribe.working")
                : t("unsubscribe.confirm")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
