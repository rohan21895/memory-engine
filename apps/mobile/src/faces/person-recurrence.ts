/**
 * Who actually belongs in this library, measured by how often they come back.
 *
 * The face scan resolved 17,699 faces into 2,173 people. Roughly 2,161 of those
 * clusters hold about six faces each, and in a family library shot at weddings,
 * parks and restaurants most of them are strangers -- other guests, passers-by,
 * people at the next table. Any album feature that needs to know "who matters
 * here" therefore cannot use face count, and REALLY cannot use rarity: ranking
 * by scarcity puts the stranger at the next table ahead of the owner's
 * daughter.
 *
 * Recurrence separates them cleanly and cheaply. A wedding guest appears on one
 * occasion however many frames they landed in; a family member turns up again
 * months later, and again after that. So this counts OCCASIONS, not photos and
 * not days: forty shutter presses of one stranger is one occasion, and a
 * three-day trip is still one occasion.
 *
 * Nothing here needs a model, a network call, or anything from the user.
 */

/** Days apart before two appearances count as separate occasions. */
const SESSION_GAP_MS = 14 * 24 * 60 * 60 * 1000;

/**
 * Occasions needed to be treated as familiar.
 *
 * Two, and the number means something rather than being tuned: turning up twice
 * more than a fortnight apart is not something a passer-by does. It is the
 * smallest bar that a single event -- however long, however many photos --
 * cannot clear on its own.
 */
const FAMILIAR_SESSION_FLOOR = 2;

export type PersonRecurrenceInput = {
  id: string;
  assetIds: readonly string[];
};

export type PersonRecurrence = {
  /** Distinct occasions this person appears on, library-wide. */
  sessionCount(personId: string): number;
  /** Distinct local calendar days this person appears on, library-wide. */
  dayCount(personId: string): number;
  /**
   * True when this person recurs across enough separate occasions to be treated
   * as someone in the owner's life rather than someone who was also there.
   */
  isFamiliar(personId: string): boolean;
  /**
   * Every person, most-recurring first. Always a total order, so a caller can
   * still rank people even in a library too short for anyone to clear the
   * floor -- a month-old library has no recurring faces yet, and that must
   * degrade to "best available" rather than to "nobody".
   */
  ranked(): readonly string[];
};

/**
 * Turns a photo index `YYYY-MM` bucket into a timestamp, so month-resolution
 * data can feed `buildPersonRecurrence` unchanged.
 *
 * The photo index stores a month per asset, not a capture time, and giving it
 * one would bump its version and force a full re-index. Feeding month starts
 * through the same code gives exactly month-resolution occasions: everything
 * inside one month collapses to a single day, and consecutive months are always
 * more than a fortnight apart so they stay separate occasions. That is the
 * honest resolution of what is stored -- two visits in one month count once.
 *
 * Coarse in the safe direction: it can only ever UNDERSTATE how much somebody
 * recurs, so it cannot promote a stranger. When per-asset capture times exist,
 * passing them instead gives finer occasions with no change here.
 */
export function monthStartMs(monthId: string | null | undefined): number | undefined {
  if (typeof monthId !== "string") return undefined;
  const match = /^(\d{4})-(\d{2})$/u.exec(monthId);
  if (!match) return undefined;
  const year = Number(match[1]);
  const month = Number(match[2]);
  if (month < 1 || month > 12) return undefined;
  return new Date(year, month - 1, 1, 12).getTime();
}

/** Local calendar day. Photos are taken in local time; a UTC day boundary would
 *  split one evening in two and inflate an occasion into several. */
function localDay(timestamp: number): number {
  const date = new Date(timestamp);
  return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
}

/**
 * Builds the recurrence lookup.
 *
 * `capturedAtForAsset` is supplied by the caller rather than read here, because
 * the authoritative capture time lives in the photo index. The face index's own
 * observations are NOT a safe source: capture times were added to them without
 * bumping the index version, so every face recorded before that change has none
 * and a rebuild cannot recover it.
 */
export function buildPersonRecurrence(
  people: readonly PersonRecurrenceInput[],
  capturedAtForAsset: (assetId: string) => number | undefined,
): PersonRecurrence {
  const sessions = new Map<string, number>();
  const days = new Map<string, number>();

  for (const person of people) {
    const seen = new Set<number>();
    for (const assetId of person.assetIds) {
      const capturedAt = capturedAtForAsset(assetId);
      if (typeof capturedAt !== "number" || !Number.isFinite(capturedAt) || capturedAt <= 0) {
        continue;
      }
      seen.add(localDay(capturedAt));
    }
    const sorted = [...seen].sort((a, b) => a - b);
    let occasions = 0;
    let previous: number | undefined;
    for (const day of sorted) {
      if (previous === undefined || day - previous > SESSION_GAP_MS) occasions += 1;
      previous = day;
    }
    sessions.set(person.id, occasions);
    days.set(person.id, sorted.length);
  }

  // Ordered once, not per query. Ties break on days and then on id, so the
  // order is total and stable between runs -- an album that reshuffles its
  // people on every open looks broken even when every pick is defensible.
  const order = people
    .map((person) => person.id)
    .sort((a, b) => {
      const bySession = (sessions.get(b) ?? 0) - (sessions.get(a) ?? 0);
      if (bySession !== 0) return bySession;
      const byDay = (days.get(b) ?? 0) - (days.get(a) ?? 0);
      if (byDay !== 0) return byDay;
      return a < b ? -1 : a > b ? 1 : 0;
    });

  return {
    sessionCount: (personId) => sessions.get(personId) ?? 0,
    dayCount: (personId) => days.get(personId) ?? 0,
    isFamiliar: (personId) => (sessions.get(personId) ?? 0) >= FAMILIAR_SESSION_FLOOR,
    ranked: () => order,
  };
}
