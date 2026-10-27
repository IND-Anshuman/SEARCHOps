import React from 'react'
import ReactDOM from 'react-dom/client'
import { ClerkProvider } from '@clerk/react'
import App from './App'
import { SignInGate } from './features/SignInGate'
import { AUTH_ENABLED } from './shared/lib/authMode'
import './index.css'

const CLERK_PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

const root = ReactDOM.createRoot(document.getElementById('root')!)

if (!AUTH_ENABLED) {
  // Sign-in is paused: open the workbench directly.
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
} else if (!CLERK_PUBLISHABLE_KEY) {
  // Fail visibly: without Clerk there is no way to sign in.
  root.render(
    <div className="flex h-screen items-center justify-center bg-paper px-4 text-ink">
      <p className="max-w-md text-center text-label">
        Sign-in is not configured. Set <code>VITE_CLERK_PUBLISHABLE_KEY</code> in <code>frontend/.env.local</code>{' '}
        and restart the dev server.
      </p>
    </div>,
  )
} else {
  root.render(
    <React.StrictMode>
      <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY} afterSignOutUrl="/app/">
        <SignInGate>
          <App />
        </SignInGate>
      </ClerkProvider>
    </React.StrictMode>,
  )
}
