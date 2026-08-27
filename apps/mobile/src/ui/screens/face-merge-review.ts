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
 * Co-occurrence rates at which the two readings separate.
 *
 * Measured across the owner's library on the pairs the same-photo rule blocks.
 * Two populations, and the gap between them is empty:
 *
 *   0.6% - 3.6%   eleven pairs holding 1,065 faces. Two clusters of 180 and 310
 *                 faces sharing exactly ONE photo. Far more often one person
 *                 with a reflection, a framed photo on a wall, or a duplicate
 *                 detection in a single frame than two people who met once.
 *   7.7% - 57%    people who really are two people. A parent and child who are
 *                 photographed together constantly sit at 52%.
 *
 * Nothing lands between 3.6% and 7.7%, so the bands below are reading a real
 * separation rather than slicing a continuum at a convenient place.
 */
const LIKELY_DOUBLE_DETECTION = 0.05;
const LIKELY_TWO_PEOPLE = 0.15;

/**
 * The evidence the app is asking the user to overrule, in a sentence.
 *
 * These pairs cleared the merge bar on face evidence and are held apart ONLY by
 * having been photographed together, so the whole question is how much that one
 * fact is worth — and "1 shared photo" cannot answer it. One shared photo out of
 * four hundred means something completely different from one out of two, and
 * without the denominator the user is guessing at exactly the questions that
 * matter most.
 *
 * States the evidence and what it usually indicates. Deliberately stops short of
 * recommending an answer: a low rate is evidence, not proof — two people
 * photographed together exactly once look identical here — and the merge is
 * irreversible, so the judgement stays with the person who knows these faces.
 *
 * Returns undefined when co-occurrence is not what is holding the pair back,
 * because then it is not the evidence under discussion.
 */
export function coOccurrenceEvidence(
  suggestion: MergeSuggestion,
): string | undefined {
  const { blockedByCoOccurrence, sharedAssets, appearances } = suggestion;
  if (!blockedByCoOccurrence || sharedAssets <= 0 || appearances <= 0) {
    return undefined;
  }
  const photos = `${sharedAssets === 1 ? "1 photo" : `${sharedAssets} photos`}`;
  const of = `of ${appearances.toLocaleString()}`;
  const rate = sharedAssets / appearances;
  if (rate <= LIKELY_DOUBLE_DETECTION) {
    return (
      `They appear together in only ${photos} ${of}. ` +
      `That usually means one face was counted twice in that photo — a mirror, ` +
      `a picture on the wall, or the same head found twice.`
    );
  }
  if (rate >= LIKELY_TWO_PEOPLE) {
    return (
      `They appear together in ${photos} ${of}. ` +
      `People who are photographed together this often are usually two ` +
      `different people.`
    );
  }
  return `They appear together in ${photos} ${of}.`;
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
