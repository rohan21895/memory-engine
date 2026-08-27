// @ts-expect-error Node's TypeScript runner requires the source extension.
import { FREQUENT_MERGE_CO_OCCURRENCE_RATE, RARE_MERGE_CO_OCCURRENCE_RATE } from "../../faces/face-cluster.ts";
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
 * Appearances below which the RATE carries no information and no conclusion is
 * stated.
 *
 * The bands in `face-cluster.ts` were measured on pairs with real denominators.
 * Applied to a small one they invert: two people who each appear in a single
 * photo, and that photo is the same one, give 1/1 = 100%, sail past
 * `FREQUENT_MERGE_CO_OCCURRENCE_RATE` and are announced as "usually two
 * different people" — when what actually happened is
 * that ONE face was found twice, which is the opposite conclusion and the more
 * common cause by far.
 *
 * That was not hypothetical. The owner opened the review, was shown a picture of
 * his wife beside the identical picture of his wife, and was told underneath
 * that people photographed together this often are usually two different people.
 * Ten of his top sixty questions were that same shape.
 *
 * Four matches `MERGE_EVIDENCE_MIN_FACES`, the count this codebase already uses
 * to mean "enough to be evidence". Below it the sentence reports the fact and
 * stops; a denominator of one cannot separate two populations.
 */
const MIN_APPEARANCES_FOR_A_CONCLUSION = 4;

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
  const rate = sharedAssets / appearances;
  const percentage = Math.round(rate * 1_000) / 10;
  const shared = `${sharedAssets.toLocaleString()} ${
    sharedAssets === 1 ? "photo" : "photos"
  }`;
  const total = `${appearances.toLocaleString()} ${
    appearances === 1 ? "photo" : "photos"
  }`;
  const fact = `${shared} out of ${total} (${percentage}%) ${
    sharedAssets === 1 ? "shows" : "show"
  } both faces.`;
  // Every photo either of them has is the SAME photo. That is the
  // double-detection signature, not evidence of two people — and on a
  // denominator this small the rate would claim the opposite with total
  // confidence. Checked before the rate, because this is the case the owner
  // actually hit and 1/1 = 100% sails straight past the frequent bar.
  if (sharedAssets >= appearances) {
    return (
      `Each of these appears in ${shared}, and it is the same photo. ` +
      `That usually means one face was counted twice rather than two people ` +
      `who were photographed together.`
    );
  }
  if (appearances < MIN_APPEARANCES_FOR_A_CONCLUSION) return fact;
  if (rate <= RARE_MERGE_CO_OCCURRENCE_RATE) {
    return (
      `${fact} In this library, seeing both this rarely can happen when one ` +
      `face was counted twice — in a mirror, a framed photo, or the same head ` +
      `found twice.`
    );
  }
  if (rate >= FREQUENT_MERGE_CO_OCCURRENCE_RATE) {
    return (
      `${fact} Seeing both this often can mean they are two different people ` +
      `who are often photographed together.`
    );
  }
  return fact;
}

/**
 * The single photo both tiles come from, when that photo is the WHOLE of what
 * either one holds — and the reason the question has to change shape.
 *
 * Measured on the owner's live index (17,769 faces): 48 pairs are this exact
 * shape, and every one scores between 0.456 and 0.705 — clustered just under
 * `SAME_PHOTO_DUPLICATE_SIMILARITY`, the bar above which a same-photo repeat is
 * deleted outright. They survive because two boxes landing slightly differently
 * on ONE head produce an alignment difference the identity model reads as a
 * stranger, so the same face scores like two people and the deletion rule
 * correctly declines to guess.
 *
 * Which means similarity cannot settle these, and neither can the owner: if it
 * really is one face found twice, the two crops he is being asked to compare
 * are the same pixels. There is no difference to spot. He said so —
 * "how will i know this bro?" — and he was right.
 *
 * The photograph settles it instantly. One person in the frame means one face
 * was counted twice; two people means two people. So for this shape the screen
 * shows the SOURCE PHOTO and asks about the photo, which is a question about
 * something he can see, rather than about two thumbnails that cannot differ.
 *
 * Deliberately narrow. Both sides must hold exactly one face and it must be the
 * same photo — then a wrong answer moves one photograph and nothing else. A
 * tile with a real history behind it goes back to the ordinary comparison,
 * where the crops ARE different pixels and the face count is the warning that
 * the answer is worth care.
 */
export function soleSharedPhoto(
  pair: FaceMergeReviewPair,
): string | undefined {
  const { first, second, suggestion } = pair;
  if (!suggestion.blockedByCoOccurrence) return undefined;
  if (first.faceCount !== 1 || second.faceCount !== 1) return undefined;
  if (first.assetIds.length !== 1 || second.assetIds.length !== 1) {
    return undefined;
  }
  return first.assetIds[0] === second.assetIds[0]
    ? first.assetIds[0]
    : undefined;
}

export type FaceMergeReviewProgress = {
  answered: number;
  photosRepaired: number;
};

/** Advances the two numbers the owner sees after a recorded answer. */
export function advanceFaceMergeReviewProgress(
  progress: FaceMergeReviewProgress,
  suggestion: MergeSuggestion,
  samePerson: boolean,
): FaceMergeReviewProgress {
  return {
    answered: progress.answered + 1,
    photosRepaired:
      progress.photosRepaired + (samePerson ? suggestion.photosFixed : 0),
  };
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
