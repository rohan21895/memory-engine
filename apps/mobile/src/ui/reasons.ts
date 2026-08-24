import { copy } from "./copy";

function contains(reason: string, words: string[]) {
  return words.some((word) => reason.includes(word));
}

export function plainChosenReason(reasons: string[]): string {
  const reason = reasons.join(" ").toLowerCase();
  if (!reason) return copy.review.chosenFallback;
  const smiling = contains(reason, ["smil", "happy", "laugh"]);
  const sharp = contains(reason, ["sharp", "clear", "detail", "focus"]);
  if (smiling && sharp) return copy.reasons.smilingSharp;
  if (smiling) return copy.reasons.smiling;
  if (contains(reason, ["eyes open", "open eyes"])) return copy.reasons.eyesOpen;
  if (contains(reason, ["burst", "similar", "duplicate"])) return copy.reasons.similarBest;
  if (contains(reason, ["expression"])) return copy.reasons.naturalExpression;
  if (contains(reason, ["face", "everyone", "whole table"])) return copy.reasons.facesClear;
  if (contains(reason, ["light", "exposure", "detail", "color"])) return copy.reasons.light;
  if (contains(reason, ["composition", "symmetry", "balance", "horizon", "vertical", "wide", "layers"])) {
    return copy.reasons.composition;
  }
  if (contains(reason, ["story", "opening", "final", "chapter", "moment"])) return copy.reasons.story;
  if (sharp) return copy.reasons.sharp;
  return copy.reasons.neutralChosen;
}

export function plainAlternativeReason(reasons: string[]): string {
  const reason = reasons.join(" ").toLowerCase();
  if (!reason) return copy.review.alternativeFallback;
  if (contains(reason, ["screenshot", "screen capture"])) return copy.reasons.screenshot;
  if (contains(reason, ["blink", "eyes closed"])) return copy.reasons.blinking;
  if (contains(reason, ["turned away", "face away", "obscured"])) return copy.reasons.faceAway;
  if (contains(reason, ["blur", "soft", "focus", "sharp"])) return copy.reasons.blur;
  if (contains(reason, ["crop", "headroom", "edge", "safe area", "fit", "room"])) return copy.reasons.crop;
  if (contains(reason, ["similar", "duplicate", "burst"])) return copy.reasons.neutralLeftOut;
  if (contains(reason, ["story", "moment", "chapter"])) return copy.reasons.story;
  return copy.reasons.neutralLeftOut;
}
