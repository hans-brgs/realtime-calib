import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import '@fontsource-variable/sora';
import '@fontsource-variable/manrope';

import App from '@/App';
import '@/index.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('root element not found');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
