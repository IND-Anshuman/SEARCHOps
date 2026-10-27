import React, { useEffect, useState } from 'react';
import { Show, SignIn, useAuth } from '@clerk/react';
import { setTokenGetter, UNAUTHORIZED_EVENT } from '../shared/lib/api';

/**
 * Makes Clerk's session token available to non-React code (`apiFetch`, the
 * socket manager). Rendered once, inside `<ClerkProvider>`.
 */
const ClerkTokenBridge: React.FC = () => {
  const { getToken, isSignedIn } = useAuth();
  useEffect(() => {
    setTokenGetter(isSignedIn ? (options) => getToken(options) : null);
    return () => setTokenGetter(null);
  }, [getToken, isSignedIn]);
  return null;
};

/**
 * Signed out: Clerk's sign-in, set on the page like the rest of the product.
 * Signed in: the workbench. A 401 the API still returns after a token retry
 * means the server rejected this session (for example, a different Clerk
 * instance) and is shown rather than silently looping.
 */
export const SignInGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [rejected, setRejected] = useState(false);

  useEffect(() => {
    const onUnauthorized = () => setRejected(true);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  return (
    <>
      <ClerkTokenBridge />
      <Show when="signed-in">
        {rejected ? (
          <div role="alert" className="border-b border-rule-strong bg-paper-inset px-4 py-2 text-micro text-ink">
            The server did not accept this session. Check that the API's CLERK_PUBLISHABLE_KEY matches the
            frontend's, then reload.
            <button type="button" onClick={() => setRejected(false)} className="ml-2 text-ink-muted hover:text-ink">
              Dismiss
            </button>
          </div>
        ) : null}
        {children}
      </Show>
      <Show when="signed-out">
        <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-paper px-4 py-10 text-ink">
          <div className="text-center">
            <h1 className="font-display text-display-sm">SEARCHOps</h1>
            <p className="mt-1 text-label text-ink-muted">Sign in to run research and read your reports.</p>
          </div>
          <SignIn routing="hash" />
        </div>
      </Show>
    </>
  );
};
