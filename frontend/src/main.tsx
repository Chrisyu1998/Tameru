import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import { DeviceDisplacedModal } from './components/DeviceDisplacedModal';
import { UpdateToast } from './components/UpdateToast';
import { initAuth, startDeviceCheckPoll } from './lib/auth';
import { ConfirmHomeCurrency } from './pages/ConfirmHomeCurrency';
import { Home } from './pages/Home';
import { SignIn } from './pages/SignIn';
import { Splash } from './pages/Splash';
import './index.css';

/*
 * Routes (Day 7):
 *   /                  Splash + post-auth dispatcher (also OAuth landing)
 *   /signin            Google OAuth + magic-link disclosure
 *   /confirm-currency  one-time home-currency picker (immutable post-confirm)
 *   /home              dashboard placeholder (real screen lands Day 15)
 *
 * The shell awaits initAuth() before rendering routes so the first paint
 * already reflects the persisted Supabase session — without this, Splash
 * would briefly see jwt=null on a refresh and bounce the user to /signin
 * before the session loaded.
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
    // Same visual as Splash, but unrouted — we don't want to mount Splash's
    // dispatch effect before the store is hydrated, or it would prematurely
    // route an authenticated refresh to /signin.
    return (
      <main className="flex min-h-dvh items-center justify-center bg-canvas px-6">
        <h1 className="font-display text-5xl text-primary">tameru</h1>
      </main>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Splash />} />
        <Route path="/signin" element={<SignIn />} />
        <Route path="/confirm-currency" element={<ConfirmHomeCurrency />} />
        <Route path="/home" element={<Home />} />
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
