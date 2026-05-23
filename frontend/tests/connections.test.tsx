/**
 * Connected apps page test — Day 23b.
 *
 * Pins the three contract calls into supabase-js the page is built on:
 *   - listGrants() on mount (and after a successful disconnect).
 *   - revokeGrant({ clientId }) when the user confirms disconnect.
 *
 * Also covers the empty / loading / error states.
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import ConnectionsPage from "@/pages/connections";

const supabaseMocks = vi.hoisted(() => ({
  listGrants: vi.fn(),
  revokeGrant: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  supabase: {
    auth: {
      oauth: supabaseMocks,
    },
  },
}));

function renderPage() {
  return render(
    <MemoryRouter>
      <ConnectionsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  supabaseMocks.listGrants.mockReset();
  supabaseMocks.revokeGrant.mockReset();
});

describe("ConnectionsPage", () => {
  test("renders the empty state when no grants exist", async () => {
    supabaseMocks.listGrants.mockResolvedValueOnce({ data: [], error: null });
    renderPage();
    expect(await screen.findByText(/no apps connected yet/i)).toBeInTheDocument();
  });

  test("renders one row per grant", async () => {
    supabaseMocks.listGrants.mockResolvedValueOnce({
      data: [
        {
          client: {
            id: "c1",
            name: "Claude.ai",
            uri: "https://claude.ai",
            logo_uri: "",
          },
          scopes: ["openid"],
          granted_at: "2026-05-21T10:00:00Z",
        },
        {
          client: {
            id: "c2",
            name: "Claude Code",
            uri: "",
            logo_uri: "",
          },
          scopes: ["openid"],
          granted_at: "2026-05-18T08:30:00Z",
        },
      ],
      error: null,
    });

    renderPage();

    expect(await screen.findByText("Claude.ai")).toBeInTheDocument();
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
  });

  test("Disconnect → confirm calls revokeGrant with the clientId and refreshes the list", async () => {
    supabaseMocks.listGrants.mockResolvedValueOnce({
      data: [
        {
          client: {
            id: "c1",
            name: "Claude.ai",
            uri: "https://claude.ai",
            logo_uri: "",
          },
          scopes: ["openid"],
          granted_at: "2026-05-21T10:00:00Z",
        },
      ],
      error: null,
    });
    supabaseMocks.revokeGrant.mockResolvedValueOnce({ data: {}, error: null });
    // After revoke, the list reloads — return empty.
    supabaseMocks.listGrants.mockResolvedValueOnce({ data: [], error: null });

    renderPage();
    await screen.findByText("Claude.ai");

    await userEvent.click(screen.getByRole("button", { name: /disconnect/i }));

    // The sheet portals to document.body — search globally.
    const dialog = await screen.findByRole("dialog", {
      name: /disconnect app/i,
    });
    await userEvent.click(
      within(dialog).getByRole("button", { name: /^disconnect$/i }),
    );

    await waitFor(() => {
      expect(supabaseMocks.revokeGrant).toHaveBeenCalledWith({
        clientId: "c1",
      });
    });
    // The list reloaded → empty state visible.
    expect(await screen.findByText(/no apps connected yet/i)).toBeInTheDocument();
  });

  test("a listGrants error renders the failure message", async () => {
    supabaseMocks.listGrants.mockResolvedValueOnce({
      data: null,
      error: { message: "could not reach the auth server" },
    });
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not reach the auth server/i);
  });
});
