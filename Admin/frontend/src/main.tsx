import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { AppErrorBoundary } from './components/AppErrorBoundary.tsx'
import { registerGlobalErrorListeners } from './lib/error-report.ts'

// Module level, so StrictMode's double mount cannot register the listeners twice.
registerGlobalErrorListeners()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </StrictMode>,
)
