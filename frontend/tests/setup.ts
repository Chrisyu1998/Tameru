import '@testing-library/jest-dom/vitest';
import 'fake-indexeddb/auto';
// Initialize i18next (Tier 2b) so components using `useTranslation()` resolve
// real strings in tests instead of raw keys. jsdom's navigator.language is
// en-US, so the instance defaults to English — matching what the assertions
// (written against English copy) expect.
import '../src/lib/i18n';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
});
