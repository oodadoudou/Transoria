import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { EpubEditorWindow } from './pages/general-tools/EpubEditorWindow';
import './theme/tokens.css';
import './theme/global.css';

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element #root not found');
}

createRoot(rootElement).render(
  <StrictMode>
    {new URLSearchParams(window.location.search).get('epub-editor') === '1' ? <EpubEditorWindow /> : <App />}
  </StrictMode>,
);
