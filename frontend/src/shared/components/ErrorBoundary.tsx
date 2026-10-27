import React from 'react';

interface Props {
  children: React.ReactNode;
  /** Rendered instead of children when a descendant throws. */
  fallback?: React.ReactNode;
}

interface State {
  hasError: boolean;
  message: string;
}

/**
 * Last line of defence against the blank screen.
 *
 * Without this, any exception thrown while a view renders unmounts the entire
 * React tree — the user sees a fully blank page with nothing to click and no
 * error message, because React 18 removes the root on an uncaught render
 * error. It happened whenever one job field was absent or partial.
 *
 * This does not replace fixing the underlying guards: a boundary is a net, and
 * a view behind a net is still a view that failed. Views must still fail
 * visibly on their own data.
 */
export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false, message: '' };

  static getDerivedStateFromError(err: unknown): State {
    return { hasError: true, message: err instanceof Error ? err.message : String(err) };
  }

  componentDidCatch(err: unknown, info: React.ErrorInfo): void {
    // Keep the failure observable in the console; the UI shows the fallback.
    console.error('[ErrorBoundary] view crashed:', err, info.componentStack);
  }

  render(): React.ReactNode {
    if (!this.state.hasError) return this.props.children;

    return (
      <div
        role="alert"
        className="flex-1 flex flex-col items-center justify-center gap-2 p-8 text-center bg-paper"
      >
        {/* A failed view is a real fault, so it wears the alarm status. */}
        <p className="font-display text-body-lg text-ink">This view failed to render</p>
        <p className="text-micro text-ink-muted font-mono max-w-md break-words">
          {this.state.message || 'Unknown error.'}
        </p>
        <button
          type="button"
          onClick={() => this.setState({ hasError: false, message: '' })}
          className="mt-2 text-label text-ink border border-rule bg-paper-raised hover:bg-paper-inset px-3 py-1.5 rounded-md transition-colors"
        >
          Try again
        </button>
      </div>
    );
  }
}

export default ErrorBoundary;
