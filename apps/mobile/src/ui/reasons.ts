import type { PlannerReasonCode } from "../selection/album-planner";

// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { copy } from "./copy.ts";

type UiReasonCode =
  | PlannerReasonCode
  | "blinking"
  | "blur"
  | "crop"
  | "diagnostic_unavailable"
  | "face_away"
  | "lower_smile"
  | "similar_left_out"
  | "stronger_frames";

const reasonCodeByMessage = new Map<string, UiReasonCode>([
  ["Kept because you chose it.", "user_choice"],
  ["Keeps everyone in the story.", "only_shot_of_person"],
  ["Adds another moment to the story.", "coverage_moment"],
  ["Keeps the album spread across the whole event.", "coverage_time"],
  ["Shows a different place from the memory.", "different_place"],
  ["Adds a different pose.", "different_pose"],
  ["One of the clearest photos in its group.", "sharpest_of_take"],
  ["A strong photo that fits the story.", "strong_photo"],
  ["Everyone looks happy, and this is one of the clearest photos in its group.", "smiling_sharp"],
  ["Everyone looks happy in this moment.", "smiling"],
  ["Everyone's eyes are open and easy to see.", "eyes_open"],
  ["The expression feels natural and warm.", "natural_expression"],
  ["Selected from a distinct visual take to keep the album varied.", "distinct_take"],
  ["Natural expression and the sharpest eyes in this burst.", "natural_expression"],
  ["A face cut at the frame edge lowered this frame's quality.", "face_cut"],
  ["Rejected: a face is cut at the frame edge.", "face_cut"],
  ["No reliable thumbnail-detail proxy was available; stable metadata order was used.", "diagnostic_unavailable"],
  ["Screenshot excluded from automatic album selection.", "screenshot"],
  ["The album target already filled with stronger frames from distinct takes.", "stronger_frames"],
  ["excluded by user", "user_excluded"],
  ["failed a hard image gate", "hard_image_gate"],
  ["screenshot or document", "screenshot"],
  ["below the quality floor", "below_quality_floor"],
  ["a face is cut by the frame", "face_cut"],
  ["face exposure is clipped", "exposure_clipped"],
  ["a face is too dark", "face_too_dark"],
  ["a face is out of focus", "face_out_of_focus"],
  ["the subject is out of focus", "subject_out_of_focus"],
  ["per-person cap reached", "person_cap"],
  ["A similar moment, but one face is turned away.", "face_away"],
  ["More motion blur around the subject.", "blur"],
  ["Lovely expression, but the crop needs more headroom.", "crop"],
]);

/**
 * The selection engine mostly emits *measured* sentences — "Rejected: blurrier
 * than the chosen frame (52% vs 71% sharpness)." — that carry the numbers with
 * them and so can never appear in an exact-match table. Without this layer every
 * alternative in the swap sheet fell through to one generic sentence, which is
 * how the review screen came to look like it was showing placeholder text.
 * Order matters only in that the first matching pattern wins per string.
 */
const reasonCodePatterns: readonly (readonly [RegExp, UiReasonCode])[] = [
  [/^rejected: subject blinking/i, "blinking"],
  [/^rejected: blurrier than/i, "blur"],
  [/face (is )?cut (at|by) (the )?frame edge/i, "face_cut"],
  [/a face touches the frame edge/i, "face_cut"],
  [/lower smile signal/i, "lower_smile"],
  [/^best smile signal/i, "smiling"],
  [/all known significant faces have open eyes/i, "eyes_open"],
  [/sharpest of \d+ near-duplicates/i, "sharpest_of_take"],
  [/strongest thumbnail-detail proxy among/i, "sharpest_of_take"],
  [/-weighted quality among \d+ near-duplicate/i, "strong_photo"],
  [/^near-duplicate/i, "similar_left_out"],
  [/album target (was )?already filled/i, "stronger_frames"],
  [/^screenshot excluded/i, "screenshot"],
  [/no (valid take information|reliable thumbnail-detail proxy)/i, "diagnostic_unavailable"],
];

function codeFor(reason: string): UiReasonCode | undefined {
  const exact = reasonCodeByMessage.get(reason);
  if (exact) return exact;
  return reasonCodePatterns.find(([pattern]) => pattern.test(reason))?.[1];
}

const chosenCopyByCode: Record<UiReasonCode, string> = {
  below_quality_floor: copy.reasons.qualityConcern,
  blinking: copy.reasons.blinking,
  blur: copy.reasons.blur,
  coverage_moment: copy.reasons.story,
  coverage_time: copy.reasons.coverageTime,
  crop: copy.reasons.crop,
  diagnostic_unavailable: copy.reasons.diagnosticUnavailable,
  different_place: copy.reasons.differentPlace,
  different_pose: copy.reasons.differentPose,
  distinct_take: copy.reasons.distinctTake,
  eyes_open: copy.reasons.eyesOpen,
  exposure_clipped: copy.reasons.exposureConcern,
  face_away: copy.reasons.faceAway,
  face_cut: copy.reasons.faceCut,
  face_out_of_focus: copy.reasons.focusConcern,
  face_too_dark: copy.reasons.exposureConcern,
  hard_image_gate: copy.reasons.qualityConcern,
  low_priority_people: copy.reasons.lowPriorityPeople,
  lower_smile: copy.reasons.lowerSmile,
  natural_expression: copy.reasons.naturalExpression,
  only_shot_of_person: copy.reasons.onlyShotOfPerson,
  person_cap: copy.reasons.neutralLeftOut,
  screenshot: copy.reasons.screenshot,
  sharpest_of_take: copy.reasons.sharp,
  similar_left_out: copy.reasons.neutralLeftOut,
  smiling: copy.reasons.smiling,
  smiling_sharp: copy.reasons.smilingSharp,
  stronger_frames: copy.reasons.neutralLeftOut,
  strong_photo: copy.reasons.neutralChosen,
  subject_out_of_focus: copy.reasons.focusConcern,
  user_choice: copy.reasons.userChoice,
  user_excluded: copy.reasons.userExcluded,
};

const alternativeCopyByCode: Record<UiReasonCode, string> = {
  ...chosenCopyByCode,
  coverage_moment: copy.reasons.neutralLeftOut,
  coverage_time: copy.reasons.neutralLeftOut,
  different_place: copy.reasons.neutralLeftOut,
  different_pose: copy.reasons.neutralLeftOut,
  distinct_take: copy.reasons.neutralLeftOut,
  eyes_open: copy.reasons.neutralLeftOut,
  natural_expression: copy.reasons.neutralLeftOut,
  only_shot_of_person: copy.reasons.neutralLeftOut,
  sharpest_of_take: copy.reasons.neutralLeftOut,
  smiling: copy.reasons.neutralLeftOut,
  smiling_sharp: copy.reasons.neutralLeftOut,
  strong_photo: copy.reasons.neutralLeftOut,
  user_choice: copy.reasons.neutralLeftOut,
};

const chosenPriority: UiReasonCode[] = [
  "diagnostic_unavailable",
  "screenshot",
  "user_excluded",
  "blinking",
  "face_away",
  "face_cut",
  "blur",
  "lower_smile",
  "face_out_of_focus",
  "subject_out_of_focus",
  "crop",
  "exposure_clipped",
  "face_too_dark",
  "hard_image_gate",
  "below_quality_floor",
  "person_cap",
  "stronger_frames",
  "similar_left_out",
  "smiling_sharp",
  "eyes_open",
  "smiling",
  "natural_expression",
  "sharpest_of_take",
  "only_shot_of_person",
  "different_pose",
  "different_place",
  "distinct_take",
  "coverage_time",
  "coverage_moment",
  "user_choice",
  "strong_photo",
];

const alternativePriority: UiReasonCode[] = [
  "screenshot",
  "blinking",
  "face_away",
  "face_cut",
  "blur",
  "lower_smile",
  "face_out_of_focus",
  "subject_out_of_focus",
  "crop",
  "diagnostic_unavailable",
  "exposure_clipped",
  "face_too_dark",
  "below_quality_floor",
  "hard_image_gate",
  "user_excluded",
  // Above the generic "a stronger frame won" lines: when a photo is out because
  // of who is in it, that is the honest reason and the one the user can act on
  // by changing their answer, so it must not be masked by a quality excuse.
  "low_priority_people",
  "stronger_frames",
  "similar_left_out",
];

function firstCode(reasons: readonly string[], priority: readonly UiReasonCode[]): UiReasonCode | undefined {
  const present = new Set(reasons.flatMap((reason) => {
    const code = codeFor(reason);
    return code ? [code] : [];
  }));
  return priority.find((code) => present.has(code));
}

export function plainChosenReason(reasons: readonly string[]): string {
  if (reasons.length === 0) return copy.review.chosenFallback;
  const code = firstCode(reasons, chosenPriority);
  return code ? chosenCopyByCode[code] : copy.reasons.neutralChosen;
}

export function plainAlternativeReason(reasons: readonly string[]): string {
  if (reasons.length === 0) return copy.review.alternativeFallback;
  const code = firstCode(reasons, alternativePriority);
  return code ? alternativeCopyByCode[code] : copy.reasons.neutralLeftOut;
}
