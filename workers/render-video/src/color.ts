import type {
  ColorEncoding,
  ColorOp,
  ColorPipeline,
  EncodeProfile,
  MediaRef,
  ToneMap,
} from "../../../contracts/codegen/generated/typescript/index.js";

import { RenderVideoError } from "./errors.js";

/**
 * The colour path, executed rather than guessed (contracts#49, contracts#58).
 *
 * Before this the worker refused every EDL that carried a colour op — `ColorOp.amount` was
 * "normalised to [-1,1]" and nothing else, so applying one meant inventing the physical
 * size of the adjustment — and refused every HDR source, because `tone_map_hdr_to_sdr` was
 * a boolean that named no operator. Both are now stated by the contract, per op and per
 * curve, as formulas. This module is the other half of that: it turns the stated formulas
 * into ffmpeg arguments and nothing else. There is no default anywhere in this file.
 *
 * WHY THE FORMULAS ARE WORTH TRUSTING. They were not copied out of a filter's
 * documentation; they were measured. `test/color.test.ts` feeds known linear values
 * through the real ffmpeg filters and checks the output against the schema's arithmetic to
 * float precision. That test is what found the first draft's claim that reinhard's
 * `operator_param: 1` was a straight scale — it is the degenerate end, where every pixel
 * above black becomes reference white.
 */

function fail(detail: string): never {
  throw new RenderVideoError("validation_failed", `The video renderer refused the colour pipeline: ${detail}`);
}

/**
 * `ColorEncoding` -> the three tags that define it, in zscale's vocabulary.
 *
 * The schema's ColorEncoding $comment states the same table in standards terms (BT.709-6,
 * IEC 61966-2-1, SMPTE ST 2084, ARIB STD-B67). This is that table with zimg's spellings,
 * kept in one place so a token never turns into a guess at a call site.
 */
interface EncodingTags {
  primaries: string;
  transfer: string;
  matrix: string;
}

const ENCODING_TAGS: Readonly<Record<ColorEncoding, EncodingTags>> = Object.freeze({
  srgb: { primaries: "bt709", transfer: "iec61966-2-1", matrix: "bt709" },
  bt709: { primaries: "bt709", transfer: "bt709", matrix: "bt709" },
  display_p3: { primaries: "smpte432", transfer: "iec61966-2-1", matrix: "bt709" },
  bt2020_sdr: { primaries: "bt2020", transfer: "bt709", matrix: "bt2020nc" },
  bt2100_pq: { primaries: "bt2020", transfer: "smpte2084", matrix: "bt2020nc" },
  bt2100_hlg: { primaries: "bt2020", transfer: "arib-std-b67", matrix: "bt2020nc" },
});

/** The two HDR members. A source outside this set never meets the tone map. */
export const HDR_ENCODINGS: ReadonlySet<string> = new Set<string>(["bt2100_pq", "bt2100_hlg"]);

/**
 * Working space -> its primaries and its luminance vector.
 *
 * The vector is what `ColorOp.saturation` and the tone map's desaturation both weight by,
 * and the schema names it per space precisely because BT.709's vector applied to BT.2020
 * primaries desaturates greens by several percent, which reads as the grade rather than as
 * a bug.
 */
interface WorkingSpace {
  primaries: string;
  /** zimg matrix name, used only to TAG the linear frames so `tonemap` picks this vector. */
  matrix: string;
  luma: readonly [number, number, number];
}

const WORKING_SPACES: Readonly<Record<string, WorkingSpace>> = Object.freeze({
  linear_bt709: { primaries: "bt709", matrix: "bt709", luma: [0.2126, 0.7152, 0.0722] },
  linear_bt2020: { primaries: "bt2020", matrix: "bt2020nc", luma: [0.2627, 0.678, 0.0593] },
});

type Matrix3 = readonly [
  readonly [number, number, number],
  readonly [number, number, number],
  readonly [number, number, number],
];

const IDENTITY: Matrix3 = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];

/**
 * One op as a 3x3 linear map of linear-light RGB, exactly as the ColorOp $comment states.
 *
 *   exposure    RGB' = RGB * 2^(2a)                        — `a` buys +/- 2 stops
 *   saturation  RGB' = Y + (1 + a) * (RGB - Y),  Y = k.RGB — `a` buys 0x..2x chroma
 *
 * Both are linear, which is what lets the whole list fuse into one matrix below.
 */
export function opMatrix(op: ColorOp, workingSpace: string): Matrix3 {
  const space = WORKING_SPACES[workingSpace];
  if (!space) fail(`working space ${workingSpace} has no definition in this worker.`);
  const a = op.amount;
  switch (op.op) {
    case "exposure": {
      const gain = 2 ** (2 * a);
      return [
        [gain, 0, 0],
        [0, gain, 0],
        [0, 0, gain],
      ];
    }
    case "saturation": {
      const k = space.luma;
      const row = (i: number): readonly [number, number, number] => [
        (i === 0 ? 1 + a : 0) - a * k[0],
        (i === 1 ? 1 + a : 0) - a * k[1],
        (i === 2 ? 1 + a : 0) - a * k[2],
      ];
      return [row(0), row(1), row(2)];
    }
    default:
      return fail(
        `colour op ${op.op} has no transfer function in this worker. The contract's ColorOp ` +
          "enum and this switch are supposed to be the same list; if they have drifted, the " +
          "renderer would be inventing the size of the adjustment (contracts#49).",
      );
  }
}

function multiply(left: Matrix3, right: Matrix3): Matrix3 {
  const at = (i: number, j: number): number => {
    const row = left[i] as readonly [number, number, number];
    const r0 = right[0] as readonly [number, number, number];
    const r1 = right[1] as readonly [number, number, number];
    const r2 = right[2] as readonly [number, number, number];
    return row[0] * r0[j]! + row[1] * r1[j]! + row[2] * r2[j]!;
  };
  return [
    [at(0, 0), at(0, 1), at(0, 2)],
    [at(1, 0), at(1, 1), at(1, 2)],
    [at(2, 0), at(2, 1), at(2, 2)],
  ];
}

/**
 * The whole op list as ONE matrix, multiplied in array order.
 *
 * The schema requires this rather than permitting it, and the reason is reproducibility
 * rather than speed: applying each op in turn rounds after every step, so the result would
 * depend on how many ops the planner happened to emit rather than on what they say. Fusing
 * first means a two-op grade and the single equivalent op land on the same bits.
 */
export function fuseColorOps(ops: readonly ColorOp[], workingSpace: string): Matrix3 {
  let fused = IDENTITY;
  for (const op of ops) fused = multiply(opMatrix(op, workingSpace), fused);
  return fused;
}

/** True when the fused matrix would change no pixel, so the whole chain can be skipped. */
export function isIdentityMatrix(matrix: Matrix3): boolean {
  for (let i = 0; i < 3; i += 1) {
    const row = matrix[i] as readonly [number, number, number];
    for (let j = 0; j < 3; j += 1) {
      if (row[j] !== (i === j ? 1 : 0)) return false;
    }
  }
  return true;
}

/**
 * A number for an ffmpeg option, with enough digits that the binary64 value round-trips.
 * `String(x)` in JavaScript already emits the shortest round-tripping decimal, so this is
 * really a guard against exponent notation, which ffmpeg's expression parser reads as a
 * multiplication by a variable named `e`.
 */
function arg(value: number): string {
  if (!Number.isFinite(value)) fail(`a colour coefficient is ${value}.`);
  const text = String(value);
  if (!text.includes("e") && !text.includes("E")) return text;
  // JavaScript switches to exponent notation below 1e-6 and at or above 1e21, and
  // ffmpeg's expression parser reads a bare `e` as a variable rather than as an
  // exponent, so the option would silently parse to something else.
  const fixed = value.toFixed(20);
  if (fixed.includes("e") || fixed.includes("E")) {
    fail(`colour coefficient ${text} has no decimal spelling ffmpeg would read correctly.`);
  }
  return fixed;
}

function colorChannelMixer(matrix: Matrix3): string {
  const [r, g, b] = matrix as unknown as [
    readonly [number, number, number],
    readonly [number, number, number],
    readonly [number, number, number],
  ];
  return (
    `colorchannelmixer=rr=${arg(r[0])}:rg=${arg(r[1])}:rb=${arg(r[2])}` +
    `:gr=${arg(g[0])}:gg=${arg(g[1])}:gb=${arg(g[2])}` +
    `:br=${arg(b[0])}:bg=${arg(b[1])}:bb=${arg(b[2])}`
  );
}

function toneMapFilter(toneMap: ToneMap, peak: number): string {
  const parts = [`tonemap=${toneMap.operator}`];
  if (toneMap.operator === "hable") {
    if (toneMap.operator_param != null) {
      fail("hable carries an operator_param, which the curve has nowhere to put.");
    }
  } else {
    const param = toneMap.operator_param;
    if (param == null || !(param > 0 && param < 1)) {
      fail(`${toneMap.operator} needs an operator_param in (0,1) and the plan states ${param}.`);
    }
    parts.push(`param=${arg(param)}`);
  }
  parts.push(`desat=${arg(toneMap.desaturation)}`, `peak=${arg(peak)}`);
  return parts.join(":");
}

export interface ColorChain {
  /** Filter text to append after geometry, WITHOUT a leading comma. Empty when identity. */
  filter: string;
  /** True when the source's code values reach the file untouched. */
  identity: boolean;
}

/**
 * The colour chain for one source use.
 *
 * ORDER, from ColorPipeline's $comment: geometry has already happened, in the source's own
 * encoding. Then linearise into the working space, apply the ops as one fused matrix,
 * tone map, convert to the output encoding — which is the only place values are clamped —
 * and quantise to the pixel format.
 *
 * THE IDENTITY CASE IS NORMATIVE. When the source is already in the output encoding, the
 * clip carries no ops and the source is not HDR, the whole chain is skipped and the code
 * values are passed through. A round trip through linear light is not lossless at 8 bits,
 * so a renderer that inserted one would change every frame of an ordinary SDR reel to no
 * purpose — invisibly in review and permanently in the file.
 */
export function colorChain(
  pipeline: ColorPipeline,
  ref: MediaRef,
  ops: readonly ColorOp[],
  profile: EncodeProfile,
): ColorChain {
  const encoding = ref.color_encoding;
  if (!encoding) {
    fail(
      `media_ref ${ref.media_ref_id} carries a picture and states no color_encoding. The ` +
        "plan asserts what a source's code values mean; this worker never infers it.",
    );
  }
  const source = ENCODING_TAGS[encoding];
  if (!source) fail(`colour encoding ${encoding} has no definition in this worker.`);
  const output = ENCODING_TAGS[pipeline.output_encoding];
  if (!output) fail(`output encoding ${pipeline.output_encoding} has no definition in this worker.`);
  const space = WORKING_SPACES[pipeline.working_space];
  if (!space) fail(`working space ${pipeline.working_space} has no definition in this worker.`);

  const isHdr = HDR_ENCODINGS.has(encoding);
  const toneMap = pipeline.tone_map;
  if (isHdr && !toneMap) {
    fail(
      `media_ref ${ref.media_ref_id} is ${encoding} and the plan carries no tone_map. An HDR ` +
        "source fitted into an SDR delivery with no operator renders washed out, which is the " +
        "failure nobody catches in review (contracts#58).",
    );
  }
  const fused = fuseColorOps(ops, pipeline.working_space);
  const graded = !isIdentityMatrix(fused);

  if (!graded && !isHdr && encoding === pipeline.output_encoding) {
    return { filter: "", identity: true };
  }

  // `reference_white_nits` lives on ToneMap, and only an HDR source has an absolute
  // transfer for it to scale. An SDR source linearises with its diffuse white at 1.0
  // whatever the number says, which is exactly the equivalence the schema states.
  const referenceWhite = toneMap ? toneMap.reference_white_nits : 100;
  const stages = [
    `zscale=tin=${source.transfer}:pin=${source.primaries}:min=${source.matrix}:rin=tv` +
      `:t=linear:p=${space.primaries}:npl=${arg(referenceWhite)}`,
    "format=gbrpf32le",
    // Pins the luminance vector `tonemap` desaturates by. Without it the frames carry no
    // matrix tag and the filter falls back to a vector whose coefficients sum to 3, which
    // desaturates pixels that are nowhere near the threshold. Measured, not assumed.
    `setparams=colorspace=${space.matrix}`,
  ];
  if (graded) stages.push(colorChannelMixer(fused));
  if (isHdr && toneMap) {
    const peak = ref.source_peak_nits;
    if (peak == null || !(peak > 0)) {
      fail(`media_ref ${ref.media_ref_id} is ${encoding} and states no source_peak_nits.`);
    }
    stages.push(toneMapFilter(toneMap, peak / referenceWhite));
  }
  stages.push(
    `zscale=t=${output.transfer}:p=${output.primaries}:m=${output.matrix}:r=tv`,
    `format=${profile.video.pixel_format}`,
  );
  return { filter: stages.join(","), identity: false };
}

/**
 * The colour tags written into the delivered container.
 *
 * An untagged SDR file is not neutral, it is ambiguous: a player picks a transfer from its
 * own habits, and the same bytes look different in two of them. `output_encoding` says
 * what the code values mean, so the file has to say so too.
 */
export function outputColorArgs(pipeline: ColorPipeline): string[] {
  const tags = ENCODING_TAGS[pipeline.output_encoding];
  if (!tags) fail(`output encoding ${pipeline.output_encoding} has no definition in this worker.`);
  return [
    "-colorspace",
    tags.matrix,
    "-color_primaries",
    tags.primaries,
    "-color_trc",
    tags.transfer,
    "-color_range",
    "tv",
  ];
}

/**
 * The source's own tags, when it carries any, so a plan that contradicts the file fails.
 *
 * The plan's declaration WINS — that is the whole point of moving the decision off the
 * renderer — but a declaration that contradicts the container is either the wrong file or
 * a plan made before the file was re-graded, and both are worth stopping for. An untagged
 * source contradicts nothing, which is the case action cameras and phone HEVC actually
 * produce and the case `input_transform: "auto"` handled worst.
 */
/**
 * ffprobe spellings that name the SAME curve as one of ours.
 *
 * SMPTE 170M and BT.709 share an OETF: 170M is the 525-line spelling of the identical
 * curve, and cameras tag one or the other by habit. BT.2020-10 and BT.2020-12 are that
 * curve again at 10 and 12 bits. Deliberately short: `bt470bg` is gamma 2.8 and is NOT
 * here, because aliasing it to BT.709 would silence a real disagreement about roughly a
 * stop in the shadows — which is the kind of thing this check exists to catch.
 */
const TRANSFER_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  smpte170m: "bt709",
  "bt2020-10": "bt709",
  "bt2020-12": "bt709",
});

export function assertColorDeclarationMatchesFile(
  ref: MediaRef,
  probedTransfer: string | null,
  probedRange: string | null,
): void {
  const encoding = ref.color_encoding;
  if (!encoding) return;

  /**
   * Every ColorEncoding token is LIMITED range, stated in the schema's ColorEncoding
   * $comment. A full-range source is not refused because it is exotic — phones shoot it —
   * but because reading full-range levels as limited crushes the blacks and clips the
   * whites, and doing it the other way round washes the picture out. Neither raises.
   */
  if (probedRange === "pc" || probedRange === "full") {
    fail(
      `media_ref ${ref.media_ref_id} resolves to a FULL-range file and every ColorEncoding ` +
        "token in this contract is limited range. Reading one as the other moves every code " +
        "value, and nothing about it raises (contracts#101).",
    );
  }

  if (!probedTransfer || probedTransfer === "unknown" || probedTransfer === "unspecified") return;
  const expected = ENCODING_TAGS[encoding]?.transfer;
  if (expected === undefined) return;
  const probed = TRANSFER_ALIASES[probedTransfer] ?? probedTransfer;
  const declared = TRANSFER_ALIASES[expected] ?? expected;
  if (probed !== declared) {
    fail(
      `media_ref ${ref.media_ref_id} is declared ${encoding}, whose transfer is ${expected}, ` +
        `and the resolved file is tagged ${probedTransfer}. The plan and the footage disagree ` +
        "about what the code values mean; one of them is out of date.",
    );
  }
}
