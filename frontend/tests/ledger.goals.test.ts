/**
 * ledger goals — schedule/undo delete parity test.
 *
 * Verifies that the goal-delete undo window mirrors the cards/memory
 * pattern: scheduling a delete puts the goal id into
 * `pendingGoalDeletes` (and keeps the row visible), and `undoDeleteGoal`
 * clears it before the timer commits. The timer-fire commit path itself
 * is intentionally NOT exercised here — that's parity with the cards/
 * memory implementations the backend route tests cover end-to-end.
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

vi.mock('@/lib/goalsApi', async () => {
  const actual = await vi.importActual<typeof import('@/lib/goalsApi')>(
    '@/lib/goalsApi',
  );
  return {
    ...actual,
    listGoals: vi.fn(),
    patchGoal: vi.fn(),
    deleteGoal: vi.fn(async () => undefined),
  };
});

import { listGoals, patchGoal } from '@/lib/goalsApi';
import { ledger } from '@/lib/ledger';
import type { Goal, GoalWithSpend } from '@/lib/goalsApi';
import { useAppStore } from '@/store';

const listGoalsMock = vi.mocked(listGoals);
const patchGoalMock = vi.mocked(patchGoal);

function makeGoal(id: string, category: string | null): GoalWithSpend {
  return {
    goal: {
      id,
      user_id: 'u1',
      category,
      amount: '300.00',
      period: 'month',
      created_at: '2026-05-01T00:00:00Z',
      updated_at: '2026-05-01T00:00:00Z',
    },
    spent_period_to_date: '42.00',
    window_start: '2026-05-01',
    window_end: '2026-05-31',
    progress_ratio: 0.14,
  };
}

async function seedGoals(goals: GoalWithSpend[]) {
  listGoalsMock.mockResolvedValueOnce({ items: goals });
  await ledger.refreshGoals();
}

describe('ledger.scheduleDeleteGoal / undoDeleteGoal', () => {
  beforeEach(async () => {
    useAppStore.setState({ jwt: 'test-jwt', deviceId: 'test-device' });
    await seedGoals([]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  test('scheduleDeleteGoal populates pendingGoalDeletes; undo clears it', async () => {
    await seedGoals([makeGoal('g-1', 'Dining')]);
    expect(ledger.getSnapshot().goals).toHaveLength(1);
    expect(ledger.getSnapshot().pendingGoalDeletes).toEqual({});

    ledger.scheduleDeleteGoal('g-1', 5000);
    expect(ledger.getSnapshot().pendingGoalDeletes['g-1']).toMatchObject({
      id: 'g-1',
      durationMs: 5000,
    });
    // Row STAYS visible during the undo window so the UI can render the
    // countdown — identical to scheduleDeleteCard.
    expect(ledger.getSnapshot().goals).toHaveLength(1);

    ledger.undoDeleteGoal('g-1');
    expect(ledger.getSnapshot().pendingGoalDeletes).toEqual({});
    expect(ledger.getSnapshot().goals).toHaveLength(1);
  });

  test('scheduleDeleteGoal is idempotent — second call on pending id no-ops', async () => {
    await seedGoals([makeGoal('g-2', null)]);
    ledger.scheduleDeleteGoal('g-2', 5000);
    const firstScheduledAt =
      ledger.getSnapshot().pendingGoalDeletes['g-2'].scheduledAt;
    await new Promise((r) => setTimeout(r, 5));
    ledger.scheduleDeleteGoal('g-2', 5000);
    expect(
      ledger.getSnapshot().pendingGoalDeletes['g-2'].scheduledAt,
    ).toBe(firstScheduledAt);
    ledger.undoDeleteGoal('g-2');
  });

  test('scheduleDeleteGoal on unknown id is a no-op', async () => {
    await seedGoals([]);
    ledger.scheduleDeleteGoal('does-not-exist', 5000);
    expect(ledger.getSnapshot().pendingGoalDeletes).toEqual({});
  });
});

describe('ledger.updateGoal', () => {
  beforeEach(async () => {
    useAppStore.setState({ jwt: 'test-jwt', deviceId: 'test-device' });
    listGoalsMock.mockResolvedValueOnce({ items: [] });
    await ledger.refreshGoals();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  test('amount-only edit recomputes progress_ratio locally', async () => {
    // Regression for Codex finding: previously the ratio carried over
    // from the prior amount, so the bar showed stale fill until a
    // full refresh.
    const prior = makeGoal('g-amt', 'Dining');
    prior.goal.amount = '200.00';
    prior.spent_period_to_date = '50.00';
    prior.progress_ratio = 0.25; // 50/200
    await seedGoals([prior]);

    const updated: Goal = { ...prior.goal, amount: '100.00' };
    patchGoalMock.mockResolvedValueOnce(updated);

    await ledger.updateGoal('g-amt', { amount: '100.00' });

    const after = ledger.getSnapshot().goals.find((g) => g.goal.id === 'g-amt');
    expect(after).toBeDefined();
    expect(after!.goal.amount).toBe('100.00');
    // 50 / 100 = 0.5 — recomputed from spent on PATCH, not stale 0.25.
    expect(after!.progress_ratio).toBeCloseTo(0.5);
  });

  test('refreshGoals rethrows on API failure so the page error UI fires', async () => {
    // Regression for Codex finding: the previous implementation
    // swallowed every error and `console.warn`'d, which left the
    // /goals page's try/catch as dead code and rendered an empty
    // state on a real auth/network failure.
    listGoalsMock.mockRejectedValueOnce(new Error('boom'));
    await expect(ledger.refreshGoals()).rejects.toThrow('boom');
  });

  test('amount edit to zero or negative leaves ratio untouched', async () => {
    // Defensive: the backend rejects non-positive amounts (422), but
    // if the optimistic apply ever sees one we keep the prior ratio
    // rather than producing NaN/Infinity.
    const prior = makeGoal('g-zero', 'Dining');
    prior.spent_period_to_date = '50.00';
    prior.progress_ratio = 0.5;
    await seedGoals([prior]);

    // Force the patch through optimistically — we never hit the real
    // server, just observe the ratio choice on the local update.
    patchGoalMock.mockResolvedValueOnce({ ...prior.goal });
    await ledger.updateGoal('g-zero', { amount: '0' });

    const after = ledger.getSnapshot().goals.find(
      (g) => g.goal.id === 'g-zero',
    );
    expect(after!.progress_ratio).toBe(0.5);
  });
});
