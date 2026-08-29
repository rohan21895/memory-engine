/**
 * What the user answered before an album was built.
 *
 * THE CONTRACT between the setup UI and the selector. The questionnaire fills
 * this in; `buildAlbum` passes it through unchanged; the planner reads it. It is
 * deliberately the only shape that crosses that line, so the UI never needs to
 * know what a planner policy is and the planner never needs to know what a
 * screen looks like.
 */

/**
 * How much of the album a person should be.
 *
 * Stored as high/medium/low because that is what the planner reasons about.
 * The words the user sees are the UI's business and are deliberately warmer --
 * "Main focus", "Include", "Background only" -- but those must never become the
 * stored values, or renaming a label would silently re-plan every album.
 */
export type PersonPriority = "high" | "medium" | "low";

export type AlbumBuildPreferences = {
  /**
   * How many photos the user asked for, as an UPPER BOUND.
   *
   * Not a target to hit. Quality floors, near-duplicate collapsing and the
   * priority gate can all legitimately return fewer, and padding the album back
   * up to a round number with photos the gates rejected is exactly the failure
   * the gates exist to prevent. Asking for 30 and getting 26 good ones is the
   * correct outcome; the UI should say "up to".
   */
  maxPhotos: number;

  /**
   * Who the album is for. Anyone absent is LOW priority.
   *
   * An EMPTY map means the question was never asked, and everything downstream
   * then behaves exactly as it did before priorities existed. That distinction
   * carries real weight: "no preference" must not collapse into "everyone is
   * low priority", which would gate out the entire library and hand back an
   * empty album.
   */
  personPriority: Readonly<Record<string, "high" | "medium">>;

  /**
   * The people the questionnaire actually offered, frozen at the moment it was
   * asked.
   *
   * Clustering keeps improving in the background, and a person id can be merged
   * away between the question and the build. Without this the album would be
   * built against a roster the user never saw -- silently dropping someone they
   * marked as the main focus. Holding the roster means a stale id can be
   * DETECTED and re-asked rather than quietly ignored.
   */
  offeredPersonIds: readonly string[];
};

/** Nothing was asked. Every gate then behaves as it did before priorities. */
export const NO_ALBUM_PREFERENCES: AlbumBuildPreferences = {
  maxPhotos: 0,
  personPriority: {},
  offeredPersonIds: [],
};

/**
 * Which of the user's chosen people no longer exist.
 *
 * Non-empty means clustering changed underneath the answers and the user should
 * be asked again rather than handed an album quietly missing someone. Returning
 * the ids rather than a boolean so the caller can name them.
 */
export function stalePriorityPeople(
  preferences: AlbumBuildPreferences,
  livePersonIds: Iterable<string>,
): string[] {
  const live = new Set(livePersonIds);
  return Object.keys(preferences.personPriority).filter((id) => !live.has(id));
}

/**
 * Is this answer usable, or was the question effectively never answered?
 *
 * A preference set with people but no HIGH priority is not a valid answer: the
 * requirement is that most of the album is the high-priority people, and with
 * none the comparison has no anchor. The UI is expected to require one; this is
 * the backstop for every other caller.
 */
export function hasUsablePriorities(
  preferences: AlbumBuildPreferences,
): boolean {
  return Object.values(preferences.personPriority).some(
    (priority) => priority === "high",
  );
}
