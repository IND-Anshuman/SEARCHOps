/**
 * Whether Clerk sign-in is on.
 *
 * Paused by default: the workbench opens straight away and the API must run
 * with AUTH_DISABLED_EXPLICITLY=true to match. Set VITE_AUTH_ENABLED=true
 * (with VITE_CLERK_PUBLISHABLE_KEY) to bring sign-in back.
 */
export const AUTH_ENABLED = import.meta.env.VITE_AUTH_ENABLED === 'true';
