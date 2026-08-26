import {
  __observationsFileForTest as file,
  // @ts-expect-error Node's TypeScript runner requires the source extension.
} from "./face-index.ts";
import type { FaceObservation } from "./types.ts";

function assert(value: unknown, message: string): asserts value {
  if (!value) throw new Error(`observations-file self-check failed: ${message}`);
}

/**
 * The embeddings live in their own file so that opening the app does not have
 * to parse 13.8MB before it can draw anything. Measured on the owner's device
 * before the split: `readMs=84 parseMs=5993` -- six seconds of frozen JS thread
 * on every launch, which is what he reported as "i click on something, nothing
 * happens, app hangs".
 *
 * The cost of that split is a new failure mode with no cheap symptom:
 * `index.observations` is EMPTY until something asks for it, so a write issued
 * while it is empty would replace the whole library with nothing and the only
 * visible sign would be an empty People grid after a five-hour re-scan. That is
 * what the first case here exists for.
 */

type Written = { uri: string; contents: string };

function fakeFileSystem() {
  const writes: Written[] = [];
  const deleted: string[] = [];
  const moves: Array<{ from: string; to: string }> = [];
  return {
    writes,
    deleted,
    moves,
    fs: {
      documentDirectory: "file:///documents/",
      writeAsStringAsync: async (uri: string, contents: string) => {
        writes.push({ uri, contents });
      },
      deleteAsync: async (uri: string) => {
        deleted.push(uri);
      },
      moveAsync: async ({ from, to }: { from: string; to: string }) => {
        moves.push({ from, to });
      },
    } as never,
  };
}

const observation = (assetId: string, angleDegrees: number): FaceObservation => {
  const radians = (angleDegrees * Math.PI) / 180;
  return {
    assetId,
    // Long enough to exercise the quantizer's packing rather than a toy vector.
    embedding: Array.from({ length: 128 }, (_, i) =>
      Math.cos(radians + i * 0.01),
    ),
    embeddingKind: "identity",
    seedable: true,
    capturedAt: 1_700_000_000_000 + angleDegrees,
  };
};

const library = [observation("a", 0), observation("b", 30), observation("c", 61)];

// THE case. Not loaded means the file on disk is the only copy that exists.
{
  const { writes, fs } = fakeFileSystem();
  file.setObservations([]);
  file.setLoaded(false);
  file.setDirty(true);
  await file.persist(fs);
  assert(
    writes.length === 0,
    `writing while the embeddings are not in memory destroys the library ` +
      `(${writes.length} writes issued, contents ${JSON.stringify(writes[0]?.contents)})`,
  );
}

// Loaded and dirty is the only combination that may write.
{
  const { writes, moves, fs } = fakeFileSystem();
  file.setObservations(library.slice());
  file.setLoaded(true);
  file.setDirty(true);
  await file.persist(fs);
  assert(writes.length === 1, `one write expected, got ${writes.length}`);
  assert(
    writes[0].uri.endsWith(".tmp"),
    "the write must land on a temp file, so a kill mid-write cannot truncate " +
      "the real one",
  );
  assert(
    moves.length === 1 && moves[0].from === writes[0].uri,
    "and the temp file is what gets moved into place",
  );
  assert(
    writes[0].contents.split("\n").length === library.length,
    `one line per face, got ${writes[0].contents.split("\n").length}`,
  );
}

// Clean means the file already matches; rewriting 13.8MB for nothing is the
// cost this flag exists to avoid.
{
  const { writes, fs } = fakeFileSystem();
  file.setObservations(library.slice());
  file.setLoaded(true);
  file.setDirty(false);
  await file.persist(fs);
  assert(writes.length === 0, "a clean list must not be rewritten");
}

// A successful write clears the flag, or every later persist would rewrite the
// whole file again.
{
  const first = fakeFileSystem();
  file.setObservations(library.slice());
  file.setLoaded(true);
  file.setDirty(true);
  await file.persist(first.fs);
  const second = fakeFileSystem();
  await file.persist(second.fs);
  assert(
    first.writes.length === 1 && second.writes.length === 0,
    "a completed write must mark the list clean",
  );
}

// Round trip. Quantization is lossy by design, so this asserts the embedding
// survives well enough to cluster with -- not that it is bit-identical.
{
  const lines = file.lines(library).split("\n");
  assert(lines.length === library.length, "one line per face");
  const restored = lines.map((line: string) => file.parseLine(line));
  for (let i = 0; i < library.length; i += 1) {
    const back = restored[i];
    assert(back !== null, `line ${i} must parse back`);
    assert(back.assetId === library[i].assetId, `line ${i} keeps its photo`);
    assert(back.seedable === library[i].seedable, `line ${i} keeps its tier`);
    assert(
      back.capturedAt === library[i].capturedAt,
      `line ${i} keeps its capture time -- temporal merging reads it`,
    );
    assert(
      back.embedding.length === library[i].embedding.length,
      `line ${i} keeps every dimension`,
    );
    const dot = back.embedding.reduce(
      (sum: number, value: number, index: number) =>
        sum + value * library[i].embedding[index],
      0,
    );
    const magnitude =
      Math.hypot(...back.embedding) * Math.hypot(...library[i].embedding);
    assert(
      dot / magnitude > 0.999,
      `line ${i} must come back as the same face (cosine ${(dot / magnitude).toFixed(5)})`,
    );
  }
  // Vacuity guard: the three faces are genuinely different, so a parser that
  // returned any constant vector would fail the check above rather than sail
  // through it.
  const first = restored[0];
  const last = restored[library.length - 1];
  assert(first !== null && last !== null, "both ends of the sample parsed");
  const cross = first.embedding.reduce(
    (sum: number, value: number, index: number) =>
      sum + value * last.embedding[index],
    0,
  );
  const crossMagnitude =
    Math.hypot(...first.embedding) * Math.hypot(...last.embedding);
  assert(
    cross / crossMagnitude < 0.9,
    `the test faces must be distinguishable or the round trip proves nothing ` +
      `(cosine ${(cross / crossMagnitude).toFixed(3)})`,
  );
}

// Corruption is survivable: one bad line, not one bad library.
//
// Both KINDS of bad line, deliberately. An earlier version only fed in
// unparseable text, which never reaches the shape check at all -- so removing
// the shape check entirely still passed. Well-formed JSON of the wrong shape is
// the case that exercises it, and it is also the realistic one: a half-flushed
// write leaves a truncated object, not gibberish.
{
  const badLines = [
    "{not json", // truncated mid-write
    "{}", // valid JSON, no face in it
    '{"assetId":42,"embedding":"AAAA","embeddingKind":"identity"}', // wrong type
    '{"assetId":"x","embedding":"","embeddingKind":"identity"}', // empty vector
    '{"assetId":"x","embedding":"AAAA","embeddingKind":"guess"}', // unknown kind
    "null",
    "[]",
  ];
  for (const bad of badLines) {
    assert(
      file.parseLine(bad) === null,
      `a line that is not a face must yield nothing: ${bad}`,
    );
  }
  const lines = file.lines(library).split("\n");
  lines[1] = badLines[2];
  const restored = lines
    .map((line: string) => file.parseLine(line))
    .filter(Boolean);
  assert(
    restored.length === library.length - 1,
    `a corrupt line costs one face, got ${restored.length} of ${library.length - 1}`,
  );
}

// An empty line is padding, not a face.
assert(file.parseLine("") === null, "an empty line yields nothing");

console.log("observations-file self-check passed");
