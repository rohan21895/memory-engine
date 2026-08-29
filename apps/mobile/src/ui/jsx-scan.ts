// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { readdirSync } from "node:fs";
// @ts-expect-error The Expo app deliberately does not ship Node declarations.
import { join } from "node:path";

/**
 * Reading the app's own JSX, for the gates that check it.
 *
 * Test-only: nothing in the running app imports this, and it uses `node:fs`, so
 * it could not run on the phone if something tried. It lives outside a
 * `.test.ts` file only because two gates need the same parser and a second copy
 * of the brace tracking below would drift out of step with the first.
 *
 * Same shape as `face-sharpness-policy-harness.ts`: a plain module the tests
 * import, rather than logic hidden inside one of them.
 */

/** Every source file under `directory`, tests excluded. */
export function sourceFiles(
  directory: string,
  extensions: readonly string[] = [".tsx"],
): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      found.push(...sourceFiles(path, extensions));
    } else if (
      extensions.some((extension) => entry.name.endsWith(extension)) &&
      !entry.name.includes(".test.")
    ) {
      found.push(path);
    }
  }
  return found;
}

/**
 * The opening tag of every `<${element}` in `source`, with its 1-based line.
 *
 * Walks to the `>` that closes the tag rather than the first one in the file.
 * These tags carry arrow functions (`style={({ pressed }) => ...}`) and string
 * props holding punctuation, so a naive `indexOf(">")` stops inside one and the
 * caller then decides on a truncated string -- passing quietly, which is the
 * failure mode a gate exists to prevent. Braces, quotes and template literals
 * are all tracked for that reason.
 */
export function openingTags(
  source: string,
  element = "Pressable",
): { tag: string; line: number }[] {
  const opener = `<${element}`;
  const tags: { tag: string; line: number }[] = [];
  for (let at = source.indexOf(opener); at !== -1; at = source.indexOf(opener, at + 1)) {
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
        tags.push({ line: source.slice(0, at).split("\n").length, tag: source.slice(at, index + 1) });
        break;
      }
    }
  }
  return tags;
}

/**
 * Every `styles.name` entry in a file with an explicit height, and what it is.
 *
 * Deliberately only the top-level entries of a `StyleSheet.create` object --
 * two-space indent, one line -- which is how every stylesheet in this app is
 * written. A style whose height arrives from somewhere else is not measurable
 * from the source and is not claimed to be.
 */
export function styleHeights(source: string): Map<string, number> {
  const heights = new Map<string, number>();
  for (const entry of source.matchAll(/^ {2}(\w+): \{([^}]*)\}/gm)) {
    const height = entry[2].match(/(?:^|[,{ ])(?:min)?[Hh]eight: ([0-9.]+)/);
    if (height) heights.set(entry[1], Number(height[1]));
  }
  return heights;
}
