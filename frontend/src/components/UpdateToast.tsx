import { useRegisterSW } from 'virtual:pwa-register/react';

/*
 * Service-worker update toast. With registerType: 'prompt' (vite.config.ts),
 * the SW waits for our signal before activating a new version — giving the
 * user a visible "New version available" moment instead of a silent swap.
 *
 * Kept intentionally tiny: this isn't the place for a toast framework.
 */
export function UpdateToast() {
  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisterError(err: unknown) {
      // Dev and non-HTTPS origins will fail here; not actionable.
      // eslint-disable-next-line no-console
      console.warn('Service worker registration failed', err);
    },
  });

  if (!needRefresh) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-4 bottom-4 z-50 flex items-center justify-between gap-3 rounded-2xl border border-hairline bg-elevated px-4 py-3"
    >
      <span className="text-sm text-ink">
        a new version is available.
      </span>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => setNeedRefresh(false)}
          className="text-sm text-ink-tertiary"
        >
          later
        </button>
        <button
          type="button"
          onClick={() => void updateServiceWorker(true)}
          className="text-sm font-medium text-moss-deep"
        >
          refresh
        </button>
      </div>
    </div>
  );
}
