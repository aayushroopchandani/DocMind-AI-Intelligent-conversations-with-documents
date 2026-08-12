/**
 * Lets "File → Rename project…" put the caret in the app bar's project-name
 * field without threading a ref through the workspace context.
 *
 * The field registers itself while mounted; the menu just asks for focus and
 * does nothing if the app bar is not on screen.
 */

type FocusHandler = () => void;

let handler: FocusHandler | null = null;

/** Called by the project-name field on mount; returns its unregister. */
export function registerProjectNameFocus(next: FocusHandler): () => void {
  handler = next;
  return () => {
    if (handler === next) handler = null;
  };
}

export function focusProjectName(): void {
  handler?.();
}
