import React from 'react';

/**
 * The status mark.
 *
 * The product has no colour, so a status is carried by the FILL PATTERN of
 * its mark, exactly the way a printed form distinguishes its checkboxes:
 *
 *   ■  filled   — supported: settled, certain
 *   ▨  half     — contested: held open, in question
 *   □  empty    — refuted: emptied out, negated
 *   ░  dotted   — unverified: not yet filled
 *   —  rule     — not measured: no mark at all
 *
 * A reader who cannot see colour still reads the status correctly, and a
 * reader who can sees no meaning carried by hue — so nothing is lost and
 * the product never has to choose an accent.
 */
export type StatusMark = 'filled' | 'half' | 'empty' | 'dotted' | 'none';

const MARK_CLASS: Record<StatusMark, string> = {
  filled: 'mark mark-filled',
  half: 'mark mark-half',
  empty: 'mark mark-empty',
  dotted: 'mark mark-dotted',
  none: 'mark mark-none',
};

export const StatusMarkGlyph: React.FC<{
  status: StatusMark;
  className?: string;
  /** Optional accessible name. A mark with no meaning to a screen reader is
      a decoration, so pass one wherever the mark carries a state alone. */
  title?: string;
}> = ({ status, className = '', title }) => (
  <span
    aria-hidden={title ? undefined : true}
    title={title}
    className={`${MARK_CLASS[status]} ${className}`}
  />
);

interface StatusCountProps {
  /** The mark that carries this status. */
  status: StatusMark;
  /** How many. Always tabular. */
  value: number;
  /** The word. Always present — a mark without a label is not a status. */
  label: string;
  className?: string;
}

/**
 * A counted status: a mark, a number, and a word.
 *
 * The mark says what kind of thing it is, the number says how many, and the
 * word says which. Remove any one of the three and the reader is guessing.
 */
export const StatusCount: React.FC<StatusCountProps> = ({
  status,
  value,
  label,
  className = '',
}) => (
  <span className={`inline-flex items-center gap-1.5 ${className}`}>
    <StatusMarkGlyph status={status} className="text-ink" />
    <span className="font-mono tnum text-ink font-medium">{value}</span>
    <span className="text-ink-muted">{label}</span>
  </span>
);

/** Map a claim status onto its mark. */
export function markForStatus(status: string): StatusMark {
  switch (status) {
    case 'supported':
      return 'filled';
    case 'contested':
      return 'half';
    case 'refuted':
      return 'empty';
    case 'unverified':
      return 'dotted';
    default:
      return 'none';
  }
}

export default StatusMarkGlyph;
