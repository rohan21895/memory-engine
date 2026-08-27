/**
 * Regenerates `fixtures/album-plans/expected.json` — the pinned albums that
 * `src/selection/album-fixtures.test.ts` asserts against.
 *
 *   node --experimental-strip-types scripts/pin-album-fixtures.ts
 *
 * Run it ONLY when a selection change is intended, and read the diff: every
 * line that moves is a photograph that entered or left somebody's album.
 */

// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { writeFileSync } from "node:fs";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { albumFixtures, fixtureDigest } from "../src/selection/album-fixtures.ts";
// @ts-expect-error Node requires the extension; Metro resolves this path too.
import { planAlbum } from "../src/selection/album-planner.ts";
import type { PlannerPolicy } from "../src/selection/album-planner";

const SELECTORS: PlannerPolicy["selector"][] = ["coverage-keys", "submodular"];

const albums = albumFixtures().map((fixture) => ({
  name: fixture.name,
  target: fixture.target,
  corpusDigest: fixtureDigest(fixture),
  selectors: Object.fromEntries(
    SELECTORS.map((selector) => {
      const plan = planAlbum(fixture.candidates, fixture.target, { policy: { selector } });
      return [
        selector,
        {
          selectedIds: plan.selectedIds,
          rescuedIds: plan.rescuedIds,
          rejectedCount: plan.rejected.length,
          missingPersonIds: plan.missingPersonIds,
        },
      ];
    }),
  ),
}));

writeFileSync(
  new URL("../fixtures/album-plans/expected.json", import.meta.url),
  `${JSON.stringify(
    {
      note:
        "Regenerate with scripts/pin-album-fixtures.ts. Never hand-edit: the " +
        "corpusDigest is what stops a generator change from silently re-pinning both sides.",
      generatedBy: "src/selection/album-fixtures.ts",
      albums,
    },
    null,
    2,
  )}\n`,
);

console.log(
  `pinned ${albums.map((album) => `${album.name}:${album.corpusDigest}`).join(" ")}`,
);
