import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import { DeviceDisplacedModal } from './components/DeviceDisplacedModal';
import { UpdateToast } from './components/UpdateToast';
import { initAuth, startDeviceCheckPoll } from './lib/auth';
import Layout, { NotFoundPage } from './pages/_layout';
import HomePage from './pages/home';
import ChatPage from './pages/chat';
import CardsPage from './pages/cards';
import SubscriptionsPage from './pages/subscriptions';
import MemoryPage from './pages/memory';
import MorePage from './pages/more';
import SettingsPage from './pages/settings';
import ConnectionsPage from './pages/connections';
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
 */
function App() {
  const [authReady, setAuthReady] = useState(false);

  useEffect(() => {
    let pollHandle: number | null = null;
    initAuth().then(() => {
      setAuthReady(true);
      pollHandle = startDeviceCheckPoll();
    });
    return () => {
      if (pollHandle !== null) clearInterval(pollHandle);
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
          <Route path="/" element={<HomePage />} />
          <Route path="/cards" element={<CardsPage />} />
          <Route path="/subscriptions" element={<SubscriptionsPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/more" element={<MorePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/connections" element={<ConnectionsPage />} />
          <Route path="/privacy" element={<PrivacyPage />} />
          <Route path="/breakdown" element={<BreakdownPage />} />
          <Route path="/breakdown/:category" element={<CategoryListPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/onboarding" element={<OnboardingWizard />} />
          <Route path="/onboarding/tour" element={<TourPage />} />
          <Route path="*" element={<NotFoundPage />} />
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
