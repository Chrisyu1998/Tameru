import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';

import { UpdateToast } from './components/UpdateToast';
import { Home } from './pages/Home';
import { Splash } from './pages/Splash';
import './index.css';

/*
 * Two routes today (Day 6 scaffold). Day 7 adds /signin and /confirm-currency
 * for auth + home-currency capture.
 */
const container = document.getElementById('root');
if (!container) throw new Error('Root element #root missing from index.html');

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Splash />} />
        <Route path="/home" element={<Home />} />
      </Routes>
      <UpdateToast />
    </BrowserRouter>
  </StrictMode>,
);
