import React, { useState, useEffect } from 'react';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { money, num, str } from '../shared/lib/safe';
import { Command, Bell, Sun, Moon } from 'lucide-react';
import { UserButton } from '@clerk/react';
import { StatusMarkGlyph } from '../shared/components/StatusMark';

/**
 * Top bar.
 *
 * A letterpress masthead, not a dashboard ornament. The product's name is set
 * in the display face because it is a *title*, the way the title of a paper is
 * a title — and the mark beside it is a struck rule, not a gradient tile.
 *
 * The previous version stacked five competing chrome elements — a brand chip,
 * a "WS ONLINE" badge, a job chip, a token gauge and a cost readout — all at
 * the same weight, so nothing carried meaning. Now: the masthead, the run's
 * status as the only emphasised state, and telemetry demoted to quiet
 * secondary text.
 */
export const Header: React.FC = () => {
  const { job, connectionStatus, logs } = useExecutionStore();
  const { setCommandPaletteOpen, setNotificationCenterOpen } = useWorkbenchStore();

  const [isDark, setIsDark] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    const stored = localStorage.getItem('searchops:theme');
    if (stored) return stored === 'dark';
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  });

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark');
      localStorage.setItem('searchops:theme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      localStorage.setItem('searchops:theme', 'light');
    }
  }, [isDark]);

  const toggleTheme = () => setIsDark(prev => !prev);

  const tokenBudget = num(job.tokenBudget, 150000);
  const tokenUsed = num(job.tokenUsed);
  const tokenPercent = tokenBudget > 0 ? Math.min(Math.round((tokenUsed / tokenBudget) * 100), 100) : 0;
  const costCurrent = money(job.costCurrent);
  const errorCount = logs.filter(l => l.level === 'error').length;

  const hasJob = Boolean(str(job.id));

  const status = (() => {
    switch (job.status) {
      case 'running':
      case 'planning':
      case 'verifying':
        return { label: 'Running', mark: 'dotted' as const, pulse: true };
      case 'completed':
        return { label: 'Complete', mark: 'filled' as const, pulse: false };
      case 'failed':
        return { label: 'Failed', mark: 'empty' as const, pulse: false };
      case 'paused':
        return { label: 'Paused', mark: 'half' as const, pulse: false };
      default:
        return { label: 'Idle', mark: 'none' as const, pulse: false };
    }
  })();

  return (
    <header className="h-10 shrink-0 bg-paper-raised header-gradient border-b border-rule flex items-center gap-5 px-5 select-none z-30 shadow-sm">
      {/* Masthead. The mark is a struck rule — two weights, the way a letterpress
          rule and a hairline coexist on a printed page. */}
      <div className="flex items-baseline gap-2.5 min-w-0 shrink-0">
        <span
          aria-hidden="true"
          className="self-center w-[6px] h-[14px] bg-ink shrink-0"
          style={{ clipPath: 'polygon(0 0, 100% 0, 100% 62%, 0 62%, 0 78%, 100% 78%, 100% 100%, 0 100%)' }}
        />
        <span className="font-display text-[15px] font-semibold tracking-[-0.01em] text-ink leading-none">
          SearchOps
        </span>
      </div>

      <span aria-hidden="true" className="h-5 w-px bg-rule shrink-0 hidden sm:block" />

      {/* Run status: the one emphasised piece of state */}
      <div className="hidden sm:flex items-center gap-2 text-label text-ink-muted min-w-0">
        <StatusMarkGlyph
          status={status.mark}
          className={`shrink-0 text-ink ${status.pulse ? 'animate-breathe' : ''}`}
        />
        <span className="shrink-0 text-ink font-medium">{status.label}</span>
        {hasJob && (
          <>
            <span className="text-rule-strong shrink-0" aria-hidden="true">·</span>
            <span className="truncate font-mono text-micro text-ink-faint tnum">{str(job.id).slice(0, 8)}</span>
          </>
        )}
        {job.topic && (
          <span className="truncate text-ink-muted max-w-[26ch]">{str(job.topic)}</span>
        )}
      </div>

      <div className="flex-1" />

      {/* Telemetry: quiet, secondary. A measured number with its label, never a
          gauge with no reading. */}
      {hasJob && (
        <div className="hidden md:flex items-center gap-5 text-micro text-ink-faint">
          <span className="flex items-center gap-1.5" title="Tokens used against the run's budget">
            <span className="font-mono tnum text-ink-muted">
              {tokenUsed.toLocaleString()}
            </span>
            <span className="font-mono tnum">/ {tokenBudget.toLocaleString()}</span>
            <span>tok</span>
            <span className="inline-block w-9 h-[3px] bg-paper-deep overflow-hidden align-middle rounded-pill">
              <span
                className={`block h-full ${tokenPercent > 85 ? 'bg-ink' : 'bg-ink-muted'}`}
                style={{ width: `${tokenPercent}%` }}
              />
            </span>
          </span>
          <span className="flex items-center gap-1.5" title="Cost so far for this run">
            <span className="font-mono tnum text-ink-muted">{costCurrent}</span>
            <span>USD</span>
          </span>
        </div>
      )}

      {/* The live link to a running job. It says what it is: a stream.
          "Offline" here and "Connected" in the rail meant different things
          and read identically, which is worse than either word alone. */}
      <div
        className="hidden lg:flex items-center gap-1.5 text-micro text-ink-faint"
        title={connectionStatus === 'connected'
          ? 'Streaming updates from a running job'
          : 'Not streaming — no job is running'}
      >
        <StatusMarkGlyph
          status={connectionStatus === 'connected' ? 'filled' : 'none'}
          className="text-ink"
        />
        {connectionStatus === 'connected' ? 'Streaming' : 'Not streaming'}
      </div>

      {/* Actions */}
      <button
        type="button"
        onClick={() => setCommandPaletteOpen(true)}
        className="flex items-center gap-2 text-label text-ink-muted hover:text-ink border border-rule bg-paper rounded-md px-2.5 py-1.5 transition-colors hover:border-rule-strong hover:bg-paper-raised"
        aria-label="Open command palette"
      >
        <Command className="w-3.5 h-3.5" />
        <span className="hidden sm:inline">Search</span>
        <kbd className="hidden md:inline text-micro text-ink-faint font-mono">Ctrl K</kbd>
      </button>

      <button
        type="button"
        onClick={() => setNotificationCenterOpen(true)}
        className="relative text-ink-muted hover:text-ink border border-rule bg-paper rounded-md p-1.5 transition-colors hover:border-rule-strong hover:bg-paper-raised"
        aria-label="Open notifications"
      >
        <Bell className="w-3.5 h-3.5" />
        {errorCount > 0 && (
          <span className="absolute -top-1.5 -right-1.5 min-w-[15px] h-[15px] px-[3px] rounded-pill bg-ink text-paper-raised text-[9px] font-semibold flex items-center justify-center tnum">
            {errorCount > 9 ? '9+' : errorCount}
          </span>
        )}
      </button>

      <button
        type="button"
        onClick={toggleTheme}
        title={isDark ? 'Switch to Light paper archive' : 'Switch to Dark carbon intaglio'}
        className="text-ink-muted hover:text-ink border border-rule bg-paper hover:bg-paper-raised p-1.5 rounded-md transition-all tactile-btn hover:border-rule-strong shadow-sm"
        aria-label="Toggle color theme"
      >
        {isDark ? <Sun className="w-3.5 h-3.5 text-ink" /> : <Moon className="w-3.5 h-3.5 text-ink" />}
      </button>

      {/* Account: profile and sign-out, from Clerk. */}
      <UserButton />
    </header>
  );
};

export default Header;
