/**
 * Unsubscribe confirm page (audit P3-10).
 *
 * The mutation must wait for a human tap: the page renders a confirm
 * button from the redirected token, POSTs it to the backend's RFC 8058
 * endpoint on click, and only then shows the unsubscribed state. Also
 * pins the invalid-link state (missing token → no button, no POST) and
 * the error state (failed POST → retry affordance, no false success).
 */

import { describe, expect, test, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import UnsubscribePage from "@/pages/unsubscribe";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <UnsubscribePage />
    </MemoryRouter>,
  );
}

describe("UnsubscribePage", () => {
  test("renders confirm state without firing any request", () => {
    renderAt("/unsubscribe?user=u-1&kind=digest&token=tok-1");
    expect(
      screen.getByText(/unsubscribe from the weekly digest\?/i),
    ).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("button POSTs the token to the backend and shows the done state", async () => {
    fetchMock.mockResolvedValue({ ok: true });
    renderAt("/unsubscribe?user=u-1&kind=digest&token=tok-1");

    await userEvent.click(screen.getByRole("button", { name: /^unsubscribe$/i }));

    await waitFor(() => {
      expect(screen.getByText(/you're unsubscribed\./i)).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/unsubscribe?");
    expect(String(url)).toContain("user=u-1");
    expect(String(url)).toContain("kind=digest");
    expect(String(url)).toContain("token=tok-1");
    expect(init).toMatchObject({ method: "POST" });
  });

  test("missing token renders the invalid state with no button", () => {
    renderAt("/unsubscribe?user=u-1&kind=digest");
    expect(screen.getByText(/this link isn't valid\./i)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("failed POST shows the error state with a retry, not false success", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 403 });
    renderAt("/unsubscribe?user=u-1&kind=digest&token=tok-bad");

    await userEvent.click(screen.getByRole("button", { name: /^unsubscribe$/i }));

    await waitFor(() => {
      expect(screen.getByText(/couldn't unsubscribe\./i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/you're unsubscribed\./i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
