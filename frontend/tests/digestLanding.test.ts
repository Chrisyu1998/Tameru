/**
 * Day 26b — `?source=digest` landing handler.
 *
 * Covers:
 *   - Param-present + signed-in opted-in path: fires `weekly_digest_opened`
 *     exactly once, strips the param, preserves other query + hash.
 *   - Once-only invariant: a second call (StrictMode double-mount) is a
 *     no-op. The module-level `fired` flag closes that window even when
 *     the URL is restored mid-render.
 *   - Param-absent path: no track call, no replaceState.
 *   - Disabled wrapper path: track() no-ops (mirrors the opt-out case).
 */

import {
  afterEach,
  beforeEach,
  describe,
  expect,
  test,
  vi,
} from 'vitest';
import posthog from 'posthog-js';

import { _testing as analyticsTesting } from '@/lib/analytics';
import {
  _testing as digestTesting,
  initDigestLandingTracking,
} from '@/lib/digestLanding';

vi.mock('posthog-js', () => {
  const stub = {
    init: vi.fn(),
    capture: vi.fn(),
    identify: vi.fn(),
    reset: vi.fn(),
    opt_in_capturing: vi.fn(),
    opt_out_capturing: vi.fn(),
  };
  return { default: stub };
});

const mocked = vi.mocked(posthog, true);

function setUrl(search: string, pathname = '/', hash = ''): void {
  window.history.replaceState({}, '', pathname + search + hash);
}

beforeEach(() => {
  for (const fn of Object.values(mocked) as Array<{ mockClear?: () => void }>) {
    if (typeof fn?.mockClear === 'function') fn.mockClear();
  }
  digestTesting.reset();
  analyticsTesting.reset();
  setUrl('');
});

afterEach(() => {
  digestTesting.reset();
  analyticsTesting.reset();
});

describe('initDigestLandingTracking — enabled wrapper', () => {
  beforeEach(() => {
    analyticsTesting.forceEnabled(true);
  });

  test('fires `weekly_digest_opened` once and strips ?source=digest', () => {
    setUrl('?source=digest');
    initDigestLandingTracking();

    expect(mocked.capture).toHaveBeenCalledTimes(1);
    expect(mocked.capture).toHaveBeenCalledWith('weekly_digest_opened', {});
    expect(window.location.search).toBe('');
    expect(window.location.pathname).toBe('/');
  });

  test('StrictMode double-mount fires exactly once', () => {
    setUrl('?source=digest');
    initDigestLandingTracking();
    // Simulate a re-mount that restored the URL (defensive — the strip
    // is the structural guard, but the module-level flag closes the
    // window where a re-mount happens before paint).
    setUrl('?source=digest');
    initDigestLandingTracking();

    expect(mocked.capture).toHaveBeenCalledTimes(1);
  });

  test('preserves other query params and hash when stripping', () => {
    setUrl('?source=digest&ref=foo', '/breakdown', '#anchor');
    initDigestLandingTracking();

    expect(mocked.capture).toHaveBeenCalledTimes(1);
    expect(window.location.pathname).toBe('/breakdown');
    expect(window.location.search).toBe('?ref=foo');
    expect(window.location.hash).toBe('#anchor');
  });

  test('does nothing when the URL has no source param', () => {
    setUrl('?ref=foo');
    initDigestLandingTracking();

    expect(mocked.capture).not.toHaveBeenCalled();
    expect(window.location.search).toBe('?ref=foo');
  });

  test('does nothing when source is not "digest"', () => {
    setUrl('?source=twitter');
    initDigestLandingTracking();

    expect(mocked.capture).not.toHaveBeenCalled();
    expect(window.location.search).toBe('?source=twitter');
  });
});

describe('initDigestLandingTracking — disabled wrapper (no PostHog key)', () => {
  test('strips the param but track() is a no-op', () => {
    // _testing.reset() leaves enabled=false; track() short-circuits.
    setUrl('?source=digest');
    initDigestLandingTracking();

    expect(mocked.capture).not.toHaveBeenCalled();
    // Strip still happens — the once-only flag is consumed regardless,
    // so a subsequent opt-in won't replay this landing on next reload.
    expect(window.location.search).toBe('');
  });
});
