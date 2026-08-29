// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readdirSync, readFileSync } from "node:fs";
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { join } from "node:path";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`pressable accessibility self-check failed: ${message}`);
}

/**
 * Every tappable thing has to say what it is.
 *
 * The owner's ask was "make this app super easy to use for my granny", and the
 * part of that a test can hold is the part TalkBack reads out. A `Pressable`
 * with neither a role nor a label is announced as its raw contents -- for an
 * icon button that is the glyph ("<"), and for one wrapping a photo it is
 * nothing at all, so a whole screen becomes an unnamed tappable region with no
 * hint that tapping does anything.
 *
 * Two were found this way and neither was visible in a screenshot: the
 * Phone/Email toggle on the sign-in screen, which announced both halves
 * identically with no way to tell which was active, and the full-screen photo
 * viewer, whose only reachable way out was a small glyph in the corner.
 *
 * A role alone is enough. Most of these wrap a `<Text>` that TalkBack will read
 * for the name, so demanding an explicit label everywhere would mean writing
 * the visible text twice and letting the two drift apart.
 */

const ROOT = new URL("..", import.meta.url).pathname;

/** Every .tsx under src, tests excluded -- a test file is not a screen. */
function screenFiles(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) found.push(...screenFiles(path));
    else if (entry.name.endsWith(".tsx") && !entry.name.includes(".test.")) {
      found.push(path);
    }
  }
  return found;
}

/**
 * The opening tag of each `<Pressable`, as written.
 *
 * Walks to the `>` that closes the tag rather than the first one in the file,
 * because these tags carry arrow functions (`style={({ pressed }) => ...}`) and
 * a naive `indexOf(">")` stops inside one. Braces, quotes and template literals
 * are tracked so the scan cannot be fooled by a `>` inside any of them --
 * without that this test reports whatever it happens to have truncated, which
 * is the failure mode where a green run means nothing.
 */
function openingTags(source: string): string[] {
  const tags: string[] = [];
  for (let at = source.indexOf("<Pressable"); at !== -1; at = source.indexOf("<Pressable", at + 1)) {
    let depth = 0;
    let quote: string | null = null;
    for (let index = at; index < source.length; index += 1) {
      const character = source[index];
      if (quote) {
        if (character === "\\") index += 1;
        else if (character === quote) quote = null;
        continue;
      }
      if (character === '"' || character === "'" || character === "`") {
        quote = character;
      } else if (character === "{") depth += 1;
      else if (character === "}") depth -= 1;
      else if (character === ">" && depth === 0) {
        tags.push(source.slice(at, index + 1));
        break;
      }
    }
  }
  return tags;
}

const unnamed: string[] = [];
let total = 0;
for (const file of screenFiles(ROOT)) {
  const source = readFileSync(file, "utf8");
  for (const tag of openingTags(source)) {
    total += 1;
    if (tag.includes("accessibilityLabel") || tag.includes("accessibilityRole")) {
      continue;
    }
    const line = source.slice(0, source.indexOf(tag)).split("\n").length;
    unnamed.push(`${file.slice(ROOT.length)}:${line}`);
  }
}

// VACUITY, in two directions. A scanner that finds nothing passes trivially,
// and one whose tag extraction is broken passes just as quietly -- so the
// count has to be plausible AND a tag known to be well-formed has to parse.
assert(total > 40, `the scan must actually find the app's buttons (found ${total})`);
{
  const [sample] = openingTags(
    '<Pressable accessibilityRole="button" onPress={() => go(a > b)} style={s}><Text>x</Text></Pressable>',
  );
  assert(
    sample?.endsWith("style={s}>") === true,
    `tag extraction must stop at the tag's own close, not at a > inside a prop (got ${sample})`,
  );
}

assert(
  unnamed.length === 0,
  `every Pressable needs an accessibilityRole or accessibilityLabel; ` +
    `${unnamed.length} of ${total} have neither:\n  ${unnamed.join("\n  ")}`,
);

console.log(`pressable accessibility self-check passed (${total} pressables)`);
