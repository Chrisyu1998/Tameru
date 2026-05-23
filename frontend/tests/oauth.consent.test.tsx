/**
 * /oauth/consent component test — Day 23b.
 *
 * Verifies the consent page's three contract calls into supabase-js:
 *   1. getAuthorizationDetails on mount (with the authorization_id from
 *      the URL).
 *   2. approveAuthorization when the user taps Allow.
 *   3. denyAuthorization when the user taps Cancel.
 *
 * Also covers the OAuthRedirect short-circuit (already-consented case)
 * and the error path. window.location.href is monkey-patched to a spy
 * so the test can assert the redirect target without jsdom warning
 * about an unimplemented navigation.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import OauthConsentPage from "@/pages/oauth.consent";
import { useAppStore } from "@/store";

const supabaseMocks = vi.hoisted(() => ({
  getAuthorizationDetails: vi.fn(),
  approveAuthorization: vi.fn(),
  denyAuthorization: vi.fn(),
}));

const authMocks = vi.hoisted(() => ({
  signInWithGoogle: vi.fn(),
  signInWithMagicLink: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  supabase: {
    auth: {
      oauth: supabaseMocks,
    },
  },
}));

vi.mock("@/lib/auth", () => authMocks);

// window.location.href is a getter/setter in jsdom; defining our own
// makes the redirect assertion trivial and silences the "navigation
// not implemented" warning.
let locationHref = "";
beforeEach(() => {
  locationHref = "";
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      get href() {
        return locationHref;
      },
      set href(v: string) {
        locationHref = v;
      },
    },
  });
  // Default: sign the user in. Tests that need the no-JWT branch
  // override this via useAppStore.setState({ jwt: null }) inside the test.
  useAppStore.setState({
    jwt: "fake-jwt",
    user: { id: "user-1", email: "you@example.com" },
    deviceId: "dev-1",
  });
  // Reset call history (a fresh `vi.fn()` clears between tests
  // automatically, but reuse-across-tests via vi.hoisted means we need
  // to be explicit).
  supabaseMocks.getAuthorizationDetails.mockReset();
  supabaseMocks.approveAuthorization.mockReset();
  supabaseMocks.denyAuthorization.mockReset();
  authMocks.signInWithGoogle.mockReset();
  authMocks.signInWithMagicLink.mockReset();
});

afterEach(() => {
  useAppStore.setState({ jwt: null, user: null });
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/oauth/consent" element={<OauthConsentPage />} />
        <Route path="/onboarding" element={<div>onboarding-page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("OauthConsentPage", () => {
  test("calls getAuthorizationDetails with the URL param and renders client info", async () => {
    supabaseMocks.getAuthorizationDetails.mockResolvedValueOnce({
      data: {
        authorization_id: "auth-abc",
        redirect_uri: "https://claude.ai/oauth/callback",
        client: {
          id: "client-99",
          name: "Claude.ai",
          uri: "https://claude.ai",
          logo_uri: "",
        },
        user: { id: "user-1", email: "you@example.com" },
        scope: "openid",
      },
      error: null,
    });

    renderAt("/oauth/consent?authorization_id=auth-abc");

    await waitFor(() => {
      expect(supabaseMocks.getAuthorizationDetails).toHaveBeenCalledWith("auth-abc");
    });
    expect(await screen.findByText("Claude.ai")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /allow/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeEnabled();
  });

  test("Allow calls approveAuthorization and follows the returned redirect", async () => {
    supabaseMocks.getAuthorizationDetails.mockResolvedValueOnce({
      data: {
        authorization_id: "auth-abc",
        redirect_uri: "https://claude.ai/oauth/callback",
        client: { id: "c1", name: "Claude.ai", uri: "", logo_uri: "" },
        user: { id: "user-1", email: "you@example.com" },
        scope: "openid",
      },
      error: null,
    });
    supabaseMocks.approveAuthorization.mockResolvedValueOnce({
      data: { redirect_url: "https://claude.ai/oauth/callback?code=xyz&state=s" },
      error: null,
    });

    renderAt("/oauth/consent?authorization_id=auth-abc");
    await screen.findByText("Claude.ai");

    await userEvent.click(screen.getByRole("button", { name: /allow/i }));

    await waitFor(() => {
      expect(supabaseMocks.approveAuthorization).toHaveBeenCalledWith(
        "auth-abc",
        expect.objectContaining({ skipBrowserRedirect: true }),
      );
    });
    expect(locationHref).toBe(
      "https://claude.ai/oauth/callback?code=xyz&state=s",
    );
  });

  test("Cancel calls denyAuthorization and follows the returned redirect", async () => {
    supabaseMocks.getAuthorizationDetails.mockResolvedValueOnce({
      data: {
        authorization_id: "auth-abc",
        redirect_uri: "https://claude.ai/oauth/callback",
        client: { id: "c1", name: "Claude.ai", uri: "", logo_uri: "" },
        user: { id: "user-1", email: "you@example.com" },
        scope: "openid",
      },
      error: null,
    });
    supabaseMocks.denyAuthorization.mockResolvedValueOnce({
      data: {
        redirect_url:
          "https://claude.ai/oauth/callback?error=access_denied&state=s",
      },
      error: null,
    });

    renderAt("/oauth/consent?authorization_id=auth-abc");
    await screen.findByText("Claude.ai");

    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() => {
      expect(supabaseMocks.denyAuthorization).toHaveBeenCalledWith(
        "auth-abc",
        expect.objectContaining({ skipBrowserRedirect: true }),
      );
    });
    expect(locationHref).toBe(
      "https://claude.ai/oauth/callback?error=access_denied&state=s",
    );
  });

  test("already-consented OAuthRedirect response bounces immediately", async () => {
    supabaseMocks.getAuthorizationDetails.mockResolvedValueOnce({
      data: { redirect_url: "https://claude.ai/oauth/callback?code=instant" },
      error: null,
    });

    renderAt("/oauth/consent?authorization_id=auth-abc");

    await waitFor(() => {
      expect(locationHref).toBe(
        "https://claude.ai/oauth/callback?code=instant",
      );
    });
    expect(supabaseMocks.approveAuthorization).not.toHaveBeenCalled();
  });

  test("missing authorization_id shows a hard error and never hits supabase", async () => {
    renderAt("/oauth/consent");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /opened by claude\.ai/i,
    );
    expect(supabaseMocks.getAuthorizationDetails).not.toHaveBeenCalled();
  });

  test("an error from getAuthorizationDetails surfaces in the UI", async () => {
    supabaseMocks.getAuthorizationDetails.mockResolvedValueOnce({
      data: null,
      error: { message: "authorization expired" },
    });

    renderAt("/oauth/consent?authorization_id=auth-abc");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /authorization expired/i,
    );
  });

  // -------------------------------------------------------------------------
  // SignInGate — the no-jwt branch (Codex P2). The OAuth `authorization_id`
  // must survive the sign-in round-trip, so we render an inline sign-in
  // surface instead of redirecting to /onboarding (which would drop the
  // query param and strand the OAuth dance).
  // -------------------------------------------------------------------------

  test("no jwt + valid authorization_id renders the inline sign-in CTA, never hits supabase.auth.oauth", async () => {
    useAppStore.setState({ jwt: null, user: null });

    renderAt("/oauth/consent?authorization_id=auth-abc");

    expect(
      await screen.findByRole("heading", { name: /sign in to continue/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /sign in with google/i }),
    ).toBeEnabled();
    // Crucially: the OAuth detail fetch is not attempted when signed-out.
    expect(supabaseMocks.getAuthorizationDetails).not.toHaveBeenCalled();
  });

  test("'sign in with google' passes the current URL (incl. authorization_id) as returnTo", async () => {
    useAppStore.setState({ jwt: null, user: null });
    // The component reads window.location.href as the returnTo. The
    // location mock in beforeEach doesn't set a real pathname; set it
    // here so the assertion has something specific to match.
    locationHref = "https://app.example/oauth/consent?authorization_id=auth-abc";
    authMocks.signInWithGoogle.mockResolvedValueOnce(undefined);

    renderAt("/oauth/consent?authorization_id=auth-abc");
    await screen.findByRole("button", { name: /sign in with google/i });

    await userEvent.click(
      screen.getByRole("button", { name: /sign in with google/i }),
    );

    await waitFor(() => {
      expect(authMocks.signInWithGoogle).toHaveBeenCalledWith(
        "https://app.example/oauth/consent?authorization_id=auth-abc",
      );
    });
  });

  test("magic-link path also passes returnTo so the email lands back on the consent URL", async () => {
    useAppStore.setState({ jwt: null, user: null });
    locationHref = "https://app.example/oauth/consent?authorization_id=auth-abc";
    authMocks.signInWithMagicLink.mockResolvedValueOnce(undefined);

    renderAt("/oauth/consent?authorization_id=auth-abc");

    await userEvent.click(
      screen.getByRole("button", { name: /sign in with email/i }),
    );
    await userEvent.type(
      screen.getByLabelText(/email/i),
      "you@example.com",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /send sign-in link/i }),
    );

    await waitFor(() => {
      expect(authMocks.signInWithMagicLink).toHaveBeenCalledWith(
        "you@example.com",
        "https://app.example/oauth/consent?authorization_id=auth-abc",
      );
    });
  });

  test("no jwt + missing authorization_id shows the hard-error message instead of a sign-in form", async () => {
    useAppStore.setState({ jwt: null, user: null });

    renderAt("/oauth/consent");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /opened by claude\.ai/i,
    );
    // No reason to offer sign-in when the URL is malformed — the user
    // would land back on the same broken URL.
    expect(
      screen.queryByRole("button", { name: /sign in with google/i }),
    ).toBeNull();
    expect(authMocks.signInWithGoogle).not.toHaveBeenCalled();
  });
});
