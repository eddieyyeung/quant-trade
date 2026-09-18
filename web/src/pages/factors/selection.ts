// The factor selection ticks on the library page.
//
// Deliberately browser-local: it is a convenience that pre-fills the pickers on
// the other factor pages, not a setting the backend acts on. Writing it to the
// config file would turn a checked-in YAML into runtime state, and there is no
// consumer for a stored "enabled" flag yet — the strategies read their own
// factor list.

const KEY = 'quant-trade.factor-selection';

/** The selected factor names, or an empty list when nothing is stored or readable. */
export function readSelectedFactors(): string[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((name): name is string => typeof name === 'string') : [];
  } catch {
    // Private windows and blocked storage throw; a missing selection is not an error.
    return [];
  }
}

/** Persist the selection. Storage failures are ignored — this is a convenience. */
export function writeSelectedFactors(names: string[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(names));
  } catch {
    // Ignored on purpose: see readSelectedFactors.
  }
}
