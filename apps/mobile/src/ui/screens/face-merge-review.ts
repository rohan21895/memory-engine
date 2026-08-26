import type { MergeSuggestion } from "../../faces/face-cluster";
import type { FaceIndexPerson } from "../../faces/face-index";

export type FaceMergeReviewPair = {
  first: FaceIndexPerson;
  second: FaceIndexPerson;
  suggestion: MergeSuggestion;
};

/** Resolves persisted person ids without making the screen read observations. */
export function faceMergeReviewPair(
  suggestion: MergeSuggestion,
  personForId: (personId: string) => FaceIndexPerson | undefined,
): FaceMergeReviewPair | undefined {
  const first = personForId(suggestion.a);
  const second = personForId(suggestion.b);
  return first && second ? { first, second, suggestion } : undefined;
}

/**
 * Removes the answered pair. A merge also invalidates every other suggestion
 * involving either old group because its centroid and, sometimes, id changed.
 */
export function remainingFaceMergeSuggestions(
  suggestions: readonly MergeSuggestion[],
  answered: MergeSuggestion,
  samePerson: boolean,
): MergeSuggestion[] {
  return suggestions.filter((suggestion) => {
    const exactPair =
      (suggestion.a === answered.a && suggestion.b === answered.b) ||
      (suggestion.a === answered.b && suggestion.b === answered.a);
    if (exactPair) return false;
    if (!samePerson) return true;
    return ![
      suggestion.a,
      suggestion.b,
    ].some((personId) => personId === answered.a || personId === answered.b);
  });
}
