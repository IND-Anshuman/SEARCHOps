import React from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { str } from '../shared/lib/safe';
import { Bell, X } from 'lucide-react';
import { StatusMarkGlyph } from '../shared/components/StatusMark';

/**
 * Notification drawer.
 *
 * This used to be `return null` - a component that consumed an open/closed
 * store flag and rendered nothing, so the flag was dead state. It now shows
 * the run's error and warning events, which is the only signal it has that a
 * user actually needs surfaced.
 */
export const NotificationCenter: React.FC = () => {
  const { isNotificationCenterOpen, setNotificationCenterOpen } = useWorkbenchStore();
  const { logs, job } = useExecutionStore();

  if (!isNotificationCenterOpen) return null;

  const alerts = logs.filter(l => l.level === 'error' || l.level === 'warn');
  const jobFailed = job.status === 'failed';

  return (
    <div className="fixed right-0 top-10 bottom-0 w-80 bg-paper-raised border-l border-rule z-40 flex flex-col shadow-drawer">
      <div className="px-3 py-3 border-b border-rule flex items-center justify-between">
        <div className="flex items-center gap-2 text-ink">
          <Bell className="w-3.5 h-3.5 text-ink-faint" />
          <span className="text-label font-medium">Notifications</span>
        </div>
        <button
          type="button"
          onClick={() => setNotificationCenterOpen(false)}
          className="p-1 text-ink-faint hover:text-ink hover:bg-paper rounded-md transition-colors"
          aria-label="Close notifications"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {alerts.length === 0 && !jobFailed ? (
          <p className="text-micro text-ink-faint leading-relaxed px-1 py-4">
            No warnings or errors in this run.
          </p>
        ) : null}

        {jobFailed ? (
          <div className="border border-rule-strong bg-paper-inset rounded-md px-2.5 py-2">
            <p className="text-micro text-ink leading-relaxed">
              The run finished with an error.
            </p>
          </div>
        ) : null}

        {alerts.map(log => (
          <div
            key={log.id}
            className="border border-rule bg-paper-inset px-2.5 py-2 flex items-start gap-2"
          >
            {/* The level, as a mark: empty for an error, half for a warning.
                No colour is needed to tell them apart. */}
            <StatusMarkGlyph
              status={log.level === 'error' ? 'empty' : 'half'}
              className="text-ink mt-[3px]"
            />
            <div className="min-w-0 flex-1">
              <p className="text-micro text-ink-faint font-mono truncate">
                {str(log.eventType) || 'EVENT'}
              </p>
              <p className="text-micro text-ink-muted leading-relaxed mt-1">
                {str(log.payload?.error) || str(log.payload?.message) || JSON.stringify(log.payload ?? {})}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
export default NotificationCenter;
