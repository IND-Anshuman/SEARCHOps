/**
 * Print a report through the browser's own print-to-PDF.
 *
 * Used when the server cannot render PDFs (the API-only deployment has no
 * Chromium). The workbench is a fixed-height, scrolling app shell, so printing
 * the page as-is would print one screenful of chrome. Instead the rendered
 * report is copied into a top-level `#print-root`, and the print stylesheet
 * (`index.css`, `@media print`) shows only that, set like the server export:
 * A4, serif body, ruled headings, bordered tables, cited sources.
 *
 * The page title becomes the report title for the duration, because browsers
 * use it as the default file name in "Save as PDF".
 */

const PRINT_ROOT_ID = 'print-root';

export function printReport(article: HTMLElement, title: string): void {
  document.getElementById(PRINT_ROOT_ID)?.remove();

  const root = document.createElement('div');
  root.id = PRINT_ROOT_ID;
  const copy = article.cloneNode(true) as HTMLElement;
  // Controls are for the screen: block menus, inline actions, inputs.
  copy.querySelectorAll('button, input, textarea, select, [data-print="hide"]').forEach((el) => el.remove());
  copy.removeAttribute('class');
  root.appendChild(copy);
  document.body.appendChild(root);

  const previousTitle = document.title;
  document.title = title.trim() || 'Research report';
  document.body.classList.add('printing-report');

  let cleaned = false;
  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;
    document.body.classList.remove('printing-report');
    document.title = previousTitle;
    root.remove();
    window.removeEventListener('afterprint', cleanup);
  };
  // Cleanup waits for afterprint, not for print() to return: in some browsers
  // print() returns at once while the dialog is still reading the page. Until
  // then the copy is invisible on screen (`#print-root` only shows in print).
  window.addEventListener('afterprint', cleanup);
  window.print();
}
