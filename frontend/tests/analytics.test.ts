/**
 * Day 26 — PostHog wrapper unit tests.
 *
 * Covers:
 *   - Disabled-by-default when no VITE_POSTHOG_KEY: track() / identify /
 *     setOptOut / resetIdentity all no-op (no posthog-js calls).
 *   - Enabled path: setOptOut(true) calls posthog.opt_out_capturing()
 *     + posthog.reset(); setOptOut(false) calls opt_in_capturing();
 *     identifyUser calls posthog.identify(); resetIdentity calls
 *     posthog.reset(); track() forwards name + props verbatim.
 *
 * No test asserts on event-name strings beyond the literal whitelist —
 * the TypeScript discriminated union is the actual regression guard,
 * verified at typecheck time. (See `frontend/tests/analytics.types.ts`
 * for the negative-typecheck fixtures referenced from the
 * docstring-doctrine contract.)
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import posthog from 'posthog-js';
import {
  _testing,
  identifyUser,
  resetIdentity,
  setOptOut,
  track,
} from '@/lib/analytics';

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

beforeEach(() => {
  for (const fn of Object.values(mocked) as Array<{
    mockClear: () => void;
  }>) {
    if (typeof fn?.mockClear === 'function') fn.mockClear();
  }
  _testing.reset();
});

afterEach(() => {
  _testing.reset();
});

describe('analytics — disabled (no project key)', () => {
  test('track does nothing', () => {
    track('feature_used', { feature: 'dashboard' });
    expect(mocked.capture).not.toHaveBeenCalled();
  });

  test('setOptOut does nothing', () => {
    setOptOut(true);
    setOptOut(false);
    expect(mocked.opt_in_capturing).not.toHaveBeenCalled();
    expect(mocked.opt_out_capturing).not.toHaveBeenCalled();
    expect(mocked.reset).not.toHaveBeenCalled();
  });

  test('identifyUser does nothing', () => {
    identifyUser('user-123');
    expect(mocked.identify).not.toHaveBeenCalled();
  });

  test('resetIdentity does nothing', () => {
    resetIdentity();
    expect(mocked.reset).not.toHaveBeenCalled();
  });
});

describe('analytics — enabled (forced via _testing)', () => {
  beforeEach(() => {
    _testing.forceEnabled(true);
  });

  test('track forwards name + props verbatim', () => {
    track('chat_session_started', { conversation_id: 'abc' });
    expect(mocked.capture).toHaveBeenCalledTimes(1);
    expect(mocked.capture).toHaveBeenCalledWith('chat_session_started', {
      conversation_id: 'abc',
    });
  });

  test('setOptOut(true) opts out and resets distinct_id', () => {
    setOptOut(true);
    expect(mocked.opt_out_capturing).toHaveBeenCalledTimes(1);
    expect(mocked.reset).toHaveBeenCalledTimes(1);
    expect(mocked.opt_in_capturing).not.toHaveBeenCalled();
  });

  test('setOptOut(false) opts in', () => {
    setOptOut(false);
    expect(mocked.opt_in_capturing).toHaveBeenCalledTimes(1);
    expect(mocked.opt_out_capturing).not.toHaveBeenCalled();
    expect(mocked.reset).not.toHaveBeenCalled();
  });

  test('identifyUser forwards user_id with no PII fields', () => {
    identifyUser('user-abc');
    expect(mocked.identify).toHaveBeenCalledTimes(1);
    // Single positional arg — no email/name properties piggybacked.
    expect(mocked.identify).toHaveBeenCalledWith('user-abc');
  });

  test('resetIdentity drops the current distinct_id', () => {
    resetIdentity();
    expect(mocked.reset).toHaveBeenCalledTimes(1);
  });
});
