import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom';

import { DeviceDisplacedModal } from './components/DeviceDisplacedModal';
import { UpdateToast } from './components/UpdateToast';
import { initAuth, startDeviceCheckPoll } from './lib/auth';
import { initAnalytics } from './lib/analytics';
import { setupAutoDrain } from './lib/offline_queue';
import { useAppStore } from './store';
import Layout, { NotFoundPage } from './pages/_layout';
import HomePage from './pages/home';
import ChatPage from './pages/chat';
import CardsPage from './pages/cards';
import SubscriptionsPage from './pages/subscriptions';
import MemoryPage from './pages/memory';
import GoalsPage from './pages/goals';
import MorePage from './pages/more';
import SettingsPage from './pages/settings';
import ConnectionsPage from './pages/connections';
import OauthConsentPage from './pages/oauth.consent';
import PrivacyPage from './pages/privacy';
import BreakdownPage from './pages/breakdown.index';
import CategoryListPage from './pages/breakdown.category';
import OnboardingWizard from './pages/onboarding';
import TourPage from './pages/onboarding.tour';
import './index.css';

/*
 * The shell awaits initAuth() before mounting routes so first paint already
 * reflects the persisted Supabase session — without this, a refresh would
 * briefly see jwt=null and trigger an onboarding/signin bounce. The route
 * layout itself comes from pages/_layout.tsx (imported from the Lovable
 * mock); the auth wiring above it stays ours so invariants 1 + 5 hold.
 *
 * RequireOnboarded replaces the Day 7 Splash dispatcher that the Lovable
 * import dropped. Everything except /onboarding/* sits behind it: a user
 * without a JWT, or with a JWT but no home_currency yet, is redirected to
 * /onboarding (the wizard itself picks the right step from store state).
 * Without this gate, signed-in-but-unbootstrapped users land on Home,
 * which calls /transactions and 401s with DEVICE_DISPLACED because no
 * users_meta row exists yet.
 */
function RequireOnboarded() {
  const jwt = useAppStore((s) => s.jwt);
  const homeCurrency = useAppStore((s) => s.homeCurrency);
  if (!jwt || typeof homeCurrency !== 'string') {
    return <Navigate to="/onboarding" replace />;
  }
  return <Outlet />;
}

function App() {
  const [authReady, setAuthReady] = useState(false);

  useEffect(() => {
    let pollHandle: number | null = null;
    let teardownDrain: (() => void) | null = null;
    // Init PostHog FIRST (synchronous) and BEFORE initAuth fires off the
    // /me fetch. The SDK starts opted out via opt_out_capturing_by_default;
    // initAuth's refreshHomeCurrency() flips that to opted-in after /me
    // confirms analytics_opted_out === false. The strict ordering is the
    // load-bearing piece of the leak-free-init invariant (Day 26).
    initAnalytics();
    initAuth().then(() => {
      setAuthReady(true);
      pollHandle = startDeviceCheckPoll();
      // Offline-queue drain: listens for the `online` event, drains
      // queued confirms on app mount when already online, and rebinds the
      // banner count on auth changes. Window-scope (not service worker) —
      // see offline_queue.ts header.
      teardownDrain = setupAutoDrain();
    });
    return () => {
      if (pollHandle !== null) clearInterval(pollHandle);
      if (teardownDrain) teardownDrain();
    };
  }, []);

  if (!authReady) {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-canvas px-6">
        <h1 className="font-serif text-5xl text-ink lowercase-title">tameru</h1>
      </main>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          {/* Always reachable — the gate's destination and the 404. */}
          <Route path="/onboarding" element={<OnboardingWizard />} />
          <Route path="/onboarding/tour" element={<TourPage />} />
          {/* OAuth consent — Supabase redirects here with ?authorization_id=.
              Not the OAuth authorize endpoint itself (that's on Supabase's
              Auth Server); this is the consent UI Supabase delegates to.
              Not behind RequireOnboarded: a signed-in Supabase user without
              completed Tameru onboarding is an edge case the consent page
              handles directly (Day 23b). */}
          <Route path="/oauth/consent" element={<OauthConsentPage />} />
          <Route path="*" element={<NotFoundPage />} />
          {/* Gated: require a JWT and a confirmed home_currency. */}
          <Route element={<RequireOnboarded />}>
            <Route path="/" element={<HomePage />} />
            <Route path="/cards" element={<CardsPage />} />
            <Route path="/subscriptions" element={<SubscriptionsPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/goals" element={<GoalsPage />} />
            <Route path="/more" element={<MorePage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/connections" element={<ConnectionsPage />} />
            <Route path="/privacy" element={<PrivacyPage />} />
            <Route path="/breakdown" element={<BreakdownPage />} />
            <Route path="/breakdown/:category" element={<CategoryListPage />} />
            <Route path="/chat" element={<ChatPage />} />
          </Route>
        </Route>
      </Routes>
      <DeviceDisplacedModal />
      <UpdateToast />
    </BrowserRouter>
  );
}

const container = document.getElementById('root');
if (!container) throw new Error('Root element #root missing from index.html');

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
