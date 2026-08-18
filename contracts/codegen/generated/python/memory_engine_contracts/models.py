"""
GENERATED FILE -- DO NOT EDIT.

Produced by contracts/codegen/generate.py from contracts/schemas/*.schema.json.
Edit the schemas and re-run `npm run codegen`. CI fails if these files drift
from the schemas (see scripts/ci/check-codegen-freshness.mjs).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base for every generated contract model.

    `extra=forbid` mirrors `additionalProperties: false` in the schemas:
    an undeclared field is an error on both sides of the agent boundary,
    never a silently ignored one.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=False,
        # The contracts legitimately use `model_id` / `model_runs` to
        # describe ML models; pydantic reserves the `model_` prefix by
        # default and would warn on every import.
        protected_namespaces=(),
    )


class EnhancementOpKind(str, Enum):
    DENOISE = "denoise"
    UPSCALE = "upscale"
    FACE_RESTORE = "face_restore"
    SHARPEN = "sharpen"
    EXPOSURE = "exposure"
    WHITE_BALANCE = "white_balance"
    COLOR_TRANSFER = "color_transfer"
    SPREAD_HARMONIZE = "spread_harmonize"
    OUTPAINT_TO_FIT = "outpaint_to_fit"
    STRAIGHTEN = "straighten"
    PERSPECTIVE_CORRECT = "perspective_correct"
    DUST_REMOVAL = "dust_removal"


class EnhancementOp(ContractModel):
    """
    One planned image improvement. `license_cleared` is required and defaults to
    nothing: half the popular restoration models are non-commercial (CodeFormer
    S-Lab, FLUX.1-dev), and the contract is where that gets caught rather than
    discovered at launch.
    """

    op_id: Slug

    kind: EnhancementOpKind

    # Execution order within the placement. Explicit integers rather than array
    # position so a re-plan can insert an op without renumbering the world.
    order: int = Field(
        description="Execution order within the placement. Explicit integers rather than array position so a re-plan can insert an op without renumbering the world.",
    )

    # Whether the model behind this op passed the licence audit for commercial use.
    # False must block export -- an unlicensed enhancement is a legal defect that
    # ships inside a physical book.
    license_cleared: bool = Field(
        description="Whether the model behind this op passed the licence audit for commercial use. False must block export -- an unlicensed enhancement is a legal defect that ships inside a physical book.",
    )

    model: ModelRef | None = Field(default=None)

    parameters: dict[str, Any] = Field(default_factory=dict)

    strength: Unit | None = Field(default=None)

    # Why the op was planned: 'source is 1600px on a 300mm edge'. Feeds the user-
    # facing explanation and the review queue.
    reason: str | None = Field(
        default=None,
        description="Why the op was planned: 'source is 1600px on a 300mm edge'. Feeds the user-facing explanation and the review queue.",
    )


class EventContextDateRange(ContractModel):
    start: Timestamp

    end: Timestamp


class EventContext(ContractModel):
    event_cluster_id: Uuid | None = Field(default=None)

    # Human-readable event name, typically produced by a Tier 3 pass over contact
    # sheets: 'beach day', 'night market'.
    label: str | None = Field(
        default=None,
        description="Human-readable event name, typically produced by a Tier 3 pass over contact sheets: 'beach day', 'night market'.",
    )

    date_range: EventContextDateRange | None = Field(default=None)

    # People the album is about. Only ids that passed the automated-output face gate
    # appear here.
    person_ids: list[Uuid] = Field(
        default_factory=list,
        description="People the album is about. Only ids that passed the automated-output face gate appear here.",
    )

    place_label: str | None = Field(default=None)


class FaceSafety(ContractModel):
    """
    Where the faces ended up after cropping and placement. Computed by the album
    engine and checked by the render worker -- a face in the trim zone or the gutter
    is a hard export block.
    """

    face_count: int

    all_faces_in_safe_zone: bool

    faces_in_gutter: int = Field(default=0)

    faces_in_trim_zone: int = Field(default=0)

    # Distance from the nearest face to the nearest unsafe boundary. Negative means a
    # face has already crossed it.
    min_face_margin_mm: float | None = Field(
        default=None,
        description="Distance from the nearest face to the nearest unsafe boundary. Negative means a face has already crossed it.",
    )

    # Faces the crop cut through. Sometimes deliberate on a background figure, never
    # acceptable on a subject -- so it is recorded rather than merely prevented.
    cropped_face_ids: list[Blake3Hash] = Field(
        default_factory=list,
        description="Faces the crop cut through. Sometimes deliberate on a background figure, never acceptable on a subject -- so it is recorded rather than merely prevented.",
    )


class LayoutInfoSolver(str, Enum):
    CONSTRAINT_SOLVER = "constraint_solver"
    TEMPLATE = "template"
    MANUAL = "manual"


class LayoutInfoGrid(ContractModel):
    columns: int

    rows: int

    gutter_mm: float


class LayoutInfo(ContractModel):
    """
    How the page arrangement was arrived at. Layout is constraint solving, not
    template filling (build plan 4.6), so the record is of a solver run rather than
    of a chosen template id.
    """

    solver: LayoutInfoSolver = Field(default="constraint_solver")

    template_id: Slug | None = Field(default=None)

    grid: LayoutInfoGrid | None = Field(default=None)

    constraints_satisfied: list[str] = Field(default_factory=list)

    # Soft constraints the solver had to give up on, and which therefore deserve a
    # human glance. Recording them is the difference between a solver that reports its
    # compromises and one that hides them.
    constraints_relaxed: list[str] = Field(
        default_factory=list,
        description="Soft constraints the solver had to give up on, and which therefore deserve a human glance. Recording them is the difference between a solver that reports its compromises and one that hides them.",
    )

    solver_cost: float | None = Field(default=None)


class PageSide(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    SINGLE = "single"
    FRONT_COVER = "front_cover"
    BACK_COVER = "back_cover"
    INSIDE_FLAP = "inside_flap"


class PageBackgroundKind(str, Enum):
    SOLID = "solid"
    NONE = "none"


class PageBackground(ContractModel):
    kind: PageBackgroundKind

    color_hex: str = Field(default="#ffffff")


class Page(ContractModel):
    page_index: int

    side: PageSide

    # Pages sharing a spread_id are viewed together and are colour-harmonised
    # together. Null for a cover or a single page.
    spread_id: Slug | None = Field(
        default=None,
        description="Pages sharing a spread_id are viewed together and are colour-harmonised together. Null for a cover or a single page.",
    )

    # Narrative section this page belongs to, used by the diversity constraints
    # (people/scenery/detail balance per section).
    section_id: Slug | None = Field(
        default=None,
        description="Narrative section this page belongs to, used by the diversity constraints (people/scenery/detail balance per section).",
    )

    background: PageBackground | None = Field(default=None)

    placements: list[Placement] = Field(default_factory=list)

    text_blocks: list[TextBlock] = Field(default_factory=list)

    layout: LayoutInfo | None = Field(default=None)


class PlacementBleedsItem(str, Enum):
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"


class PlacementBorder(ContractModel):
    width_mm: float

    color_hex: str


class Placement(ContractModel):
    """
    One photo on one page. Carries the mm frame it occupies, the normalised crop
    taken from the source, and the effective DPI that results -- the three numbers
    the print validator reasons about.
    """

    placement_id: Slug

    media_id: Blake3Hash

    # Where it sits on the page, in mm from the bleed-box origin.
    frame: RectMm = Field(description="Where it sits on the page, in mm from the bleed-box origin.")

    # The region of the SOURCE image used, in normalised oriented-image coordinates.
    # Its aspect ratio must match the frame's, or the renderer would have to decide
    # how to reconcile them -- and the renderer decides nothing.
    crop: NormalizedBox = Field(
        description="The region of the SOURCE image used, in normalised oriented-image coordinates. Its aspect ratio must match the frame's, or the renderer would have to decide how to reconcile them -- and the renderer decides nothing.",
    )

    # Computed as (cropped source pixels along an edge) / (printed length of that edge
    # in inches). Compared against vendor_profile.dpi_floor by the hard validator. The
    # whole reason geometry is in mm.
    effective_dpi: float = Field(
        description="Computed as (cropped source pixels along an edge) / (printed length of that edge in inches). Compared against vendor_profile.dpi_floor by the hard validator. The whole reason geometry is in mm.",
    )

    z_index: int = Field(default=0)

    # Which page edges this placement runs off. A full-bleed photo must extend past
    # the trim by the profile's bleed_mm on every edge listed here.
    bleeds: list[PlacementBleedsItem] = Field(
        default_factory=list,
        description="Which page edges this placement runs off. A full-bleed photo must extend past the trim by the profile's bleed_mm on every edge listed here.",
    )

    # The anchor image of its spread. Heroes get the DPI headroom and the composition
    # attention; supporting images fill around them.
    is_hero: bool = Field(
        default=False,
        description="The anchor image of its spread. Heroes get the DPI headroom and the composition attention; supporting images fill around them.",
    )

    face_safety: FaceSafety | None = Field(default=None)

    # Ordered ops applied to this image before composition. Order matters: denoise
    # before upscale, upscale before face restore.
    enhancement_ops: list[EnhancementOp] = Field(
        default_factory=list,
        description="Ordered ops applied to this image before composition. Order matters: denoise before upscale, upscale before face restore.",
    )

    caption: str | None = Field(default=None)

    border: PlacementBorder | None = Field(default=None)


class PrintValidationReportStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"


class PrintValidationReport(ContractModel):
    """
    THE HARD GATE. workers/render-print refuses to export a PDF unless status is
    'pass'. There is no override flag by design (AGENTS.md): a print defect cannot
    be patched after the book is in the post, so the only safe place to fail is
    before the PDF exists.
    """

    status: PrintValidationReportStatus

    checks: list[ValidationCheck]

    validated_at: Timestamp | None = Field(default=None)

    validator_version: str | None = Field(default=None)

    error_count: int = Field(default=0)

    warning_count: int = Field(default=0)


class RectMm(ContractModel):
    """
    A rectangle on the page in millimetres. Origin is the top-left of the BLEED box,
    not the trim box, so a full-bleed placement has negative-free coordinates and
    the renderer never has to guess which origin a number is relative to.
    """

    x_mm: float

    y_mm: float

    width_mm: float

    height_mm: float

    rotation_deg: float = Field(default=0)


class SelectionReportDiversityConstraintsItemConstraint(str, Enum):
    NO_NEAR_DUPLICATES_ON_SPREAD = "no_near_duplicates_on_spread"
    PEOPLE_SCENERY_DETAIL_BALANCE = "people_scenery_detail_balance"
    MAX_PER_PERSON_PER_SECTION = "max_per_person_per_section"
    CHRONOLOGICAL_WITHIN_SECTION = "chronological_within_section"
    NO_CONSECUTIVE_SAME_SCENE = "no_consecutive_same_scene"
    MIN_HERO_QUALITY = "min_hero_quality"


class SelectionReportDiversityConstraintsItem(ContractModel):
    constraint: SelectionReportDiversityConstraintsItemConstraint

    satisfied: bool

    detail: str = Field(default="")


class SelectionReportRejectedItemReason(str, Enum):
    NEAR_DUPLICATE = "near_duplicate"
    BELOW_QUALITY_FLOOR = "below_quality_floor"
    EYES_CLOSED = "eyes_closed"
    EXCLUDED_CONTENT = "excluded_content"
    DIVERSITY_CONSTRAINT = "diversity_constraint"
    NO_SPACE = "no_space"
    PERSON_NOT_CONFIRMED = "person_not_confirmed"
    DPI_TOO_LOW = "dpi_too_low"
    USER_HIDDEN = "user_hidden"


class SelectionReportRejectedItem(ContractModel):
    media_id: Blake3Hash

    reason: SelectionReportRejectedItemReason


class SelectionReport(ContractModel):
    """
    Why these photos and not others. Kept with the spec because 'why is my best
    photo missing' is the most common question a user will ever ask about an album.
    """

    candidate_count: int | None = Field(default=None)

    selected_count: int | None = Field(default=None)

    diversity_constraints: list[SelectionReportDiversityConstraintsItem] = Field(
        default_factory=list,
    )

    rejected: list[SelectionReportRejectedItem] = Field(default_factory=list)


class SizeMm(ContractModel):
    width_mm: float

    height_mm: float


class SpreadHarmonySpreadsItemAdjustmentsItem(ContractModel):
    placement_id: Slug

    exposure_ev_delta: float = Field(default=0)

    temperature_k_delta: float = Field(default=0)

    tint_delta: float = Field(default=0)

    saturation_delta: float = Field(default=0)


class SpreadHarmonySpreadsItem(ContractModel):
    spread_id: Slug

    # Per-placement deltas the solver settled on. Deltas rather than absolutes so the
    # original image data stays the reference.
    adjustments: list[SpreadHarmonySpreadsItemAdjustmentsItem] = Field(
        description="Per-placement deltas the solver settled on. Deltas rather than absolutes so the original image data stays the reference.",
    )

    target_temperature_k: float | None = Field(default=None)

    target_exposure_ev: float | None = Field(default=None)


class SpreadHarmony(ContractModel):
    """
    Colour and exposure solved jointly across facing pages rather than per image. No
    consumer tool does this, and the difference is instantly visible in print: two
    photos of the same afternoon that disagree about white balance look like a
    mistake when they are 30cm apart on the same sheet.
    """

    enabled: bool

    spreads: list[SpreadHarmonySpreadsItem] = Field(default_factory=list)


class SpreadReviewStatus(str, Enum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    ISSUES_FOUND = "issues_found"
    ISSUES_FIXED = "issues_fixed"
    NEEDS_HUMAN = "needs_human"


class SpreadReviewFindingsItemKind(str, Enum):
    CROP_HITS_FACE = "crop_hits_face"
    NEAR_IDENTICAL_PAIR = "near_identical_pair"
    COLOR_CLASH = "color_clash"
    EXPOSURE_MISMATCH = "exposure_mismatch"
    WEAK_HERO = "weak_hero"
    CLUTTERED_SPREAD = "cluttered_spread"
    AWKWARD_CROP = "awkward_crop"
    TEXT_OVERLAPS_SUBJECT = "text_overlaps_subject"
    EYES_CLOSED = "eyes_closed"
    ORIENTATION_MISMATCH = "orientation_mismatch"


class SpreadReviewFindingsItemSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class SpreadReviewFindingsItemResolution(str, Enum):
    RECROPPED = "recropped"
    REPLACED = "replaced"
    REORDERED = "reordered"
    HARMONIZED = "harmonized"
    REMOVED = "removed"
    ACCEPTED_AS_IS = "accepted_as_is"
    ESCALATED_TO_HUMAN = "escalated_to_human"


class SpreadReviewFindingsItem(ContractModel):
    finding_id: Slug

    kind: SpreadReviewFindingsItemKind

    severity: SpreadReviewFindingsItemSeverity

    resolved: bool

    spread_id: Slug | None = Field(default=None)

    placement_ids: list[Slug] = Field(default_factory=list)

    comment: str = Field(default="")

    resolution: SpreadReviewFindingsItemResolution | None = Field(default=None)


class SpreadReview(ContractModel):
    """
    The automated QA pass: render each spread at low resolution, ask a frontier
    model for a structured critique, fix, re-check. This is what makes unattended
    output trustworthy, and like every Tier 3 call it sees only low-res renders and
    returns only structured decisions against ids.
    """

    status: SpreadReviewStatus

    model: ModelRef | None = Field(default=None)

    # Required whenever the review ran in the cloud, because low-res spread renders
    # left the device.
    consent: ConsentRef | None = Field(
        default=None,
        description="Required whenever the review ran in the cloud, because low-res spread renders left the device.",
    )

    prompt_id: Slug | None = Field(default=None)

    iterations: int = Field(default=0)

    findings: list[SpreadReviewFindingsItem] = Field(default_factory=list)


class TextBlockRole(str, Enum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    CAPTION = "caption"
    DATE = "date"
    PAGE_NUMBER = "page_number"
    QUOTE = "quote"


class TextBlockAlignment(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class TextBlock(ContractModel):
    block_id: Slug

    text: str

    frame: RectMm

    role: TextBlockRole = Field(default="caption")

    font_family: str = Field(default="")

    font_size_pt: float | None = Field(default=None)

    color_hex: str = Field(default="#000000")

    alignment: TextBlockAlignment = Field(default="left")


class ValidationCheckCheckId(str, Enum):
    DPI_FLOOR = "dpi_floor"
    FACE_IN_TRIM_ZONE = "face_in_trim_zone"
    BLEED_COVERAGE = "bleed_coverage"
    COLOR_PROFILE_MATCH = "color_profile_match"
    FACE_IN_GUTTER = "face_in_gutter"
    PAGE_COUNT_VALID = "page_count_valid"
    PLACEMENT_WITHIN_PAGE = "placement_within_page"
    CROP_ASPECT_MATCHES_FRAME = "crop_aspect_matches_frame"
    NO_DUPLICATE_ON_SPREAD = "no_duplicate_on_spread"
    TEXT_WITHIN_SAFE_MARGIN = "text_within_safe_margin"
    ENHANCEMENT_LICENSE_CLEARED = "enhancement_license_cleared"
    SOURCE_MEDIA_AVAILABLE = "source_media_available"
    PDF_STANDARD_SUPPORTED = "pdf_standard_supported"


class ValidationCheckSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationCheck(ContractModel):
    # The first four are the hard gates named in the build plan: DPI floor, face in
    # trim zone, bleed violation, mismatched colour profile. Any of them failing
    # blocks export outright.
    check_id: ValidationCheckCheckId = Field(
        description="The first four are the hard gates named in the build plan: DPI floor, face in trim zone, bleed violation, mismatched colour profile. Any of them failing blocks export outright.",
    )

    severity: ValidationCheckSeverity

    passed: bool

    page_index: int | None = Field(default=None)

    placement_id: Slug | None = Field(default=None)

    # What was actually measured, e.g. 214.7 for a DPI check.
    measured_value: float | None = Field(
        default=None,
        description="What was actually measured, e.g. 214.7 for a DPI check.",
    )

    required_value: float | None = Field(default=None)

    detail: str = Field(default="")

    # What would fix it: 'upscale source' or 'reduce frame to 180mm'. The review UI
    # shows this, and the album engine can often act on it automatically and re-
    # validate.
    remediation: str | None = Field(
        default=None,
        description="What would fix it: 'upscale source' or 'reduce frame to 180mm'. The review UI shows this, and the album engine can often act on it automatically and re-validate.",
    )


class VendorProfileColorProfileIntent(str, Enum):
    PERCEPTUAL = "perceptual"
    RELATIVE_COLORIMETRIC = "relative_colorimetric"
    SATURATION = "saturation"
    ABSOLUTE_COLORIMETRIC = "absolute_colorimetric"


class VendorProfileColorProfile(ContractModel):
    icc_name: str

    intent: VendorProfileColorProfileIntent

    icc_hash: Blake3Hash | None = Field(default=None)


class VendorProfilePageCount(ContractModel):
    minimum: int

    maximum: int

    # Pages are added in physical sheets, so a book is typically constrained to
    # multiples of 2 or 4. A spec with a page count off the increment is rejected by
    # the printer, not by us -- so we reject it first.
    increment: int = Field(
        description="Pages are added in physical sheets, so a book is typically constrained to multiples of 2 or 4. A spec with a page count off the increment is rejected by the printer, not by us -- so we reject it first.",
    )


class VendorProfileBinding(str, Enum):
    LAYFLAT = "layflat"
    PERFECT_BOUND = "perfect_bound"
    SADDLE_STITCH = "saddle_stitch"
    SPIRAL = "spiral"
    HARDCOVER_CASE = "hardcover_case"


class VendorProfilePdfStandard(str, Enum):
    PDF_X_1A = "pdf_x_1a"
    PDF_X_3 = "pdf_x_3"
    PDF_X_4 = "pdf_x_4"
    PDF_1_6 = "pdf_1_6"


class VendorProfile(ContractModel):
    """
    The printer's physical spec sheet, transcribed. Built to one real vendor first
    (build plan 4.6) because a validator built against an imagined spec validates
    nothing.
    """

    vendor_id: Slug

    product_id: Slug

    # Vendors change their spec sheets. A spec validated against v1 is not
    # automatically valid against v2, and the version pin is what makes that
    # detectable.
    profile_version: str = Field(
        description="Vendors change their spec sheets. A spec validated against v1 is not automatically valid against v2, and the version pin is what makes that detectable.",
    )

    trim_size_mm: SizeMm

    # How far artwork must extend beyond the trim line. Under-bleeding produces a
    # white sliver on the finished edge.
    bleed_mm: float = Field(
        description="How far artwork must extend beyond the trim line. Under-bleeding produces a white sliver on the finished edge.",
    )

    # Inset from trim within which nothing important may sit, because guillotines
    # drift.
    safe_margin_mm: float = Field(
        description="Inset from trim within which nothing important may sit, because guillotines drift.",
    )

    # Minimum effective resolution AT PRINTED SIZE. The whole point of the phrase: a
    # 24MP photo blown across a full spread can still fall below this.
    dpi_floor: float = Field(
        description="Minimum effective resolution AT PRINTED SIZE. The whole point of the phrase: a 24MP photo blown across a full spread can still fall below this.",
    )

    color_profile: VendorProfileColorProfile

    # Dead zone at the spine. On a perfect-bound book this can swallow 10mm+ of a
    # spread, which is why a face landing in it is a hard failure rather than a
    # warning.
    gutter_mm: float = Field(
        default=0,
        description="Dead zone at the spine. On a perfect-bound book this can swallow 10mm+ of a spread, which is why a face landing in it is a hard failure rather than a warning.",
    )

    spine_mm: float | None = Field(default=None)

    dpi_preferred: float | None = Field(default=None)

    page_count: VendorProfilePageCount | None = Field(default=None)

    binding: VendorProfileBinding = Field(default="layflat")

    paper_stock: str | None = Field(default=None)

    pdf_standard: VendorProfilePdfStandard = Field(default="pdf_x_4")


class AlbumSpec(ContractModel):
    """
    The deterministic plan for one printed album: which photos, on which pages,
    cropped how, enhanced how, against which vendor's physical spec.
    """

    schema_version: SchemaVersion

    # BLAKE3 over the canonical JSON of this spec with volatile fields removed. Two
    # specs with the same id produce the same PDF.
    album_id: Blake3Hash = Field(
        description="BLAKE3 over the canonical JSON of this spec with volatile fields removed. Two specs with the same id produce the same PDF.",
    )

    vendor_profile: VendorProfile

    # Ordered pages. Page 0 is the front cover when the vendor profile includes one.
    # Spreads are expressed by pairing pages via spread_id rather than by modelling a
    # spread as a single wide page, because the gutter falls between two physically
    # separate sheets and each has its own safe zone.
    pages: list[Page] = Field(
        description="Ordered pages. Page 0 is the front cover when the vendor profile includes one. Spreads are expressed by pairing pages via spread_id rather than by modelling a spread as a single wide page, because the gutter falls between two physically ...",
    )

    determinism: Determinism

    validation: PrintValidationReport

    title: str = Field(default="")

    subtitle: str | None = Field(default=None)

    event: EventContext | None = Field(default=None)

    selection: SelectionReport | None = Field(default=None)

    spread_harmony: SpreadHarmony | None = Field(default=None)

    review: SpreadReview | None = Field(default=None)


class AspectRatio(ContractModel):
    """
    Exact aspect ratio as integers, e.g. 9:16 for a reel. Integers rather than a
    float so 'is this 16:9' is an equality test, not an epsilon comparison.
    """

    numerator: int

    denominator: int


# Lowercase hex BLAKE3-256 digest. The universal content address: same bytes anywhere in
# the world produce the same id, which is what makes every job idempotent.
Blake3Hash = str


# Calibrated probability in [0,1]. Distinct from Unit by intent: a Confidence is expected
# to be calibrated against a validation set and is therefore comparable to a threshold. A
# Unit is merely ordered.
Confidence = float


class ConsentRefScope(str, Enum):
    TIER3_CONTACT_SHEET = "tier3_contact_sheet"
    CLOUD_RENDER = "cloud_render"
    CLOUD_BACKUP = "cloud_backup"
    SHARE_LINK = "share_link"
    PRINT_ORDER = "print_order"
    MINOR_FACE_LABELING = "minor_face_labeling"
    ANONYMIZED_PREFERENCE_TRAINING = "anonymized_preference_training"


class ConsentRef(ContractModel):
    """
    Pointer into the consent ledger owned by services/api. Required on anything that
    leaves the device or touches a child's face. Hard rule: no network egress
    without a ledger entry, verified by the CI egress test.
    """

    ledger_entry_id: Uuid

    scope: ConsentRefScope

    granted_at: Timestamp

    expires_at: Timestamp | None = Field(default=None)

    revoked_at: Timestamp | None = Field(default=None)


class Determinism(ContractModel):
    """
    Everything needed to reproduce a plan byte-for-byte. Present on every artifact a
    planner emits (EDL, AlbumSpec). Hard rule 3: same plan + same sources =
    identical output, and that is only auditable if the plan says what produced it.
    """

    planner: Slug

    planner_version: str

    # Seed for every stochastic choice in planning (variant sampling, tie-breaking).
    # Same seed + same inputs must yield the same plan.
    seed: int = Field(
        description="Seed for every stochastic choice in planning (variant sampling, tie-breaking). Same seed + same inputs must yield the same plan.",
    )

    # BLAKE3 over the canonical JSON of every input the planner read: candidate ids,
    # parameters, model refs. Two plans with the same digest and the same planner
    # version are guaranteed identical.
    inputs_digest: Blake3Hash = Field(
        description="BLAKE3 over the canonical JSON of every input the planner read: candidate ids, parameters, model refs. Two plans with the same digest and the same planner version are guaranteed identical.",
    )

    generated_at: Timestamp | None = Field(default=None)


class GeoPointSource(str, Enum):
    EXIF_GPS = "exif_gps"
    QUICKTIME_LOCATION = "quicktime_location"
    XMP = "xmp"
    SIDECAR_JSON = "sidecar_json"
    USER_SUPPLIED = "user_supplied"
    INFERRED = "inferred"


class GeoPoint(ContractModel):
    latitude: float

    longitude: float

    source: GeoPointSource

    altitude_m: float | None = Field(default=None)

    horizontal_accuracy_m: float | None = Field(default=None)


# ISO 8601 date-time with NO offset, e.g. 2019-08-04T17:22:31. This is what a camera
# actually writes into EXIF DateTimeOriginal: a wall-clock reading with no timezone.
# Storing it as a naive local time and keeping the zone separate is the only lossless
# representation.
LocalDateTime = str


class ModelRefPrecision(str, Enum):
    FP32 = "fp32"
    FP16 = "fp16"
    BF16 = "bf16"
    INT8 = "int8"
    INT4 = "int4"


class ModelRef(ContractModel):
    """
    Pin to an exact model in the registry. Carries the weights hash, not just a
    version string, because 'the same version' of a HuggingFace repo has changed
    weights under people before. A record produced by an unpinned model is not
    reproducible, and reproducibility is the product.
    """

    model_id: Slug

    # The registry's version string for this model. Free-form apart from one
    # exclusion: no C0 control character and no DEL. FaceRecord.face_id joins model_id
    # and version with U+001F, and a version containing that separator would let two
    # different (model, version) pairs produce one identical byte string and therefore
    # one identical face id. Excluding the separator structurally is what lets that
    # encoding skip length prefixes; leaving it to convention is how the collision
    # gets found in a family album instead of here.
    version: str = Field(
        description="The registry's version string for this model. Free-form apart from one exclusion: no C0 control character and no DEL. FaceRecord.face_id joins model_id and version with U+001F, and a version containing that separator would let two differ...",
    )

    # BLAKE3 of the weights file, or null when the entry is unpinned. Null is
    # permitted ONLY because development mode permits loading unpinned weights; a null
    # here is exactly what makes a record non-reproducible, and release mode refuses
    # to produce one.
    weights_blake3: Blake3Hash | None = Field(
        description="BLAKE3 of the weights file, or null when the entry is unpinned. Null is permitted ONLY because development mode permits loading unpinned weights; a null here is exactly what makes a record non-reproducible, and release mode refuses to pr...",
    )

    runtime: RuntimeTarget | None = Field(default=None)

    # Quantisation the weights were executed at. int8 and fp16 runs can differ from
    # fp32 at the third decimal, which is enough to flip a borderline face match, so
    # it is part of provenance.
    precision: ModelRefPrecision | None = Field(
        default=None,
        description="Quantisation the weights were executed at. int8 and fp16 runs can differ from fp32 at the third decimal, which is enough to flip a borderline face match, so it is part of provenance.",
    )

    # BLAKE3 of the model config file that governed this run. Weights alone do not pin
    # behaviour: input size, normalisation constants, score threshold, NMS IoU and the
    # alignment template all live in the config, and changing any of them changes
    # every downstream decision while the weights hash stays byte-identical. Null only
    # for classical measures with no model config.
    config_blake3: Blake3Hash | None = Field(
        default=None,
        description="BLAKE3 of the model config file that governed this run. Weights alone do not pin behaviour: input size, normalisation constants, score threshold, NMS IoU and the alignment template all live in the config, and changing any of them changes...",
    )


class ModelRun(ContractModel):
    """
    One execution of one model against one record. Every score in this contract
    points at a run id, so 'why is this photo ranked 0.82' is always answerable and
    a model swap can be evaluated by replaying only the affected runs.
    """

    run_id: Slug

    model: ModelRef

    ran_at: Timestamp

    # Which proxy the model actually saw. Analysis never touches originals (AGENTS.md
    # hard rule 5), so this is normally set; null only for classical measures computed
    # during ingest.
    input_proxy_id: Blake3Hash | None = Field(
        default=None,
        description="Which proxy the model actually saw. Analysis never touches originals (AGENTS.md hard rule 5), so this is normally set; null only for classical measures computed during ingest.",
    )

    duration_ms: float | None = Field(default=None)

    job_id: Blake3Hash | None = Field(default=None)


class NormalizedBox(ContractModel):
    """
    Axis-aligned rectangle in normalised image coordinates: origin top-left, x to
    the right, y down, all values in [0,1] relative to the ORIENTED image (after
    EXIF rotation is applied). Normalised so a box computed on a 512px thumbnail is
    valid against the 6000px original without rescaling -- this is what lets
    analysis run on proxies and render run on sources.
    """

    x: float

    y: float

    w: float

    h: float

    # Clockwise rotation of the box about its own centre. Present only for crops that
    # deliberately rotate, e.g. straightening a horizon in an album placement.
    rotation_deg: float = Field(
        default=0,
        description="Clockwise rotation of the box about its own centre. Present only for crops that deliberately rotate, e.g. straightening a horizon in an album placement.",
    )


class PerceptualHashAlgorithm(str, Enum):
    PHASH_DCT_64_V2 = "phash-dct-64-v2"
    PHASH_DCT_64 = "phash-dct-64"
    PHASH_DCT_256 = "phash-dct-256"
    DHASH_64 = "dhash-64"
    AHASH_64 = "ahash-64"
    WAVELET_64 = "wavelet-64"


class PerceptualHashBits(int, Enum):
    V_64 = 64
    V_128 = 128
    V_256 = 256


class PerceptualHash(ContractModel):
    """
    Perceptual hash used for near-duplicate bucketing. Bucketing is by Hamming
    distance on this hash; the bucket is then refined by embedding distance (build
    plan 4.2). Always carries its algorithm so a future algorithm change cannot
    silently invalidate existing buckets.
    """

    # Which hash this is. Two digests may only be compared when this value is equal on
    # both -- equal `bits` is NOT sufficient, and the name is the comparison key, not
    # a label. See the $comment on PerceptualHash for the encoding of `phash-
    # dct-64-v2` and for what `phash-dct-64` is still doing in this list.
    algorithm: PerceptualHashAlgorithm = Field(
        description="Which hash this is. Two digests may only be compared when this value is equal on both -- equal `bits` is NOT sufficient, and the name is the comparison key, not a label. See the $comment on PerceptualHash for the encoding of `phash-dct-6...",
    )

    # Hash length in bits. Enforced to equal 4 * len(hex).
    bits: PerceptualHashBits = Field(
        description="Hash length in bits. Enforced to equal 4 * len(hex).",
    )

    # Lowercase hex digest. Its length is pinned to `bits` by the constraints below.
    hex: str = Field(
        description="Lowercase hex digest. Its length is pinned to `bits` by the constraints below.",
    )


class PixelSize(ContractModel):
    width: int

    height: int


class Point2D(ContractModel):
    """
    Point in the same normalised, orientation-applied coordinate space as
    NormalizedBox. Landmarks may fall slightly outside [0,1] when a face is clipped
    by the frame edge, so the bounds here are deliberately loose.
    """

    x: float

    y: float


class RationalTime(ContractModel):
    """
    A time expressed as an exact rational: value frames (or samples) at the given
    rate. Maps 1:1 onto opentimelineio.opentime.RationalTime. Seconds = value /
    rate. Never store seconds as a float in this contract.
    """

    # Position or count in units of 1/rate. May be fractional to survive sample-
    # accurate audio edits, but integral values are strongly preferred on video
    # tracks.
    value: float = Field(
        description="Position or count in units of 1/rate. May be fractional to survive sample-accurate audio edits, but integral values are strongly preferred on video tracks.",
    )

    # Units per second. Use the exact NTSC rationals where applicable: 24000/1001 =
    # 23.976023976023978, 30000/1001 = 29.97002997002997, 60000/1001 =
    # 59.94005994005994.
    rate: float = Field(
        description="Units per second. Use the exact NTSC rationals where applicable: 24000/1001 = 23.976023976023978, 30000/1001 = 29.97002997002997, 60000/1001 = 59.94005994005994.",
    )


class RuntimeTarget(str, Enum):
    ONNXRUNTIME_CPU = "onnxruntime_cpu"
    ONNXRUNTIME_COREML = "onnxruntime_coreml"
    ONNXRUNTIME_DIRECTML = "onnxruntime_directml"
    ONNXRUNTIME_CUDA = "onnxruntime_cuda"
    CTRANSLATE2 = "ctranslate2"
    MLX = "mlx"
    LLAMA_CPP = "llama_cpp"
    OPENCV = "opencv"
    LIBROSA = "librosa"
    NATIVE = "native"


# Contract version this record was written against. Frozen at 'v0' for the Phase 0
# contract. A reader that does not recognise the value must refuse the record rather than
# guess -- see hard rule 7, no silent anything.
SchemaVersion = Literal["v0"]


class Score(ContractModel):
    """
    A single scored value with a pointer back to the run that produced it.
    """

    value: Unit

    run_id: Slug | None = Field(default=None)

    # Model output before normalisation to [0,1], kept so a recalibration can be
    # applied without re-running the model.
    raw_value: float | None = Field(
        default=None,
        description="Model output before normalisation to [0,1], kept so a recalibration can be applied without re-running the model.",
    )


# Short stable machine identifier, lowercase alphanumeric with hyphens and underscores.
# Used for ids that are authored by us rather than generated: track ids, act ids, check
# ids.
Slug = str


class TimeAssertionPrecision(str, Enum):
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    UNKNOWN = "unknown"


class TimeAssertion(ContractModel):
    """
    A claim about when something was captured, together with how much we believe it.
    Modelling capture time as an assertion rather than a bare timestamp is what lets
    the system ingest a library where a third of the files have no EXIF date at all
    without either dropping them or lying about their chronology.
    """

    source: TimeSource

    # Granularity the assertion is actually good to. 'unknown' means we have no usable
    # time at all, and consumers must exclude the item from chronology-ordered output
    # rather than sorting it to the epoch.
    precision: TimeAssertionPrecision = Field(
        description="Granularity the assertion is actually good to. 'unknown' means we have no usable time at all, and consumers must exclude the item from chronology-ordered output rather than sorting it to the epoch.",
    )

    confidence: Confidence

    # Wall-clock reading as recorded by the device, with no zone applied. Null when
    # nothing in the file or its neighbours implies a time.
    local: LocalDateTime | None = Field(
        default=None,
        description="Wall-clock reading as recorded by the device, with no zone applied. Null when nothing in the file or its neighbours implies a time.",
    )

    # Resolved instant, present only when the zone is actually known (explicit offset
    # in metadata, or GPS-derived zone). Never fabricate this by assuming the
    # machine's local zone -- that silently shifts an entire holiday by hours.
    utc: Timestamp | None = Field(
        default=None,
        description="Resolved instant, present only when the zone is actually known (explicit offset in metadata, or GPS-derived zone). Never fabricate this by assuming the machine's local zone -- that silently shifts an entire holiday by hours.",
    )

    # IANA zone name, e.g. Asia/Kolkata, when it could be determined from metadata or
    # GPS coordinates.
    timezone: str | None = Field(
        default=None,
        description="IANA zone name, e.g. Asia/Kolkata, when it could be determined from metadata or GPS coordinates.",
    )

    # Offset actually recorded in the file (EXIF OffsetTimeOriginal or QuickTime),
    # when present.
    utc_offset_minutes: int | None = Field(
        default=None,
        description="Offset actually recorded in the file (EXIF OffsetTimeOriginal or QuickTime), when present.",
    )

    # When precision came from neighbour_interpolation, the sibling records that
    # bracketed this one. Recorded so the inference is auditable and can be recomputed
    # when a neighbour's date is later corrected.
    inferred_from_media_ids: list[Blake3Hash] = Field(
        default_factory=list,
        description="When precision came from neighbour_interpolation, the sibling records that bracketed this one. Recorded so the inference is auditable and can be recomputed when a neighbour's date is later corrected.",
    )


class TimeRange(ContractModel):
    """
    Half-open interval [start_time, start_time + duration). Maps 1:1 onto
    opentimelineio.opentime.TimeRange. Half-open is deliberate and matches OTIO: it
    makes adjacent clips tile a timeline with no off-by-one frame.
    """

    start_time: RationalTime

    duration: RationalTime


class TimeSource(str, Enum):
    """
    Where a capture time came from, ordered from most to least trustworthy. The
    ranking is load-bearing: event clustering weights a filename-derived date far
    less than an EXIF original, and an inferred date not at all for chronology-
    critical decisions.
    """

    EXIF_DATETIME_ORIGINAL = "exif_datetime_original"
    EXIF_DATETIME_DIGITIZED = "exif_datetime_digitized"
    QUICKTIME_CREATION_DATE = "quicktime_creation_date"
    XMP_CREATE_DATE = "xmp_create_date"
    GPS_TIMESTAMP = "gps_timestamp"
    SIDECAR_JSON = "sidecar_json"
    FILENAME_PATTERN = "filename_pattern"
    FILESYSTEM_MTIME = "filesystem_mtime"
    NEIGHBOUR_INTERPOLATION = "neighbour_interpolation"
    USER_SUPPLIED = "user_supplied"
    UNKNOWN = "unknown"


# RFC 3339 instant with an explicit offset. Every wall-clock moment the system itself
# observes (ingest time, decision time, render time) is unambiguous by construction.
Timestamp = str


# A score normalised to [0,1]. Every model output in this contract is normalised before it
# is written, so fusion never has to know a model's native range.
Unit = float


# RFC 4122 UUID, lowercase. Used only for entities whose identity is NOT determined by
# content: person ids, cluster ids, user sessions, projects.
Uuid = str


class VectorRefStorage(str, Enum):
    INDEX = "index"
    INLINE = "inline"


class VectorRefQuantization(str, Enum):
    FLOAT32 = "float32"
    FLOAT16 = "float16"
    INT8 = "int8"
    BINARY = "binary"


class VectorRef(ContractModel):
    """
    Reference to an embedding held in the vector index. Embeddings are referenced
    rather than inlined so a MediaRecord stays small enough to page through 100k of
    them in a UI; the index owns the floats. Inline values are permitted ONLY for
    fixtures and tests, where self-containment matters more than size.
    """

    space: VectorSpace

    dimensions: int

    storage: VectorRefStorage

    # Row key in the sqlite-vec table. Required when storage is 'index'.
    index_key: str | None = Field(
        default=None,
        description="Row key in the sqlite-vec table. Required when storage is 'index'.",
    )

    # Raw float components. Permitted only when storage is 'inline'. Length must equal
    # `dimensions`.
    values: list[float] | None = Field(
        default=None,
        description="Raw float components. Permitted only when storage is 'inline'. Length must equal `dimensions`.",
    )

    quantization: VectorRefQuantization = Field(default="float32")

    # True when the vector is L2-normalised, which makes cosine distance a dot
    # product. Every space in this contract stores normalised vectors.
    normalized: bool = Field(
        default=True,
        description="True when the vector is L2-normalised, which makes cosine distance a dot product. Every space in this contract stores normalised vectors.",
    )


class VectorSpace(str, Enum):
    """
    Named embedding space. Two vectors may only be compared when their space matches
    exactly, including the model version that produced them -- a SigLIP 2 upgrade
    creates a NEW space, it does not reinterpret the old one.
    """

    SIGLIP2_BASE_768 = "siglip2_base_768"
    SIGLIP2_SO400M_1152 = "siglip2_so400m_1152"
    ARCFACE_BUFFALO_L_512 = "arcface_buffalo_l_512"
    ADAFACE_IR101_512 = "adaface_ir101_512"
    CLAP_AUDIO_512 = "clap_audio_512"
    AESTHETIC_HEAD_1 = "aesthetic_head_1"


class Act(ContractModel):
    act_id: Slug

    name: str

    # What this act is for, in plain language: 'arrival, establish where we are'.
    intent: str | None = Field(
        default=None,
        description="What this act is for, in plain language: 'arrival, establish where we are'.",
    )

    timeline_range: TimeRange | None = Field(default=None)

    target_energy: Unit | None = Field(default=None)

    beats: list[StoryBeat] = Field(default_factory=list)


class AmbientPlanNoiseSuppression(str, Enum):
    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class AmbientPlanPerClipGainDbItem(ContractModel):
    clip_id: Slug

    gain_db: float


class AmbientPlan(ContractModel):
    """
    How much of the original location sound survives. Keeping real ambient under
    music is most of what separates a film that feels like a memory from a slideshow
    with a soundtrack.
    """

    enabled: bool = Field(default=True)

    default_gain_db: float = Field(default=-12)

    # When true, clips containing speech keep their ambient at full level and the
    # music ducks under them instead.
    preserve_speech: bool = Field(
        default=True,
        description="When true, clips containing speech keep their ambient at full level and the music ducks under them instead.",
    )

    # Removes wind rumble, which otherwise dominates every outdoor action clip.
    high_pass_hz: float | None = Field(
        default=None,
        description="Removes wind rumble, which otherwise dominates every outdoor action clip.",
    )

    noise_suppression: AmbientPlanNoiseSuppression = Field(default="light")

    per_clip_gain_db: list[AmbientPlanPerClipGainDbItem] = Field(default_factory=list)


class AudioPlan(ContractModel):
    """
    The complete audio intention: what music plays, how much of the original scene
    survives under it, and how the two are balanced against each other over time.
    """

    mix: MixPlan

    music: list[MusicCue] = Field(default_factory=list)

    ambient: AmbientPlan | None = Field(default=None)

    # Ordered ducking rules. Later rules win where they overlap, which keeps the
    # resolution deterministic instead of depending on the renderer's mixer
    # implementation.
    ducking: list[DuckingRule] = Field(
        default_factory=list,
        description="Ordered ducking rules. Later rules win where they overlap, which keeps the resolution deterministic instead of depending on the renderer's mixer implementation.",
    )


class BeatSection(str, Enum):
    INTRO = "intro"
    VERSE = "verse"
    PRE_CHORUS = "pre_chorus"
    CHORUS = "chorus"
    DROP = "drop"
    BRIDGE = "bridge"
    BREAKDOWN = "breakdown"
    OUTRO = "outro"


class Beat(ContractModel):
    index: int

    time: RationalTime

    is_downbeat: bool

    bar: int | None = Field(default=None)

    beat_in_bar: int | None = Field(default=None)

    strength: Unit | None = Field(default=None)

    # Musical section this beat falls in. Lets the planner put the visual peak on the
    # drop rather than merely on a loud beat.
    section: BeatSection | None = Field(
        default=None,
        description="Musical section this beat falls in. Lets the planner put the visual peak on the drop rather than merely on a loud beat.",
    )


class BeatGridTimeSignatureBeatUnit(int, Enum):
    V_1 = 1
    V_2 = 2
    V_4 = 4
    V_8 = 8
    V_16 = 16


class BeatGridTimeSignature(ContractModel):
    beats_per_bar: int

    beat_unit: BeatGridTimeSignatureBeatUnit


class BeatGrid(ContractModel):
    """
    The musical skeleton the cut is hung on. Stored as explicit per-beat times
    rather than as a BPM to be extrapolated, because real tracks drift and an
    extrapolated grid is 200ms out by the end of a 30-second reel.
    """

    # Which MusicCue this grid describes.
    source_cue_id: Slug = Field(description="Which MusicCue this grid describes.")

    bpm: float

    # Every beat, in TIMELINE time, ordered. Downbeats are flagged rather than stored
    # separately so a cut can reference one index regardless of which it turned out to
    # be.
    beats: list[Beat] = Field(
        description="Every beat, in TIMELINE time, ordered. Downbeats are flagged rather than stored separately so a cut can reference one index regardless of which it turned out to be.",
    )

    bpm_confidence: Confidence | None = Field(default=None)

    time_signature: BeatGridTimeSignature | None = Field(default=None)

    # Which beat tracker produced this. Recorded partly for reproducibility and partly
    # because the licence-safe analyser (librosa, ISC) and the more accurate but non-
    # commercial ones (madmom) must never be confused.
    analyzer: ModelRef | None = Field(
        default=None,
        description="Which beat tracker produced this. Recorded partly for reproducibility and partly because the licence-safe analyser (librosa, ISC) and the more accurate but non-commercial ones (madmom) must never be confused.",
    )

    # Maximum acceptable beat-alignment error for a cut claiming to be beat-locked.
    # The quality gate is 50ms on downbeats.
    tolerance_ms: float = Field(
        default=50,
        description="Maximum acceptable beat-alignment error for a cut claiming to be beat-locked. The quality gate is 50ms on downbeats.",
    )


class BeatLock(ContractModel):
    """
    Records that a cut was placed against the music rather than merely near it.
    `alignment_error_ms` is the audit trail for the <50ms downbeat quality gate.
    """

    # Index into BeatGrid.beats.
    beat_index: int = Field(description="Index into BeatGrid.beats.")

    # Signed distance from the clip's timeline in-point to the beat. Negative is
    # early. Non-zero because a cut must also land on a certified snap point, and the
    # nearest snap point is rarely exactly on the beat -- the planner trades a few
    # milliseconds of beat error for a cut that lands on a real motion onset.
    alignment_error_ms: float = Field(
        description="Signed distance from the clip's timeline in-point to the beat. Negative is early. Non-zero because a cut must also land on a certified snap point, and the nearest snap point is rarely exactly on the beat -- the planner trades a few milli...",
    )

    is_downbeat: bool = Field(default=False)

    # Which MomentRecord snap point the cut actually landed on.
    snap_point_kind: str | None = Field(
        default=None,
        description="Which MomentRecord snap point the cut actually landed on.",
    )


class Clip(ContractModel):
    item_type: Literal["clip"]

    clip_id: Slug

    media_ref_id: Slug

    # In and out in SOURCE time, half-open. The single most important field in the
    # contract: it is what the renderer seeks to, and it must round-trip through OTIO
    # unchanged.
    source_range: TimeRange = Field(
        description="In and out in SOURCE time, half-open. The single most important field in the contract: it is what the renderer seeks to, and it must round-trip through OTIO unchanged.",
    )

    name: str = Field(default="")

    # Derived position on the timeline. Carried for validation only; excluded from the
    # determinism digest and not exported to OTIO, which recomputes it. Its DURATION
    # is derived from source_range and any time_effect by the rule in TimeEffect's
    # $comment -- equal to source_range.duration when there is no effect -- and its
    # START is the running sum of the extents before it. A timeline_range that
    # disagrees with that arithmetic is a validation failure, never a correction.
    timeline_range: TimeRange | None = Field(
        default=None,
        description="Derived position on the timeline. Carried for validation only; excluded from the determinism digest and not exported to OTIO, which recomputes it. Its DURATION is derived from source_range and any time_effect by the rule in TimeEffect's ...",
    )

    # The MomentRecord this clip realises. The provenance link that lets 'more of her'
    # re-plan against the same candidate pool instead of starting over.
    moment_id: Blake3Hash | None = Field(
        default=None,
        description="The MomentRecord this clip realises. The provenance link that lets 'more of her' re-plan against the same candidate pool instead of starting over.",
    )

    enabled: bool = Field(default=True)

    time_effect: TimeEffect | None = Field(default=None)

    # Reframe track driving this clip's crop. Null means full frame, letterboxed or
    # pillarboxed per the target.
    reframe_track_id: Slug | None = Field(
        default=None,
        description="Reframe track driving this clip's crop. Null means full frame, letterboxed or pillarboxed per the target.",
    )

    color_ops: list[ColorOp] = Field(default_factory=list)

    audio: ClipAudio | None = Field(default=None)

    # Present when this clip's in-point was snapped to the beat grid. Carries the
    # alignment error so the <50ms quality gate is measurable from the plan alone,
    # without rendering anything.
    beat_lock: BeatLock | None = Field(
        default=None,
        description="Present when this clip's in-point was snapped to the beat grid. Carries the alignment error so the <50ms quality gate is measurable from the plan alone, without rendering anything.",
    )

    # Which story-arc beat this clip satisfies. Null on clips that are connective
    # tissue rather than a required beat.
    story_beat_id: Slug | None = Field(
        default=None,
        description="Which story-arc beat this clip satisfies. Null on clips that are connective tissue rather than a required beat.",
    )

    markers: list[Marker] = Field(default_factory=list)


class ClipAudio(ContractModel):
    gain_db: float = Field(default=0)

    muted: bool = Field(default=False)

    # Fade length at the clip's in-point. The curve is a LINEAR RAMP IN AMPLITUDE from
    # 0 to 1 over the declared frames -- not equal-power, not linear in dB
    # (contracts#60). Equal-power is the usual choice for a music crossfade and would
    # be audibly different on a long fade, so it is named here rather than left to the
    # mixer; a planner that wants a different shape emits a Transition.
    fade_in: RationalTime | None = Field(
        default=None,
        description="Fade length at the clip's in-point. The curve is a LINEAR RAMP IN AMPLITUDE from 0 to 1 over the declared frames -- not equal-power, not linear in dB (contracts#60). Equal-power is the usual choice for a music crossfade and would be audi...",
    )

    # Fade length ending on the clip's last frame, a linear ramp in amplitude from 1
    # to 0. Same convention as fade_in.
    fade_out: RationalTime | None = Field(
        default=None,
        description="Fade length ending on the clip's last frame, a linear ramp in amplitude from 1 to 0. Same convention as fade_in.",
    )

    # L-cut: hold this clip's audio past its visual out-point, so a laugh finishes
    # over the next shot. Realises MomentRecord.safe_trim.preserve_audio_tail.
    audio_extends_past_out: RationalTime | None = Field(
        default=None,
        description="L-cut: hold this clip's audio past its visual out-point, so a laugh finishes over the next shot. Realises MomentRecord.safe_trim.preserve_audio_tail.",
    )


class ColorOpOp(str, Enum):
    EXPOSURE = "exposure"
    CONTRAST = "contrast"
    SATURATION = "saturation"
    TEMPERATURE = "temperature"
    TINT = "tint"
    HIGHLIGHTS = "highlights"
    SHADOWS = "shadows"
    LIFT = "lift"
    GAMMA = "gamma"
    GAIN = "gain"
    LUT = "lut"
    AUTO_WHITE_BALANCE = "auto_white_balance"
    MATCH_TO_REFERENCE = "match_to_reference"


class ColorOp(ContractModel):
    """
    A per-clip colour adjustment, planned by the intelligence layer and merely
    applied by the renderer.
    """

    op: ColorOpOp

    # Signed, normalised to [-1,1] where 0 is no change. A single normalised scale
    # keeps ops composable and makes 'is this grade aggressive' a question with an
    # answer.
    amount: float = Field(
        description="Signed, normalised to [-1,1] where 0 is no change. A single normalised scale keeps ops composable and makes 'is this grade aggressive' a question with an answer.",
    )

    lut_id: Slug | None = Field(default=None)

    # For match_to_reference: the clip whose look this one is being matched to. Shot-
    # to-shot consistency inside a scene is planned, not left to the encoder.
    reference_clip_id: Slug | None = Field(
        default=None,
        description="For match_to_reference: the clip whose look this one is being matched to. Shot-to-shot consistency inside a scene is planned, not left to the encoder.",
    )


class ColorPipelineWorkingSpace(str, Enum):
    SRGB = "srgb"
    REC709 = "rec709"
    REC2020 = "rec2020"
    ACES_CCT = "aces_cct"
    LINEAR = "linear"


class ColorPipeline(ContractModel):
    input_transform: str = Field(default="auto")

    working_space: ColorPipelineWorkingSpace = Field(default="rec709")

    output_transform: str = Field(default="rec709")

    # Required when any source is HLG or PQ and the target is SDR. Left implicit, HDR
    # sources render washed out.
    tone_map_hdr_to_sdr: bool = Field(
        default=True,
        description="Required when any source is HLG or PQ and the target is SDR. Left implicit, HDR sources render washed out.",
    )


class DuckingRuleTarget(str, Enum):
    MUSIC = "music"
    AMBIENT = "ambient"
    SFX = "sfx"


class DuckingRuleTrigger(str, Enum):
    SPEECH = "speech"
    MUSIC = "music"
    AMBIENT = "ambient"
    EXPLICIT_RANGES = "explicit_ranges"


class DuckingRule(ContractModel):
    """
    One sidechain relationship, expressed as intent (duck music under speech by 9dB)
    rather than as a rendered gain curve, so the renderer stays dumb and the
    decision stays auditable.
    """

    rule_id: Slug

    # What gets turned down.
    target: DuckingRuleTarget = Field(description="What gets turned down.")

    # What turns it down. `explicit_ranges` means the planner decided the ranges
    # itself rather than relying on detection at render time -- always preferred,
    # because it is deterministic.
    trigger: DuckingRuleTrigger = Field(
        description="What turns it down. `explicit_ranges` means the planner decided the ranges itself rather than relying on detection at render time -- always preferred, because it is deterministic.",
    )

    # Positive number of dB to reduce by.
    reduction_db: float = Field(description="Positive number of dB to reduce by.")

    threshold_db: float | None = Field(default=None)

    ratio: float | None = Field(default=None)

    attack_ms: float = Field(default=80)

    release_ms: float = Field(default=300)

    # Timeline ranges the rule applies over. Required when trigger is explicit_ranges;
    # when empty with another trigger, the rule applies for the whole timeline.
    ranges: list[TimeRange] = Field(
        default_factory=list,
        description="Timeline ranges the rule applies over. Required when trigger is explicit_ranges; when empty with another trigger, the rule applies for the whole timeline.",
    )


class EdlValidationStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_RUN = "not_run"


class EdlValidationChecksItemCheckId(str, Enum):
    SOURCE_RANGE_WITHIN_AVAILABLE = "source_range_within_available"
    MEDIA_REFS_RESOLVABLE = "media_refs_resolvable"
    TIMELINE_CONTIGUOUS = "timeline_contiguous"
    TIME_EFFECT_EXTENT_DERIVED = "time_effect_extent_derived"
    MUSIC_CUES_PLACED_ONCE = "music_cues_placed_once"
    TRANSITION_HANDLES_AVAILABLE = "transition_handles_available"
    BEAT_ALIGNMENT_WITHIN_TOLERANCE = "beat_alignment_within_tolerance"
    NO_MID_WORD_CUT = "no_mid_word_cut"
    REFRAME_ASPECT_MATCHES_TARGET = "reframe_aspect_matches_target"
    REFRAME_KEYFRAMES_ORDERED = "reframe_keyframes_ordered"
    DURATION_WITHIN_MAX = "duration_within_max"
    MUSIC_LICENSE_COVERS_DESTINATION = "music_license_covers_destination"
    REQUIRED_STORY_BEATS_SATISFIED = "required_story_beats_satisfied"
    AUDIO_LOUDNESS_TARGET_SET = "audio_loudness_target_set"
    DETERMINISM_DIGEST_PRESENT = "determinism_digest_present"


class EdlValidationChecksItemSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class EdlValidationChecksItem(ContractModel):
    check_id: EdlValidationChecksItemCheckId

    passed: bool

    severity: EdlValidationChecksItemSeverity

    detail: str = Field(default="")

    clip_id: Slug | None = Field(default=None)


class EdlValidation(ContractModel):
    """
    Result of the pre-render checks. The renderer refuses an EDL that has not
    passed, which keeps 'the renderer is dumb' from meaning 'the renderer is
    trusting'.
    """

    status: EdlValidationStatus

    checks: list[EdlValidationChecksItem]

    validated_at: Timestamp | None = Field(default=None)

    validator_version: str | None = Field(default=None)


class GapFill(str, Enum):
    BLACK = "black"
    WHITE = "white"
    TRANSPARENT = "transparent"
    SILENCE = "silence"


class Gap(ContractModel):
    """
    Explicit silence or black. Modelled explicitly, exactly as OTIO does, so a hold
    at the end of a film is a stated intention rather than an accident of
    arithmetic.
    """

    item_type: Literal["gap"]

    duration: RationalTime

    gap_id: Slug | None = Field(default=None)

    fill: GapFill = Field(default="black")


class MarkerColor(str, Enum):
    RED = "RED"
    GREEN = "GREEN"
    BLUE = "BLUE"
    CYAN = "CYAN"
    MAGENTA = "MAGENTA"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    PURPLE = "PURPLE"
    WHITE = "WHITE"
    BLACK = "BLACK"
    PINK = "PINK"
    MINT = "MINT"


class MarkerKind(str, Enum):
    BEAT = "beat"
    DOWNBEAT = "downbeat"
    STORY_BEAT = "story_beat"
    EMOTIONAL_PEAK = "emotional_peak"
    SPEECH = "speech"
    WARNING = "warning"
    NOTE = "note"


class Marker(ContractModel):
    """
    Maps to otio.schema.Marker. Markers are how our reasoning becomes visible to a
    human editor who opens the timeline in Resolve.
    """

    name: str

    marked_range: TimeRange

    color: MarkerColor = Field(default="GREEN")

    kind: MarkerKind = Field(default="note")

    comment: str = Field(default="")


class MediaRefMediaKind(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    MUSIC = "music"
    GENERATED = "generated"


class MediaRef(ContractModel):
    """
    One source, addressed by content hash rather than by path. This is what makes an
    EDL portable: the same plan renders on any machine that has the same footage,
    wherever it happens to live.
    """

    # Local alias used by clips within this EDL.
    media_ref_id: Slug = Field(description="Local alias used by clips within this EDL.")

    # BLAKE3 of the source file, i.e. a MediaRecord primary key. For chaptered footage
    # this is the span ASSEMBLY id; the renderer expands it to the ordered member
    # files.
    media_id: Blake3Hash = Field(
        description="BLAKE3 of the source file, i.e. a MediaRecord primary key. For chaptered footage this is the span ASSEMBLY id; the renderer expands it to the ordered member files.",
    )

    # The full extent of the source, in source time. Exported as
    # ExternalReference.available_range. A clip whose source_range escapes this is
    # invalid and must fail validation before render, not during it.
    available_range: TimeRange = Field(
        description="The full extent of the source, in source time. Exported as ExternalReference.available_range. A clip whose source_range escapes this is invalid and must fail validation before render, not during it.",
    )

    media_kind: MediaRefMediaKind = Field(default="video")

    # True when media_id names a virtual assembly of chaptered files rather than a
    # single file on disk.
    is_span_assembly: bool = Field(
        default=False,
        description="True when media_id names a virtual assembly of chaptered files rather than a single file on disk.",
    )

    expected_frame_rate: float | None = Field(default=None)

    # Human-readable name for the NLE bin. Cosmetic only -- never used for resolution.
    label: str | None = Field(
        default=None,
        description="Human-readable name for the NLE bin. Cosmetic only -- never used for resolution.",
    )


class MixPlanChannels(str, Enum):
    MONO = "mono"
    STEREO = "stereo"


class MixPlanSampleRate(int, Enum):
    V_44100 = 44100
    V_48000 = 48000


class MixPlan(ContractModel):
    loudness_target_lufs: float

    master_gain_db: float = Field(default=0)

    true_peak_ceiling_db: float = Field(default=-1)

    limiter: bool = Field(default=True)

    channels: MixPlanChannels = Field(default="stereo")

    sample_rate: MixPlanSampleRate = Field(default=48000)


class MusicCue(ContractModel):
    """
    Licence and provenance for one piece of music, attached to the clips that place
    it. A cue is NOT a placement: the bed lives on an audio track like every other
    sound, and the cue says what it is and what may legally be done with it.
    """

    cue_id: Slug

    # The source this cue licenses. Must equal the media_ref_id of every clip in
    # clip_ids.
    media_ref_id: Slug = Field(
        description="The source this cue licenses. Must equal the media_ref_id of every clip in clip_ids.",
    )

    # The audio-track clips that place this cue, in timeline order. One entry for a
    # bed that plays once, one per pass for a bed that repeats. Every clip on a track
    # whose role is `music` must be claimed by exactly one cue -- that is how an
    # unlicensed bed becomes impossible rather than merely unlikely.
    clip_ids: list[Slug] = Field(
        description="The audio-track clips that place this cue, in timeline order. One entry for a bed that plays once, one per pass for a bed that repeats. Every clip on a track whose role is `music` must be claimed by exactly one cue -- that is how an unli...",
    )

    # Required, not optional. Music licensing is a Phase 0 decision precisely because
    # an unlicensed track in a shared reel is a legal problem, and the plan is the
    # place it becomes checkable.
    license: MusicLicense = Field(
        description="Required, not optional. Music licensing is a Phase 0 decision precisely because an unlicensed track in a shared reel is a legal problem, and the plan is the place it becomes checkable.",
    )


class MusicLicenseProvider(str, Enum):
    CATALOG_PARTNER = "catalog_partner"
    CREATIVE_COMMONS = "creative_commons"
    PUBLIC_DOMAIN = "public_domain"
    GENERATED_SCORE = "generated_score"
    USER_SUPPLIED = "user_supplied"
    PLATFORM_LIBRARY = "platform_library"


class MusicLicenseLicenseType(str, Enum):
    ROYALTY_FREE = "royalty_free"
    CC_BY = "cc_by"
    CC_BY_SA = "cc_by_sa"
    CC0 = "cc0"
    RIGHTS_MANAGED = "rights_managed"
    PERSONAL_USE_ONLY = "personal_use_only"
    UNKNOWN = "unknown"


class MusicLicenseClearedForItem(str, Enum):
    PRIVATE_PLAYBACK = "private_playback"
    SOCIAL_SHARE = "social_share"
    COMMERCIAL_USE = "commercial_use"
    BROADCAST = "broadcast"


class MusicLicense(ContractModel):
    provider: MusicLicenseProvider

    license_type: MusicLicenseLicenseType

    # Where this cut may legally be published. `user_supplied` music is typically
    # personal_use only, and a share flow must be able to refuse rather than discover
    # the problem after upload.
    cleared_for: list[MusicLicenseClearedForItem] = Field(
        description="Where this cut may legally be published. `user_supplied` music is typically personal_use only, and a share flow must be able to refuse rather than discover the problem after upload.",
    )

    license_id: str | None = Field(default=None)

    track_title: str | None = Field(default=None)

    attribution_required: bool = Field(default=False)

    attribution_text: str | None = Field(default=None)


class OtioExportInfo(ContractModel):
    """
    Bookkeeping for the OTIO round trip. `unmapped_fields` must be empty for an
    export to be called lossless; if it is not, the exporter is telling us exactly
    which part of the contract has outgrown the mapping.
    """

    otio_schema_version: str = Field(default="OTIO_SCHEMA:Timeline.1")

    metadata_namespace: Literal["memory_engine"] = Field(default="memory_engine")

    unmapped_fields: list[str] = Field(default_factory=list)

    # Set by the exporter after re-importing its own output and comparing to the
    # source EDL. The claim of losslessness is tested per export, not assumed.
    round_trip_verified: bool = Field(
        default=False,
        description="Set by the exporter after re-importing its own output and comparing to the source EDL. The claim of losslessness is tested per export, not assumed.",
    )


class ReframeKeyframeInterpolation(str, Enum):
    LINEAR = "linear"
    SMOOTH = "smooth"
    BEZIER = "bezier"
    HOLD = "hold"


class ReframeKeyframe(ContractModel):
    # SOURCE time of the keyframe, so the track stays valid if the clip is later
    # trimmed or retimed.
    time: RationalTime = Field(
        description="SOURCE time of the keyframe, so the track stays valid if the clip is later trimmed or retimed.",
    )

    # Crop window in normalised source coordinates. Its aspect must match the track's
    # target_aspect_ratio; a mismatch is a validation failure because the renderer
    # must not be the one deciding how to reconcile it.
    crop: NormalizedBox = Field(
        description="Crop window in normalised source coordinates. Its aspect must match the track's target_aspect_ratio; a mismatch is a validation failure because the renderer must not be the one deciding how to reconcile it.",
    )

    # How to reach the NEXT keyframe. `hold` produces a snap, which is occasionally
    # what a hard beat wants. Every mode is a stated formula, not a name -- see the
    # $comment.
    interpolation: ReframeKeyframeInterpolation = Field(
        default="smooth",
        description="How to reach the NEXT keyframe. `hold` produces a snap, which is occasionally what a hard beat wants. Every mode is a stated formula, not a name -- see the $comment.",
    )

    # Control points for bezier interpolation, as (x,y) in normalised keyframe-
    # interval space.
    bezier_control: list[Point2D] | None = Field(
        default=None,
        description="Control points for bezier interpolation, as (x,y) in normalised keyframe-interval space.",
    )

    # Tracker confidence at this keyframe. Low-confidence stretches are where the
    # fallback earns its keep.
    confidence: Confidence | None = Field(
        default=None,
        description="Tracker confidence at this keyframe. Low-confidence stretches are where the fallback earns its keep.",
    )


class ReframeSmoothingMethod(str, Enum):
    NONE = "none"
    MOVING_AVERAGE = "moving_average"
    SAVITZKY_GOLAY = "savitzky_golay"
    KALMAN = "kalman"
    SPRING_DAMPER = "spring_damper"


class ReframeSmoothing(ContractModel):
    """
    Constraints that stop a reframe from looking like a nervous camera operator. Raw
    per-frame subject centroids are far too jittery to use directly.
    """

    method: ReframeSmoothingMethod = Field(default="savitzky_golay")

    window_frames: int | None = Field(default=None)

    # Cap on crop travel, in normalised units per second. The difference between a
    # considered pan and a whip.
    max_velocity_per_second: float | None = Field(
        default=None,
        description="Cap on crop travel, in normalised units per second. The difference between a considered pan and a whip.",
    )

    # Subject movement smaller than this does not move the crop at all, which is what
    # keeps a mostly-still subject from causing constant micro-drift.
    deadzone: Unit | None = Field(
        default=None,
        description="Subject movement smaller than this does not move the crop at all, which is what keeps a mostly-still subject from causing constant micro-drift.",
    )


class ReframeTrackFallback(str, Enum):
    CENTER_CROP = "center_crop"
    SALIENCY_CROP = "saliency_crop"
    LETTERBOX = "letterbox"
    HOLD_LAST_KEYFRAME = "hold_last_keyframe"


class ReframeTrack(ContractModel):
    """
    A crop keyframe track that turns landscape footage into a vertical cut with the
    subject held in frame. This is core IP and has no OTIO equivalent, so it round-
    trips through metadata.
    """

    reframe_track_id: Slug

    target_aspect_ratio: AspectRatio

    # Ordered by time, at least one. A single keyframe is a static crop; the
    # interesting case is a moving crop that tracks a subject across the frame.
    keyframes: list[ReframeKeyframe] = Field(
        description="Ordered by time, at least one. A single keyframe is a static crop; the interesting case is a moving crop that tracks a subject across the frame.",
    )

    subject_lock: SubjectLock | None = Field(default=None)

    smoothing: ReframeSmoothing | None = Field(default=None)

    # What to do if subject tracking fails at render time. Never leave this implicit:
    # a reframe that silently falls back to a centre crop can decapitate the subject
    # of the shot.
    fallback: ReframeTrackFallback = Field(
        default="hold_last_keyframe",
        description="What to do if subject tracking fails at render time. Never leave this implicit: a reframe that silently falls back to a centre crop can decapitate the subject of the shot.",
    )


class RenderTargetDestination(str, Enum):
    MASTER = "master"
    INSTAGRAM_REEL = "instagram_reel"
    INSTAGRAM_FEED = "instagram_feed"
    YOUTUBE = "youtube"
    YOUTUBE_SHORTS = "youtube_shorts"
    TIKTOK = "tiktok"
    WHATSAPP_STATUS = "whatsapp_status"
    WEB_PREVIEW = "web_preview"


class RenderTarget(ContractModel):
    """
    What this cut is for. Destination is part of the plan because the reframe, the
    loudness target and the duration are all chosen for it -- an Instagram cut is
    not a YouTube cut at a different bitrate.
    """

    destination: RenderTargetDestination

    resolution: PixelSize

    aspect_ratio: AspectRatio

    # What the planner was asked for. The realised duration is the sum of the timeline
    # and may differ slightly, because landing a cut on a beat matters more than
    # hitting exactly 30.000s.
    target_duration: RationalTime | None = Field(
        default=None,
        description="What the planner was asked for. The realised duration is the sum of the timeline and may differ slightly, because landing a cut on a beat matters more than hitting exactly 30.000s.",
    )

    # Hard ceiling imposed by the platform. Exceeding it is a validation failure, not
    # a warning.
    max_duration: RationalTime | None = Field(
        default=None,
        description="Hard ceiling imposed by the platform. Exceeding it is a validation failure, not a warning.",
    )

    # Integrated loudness the mix is normalised to. -14 for most social platforms, -23
    # for broadcast-style masters.
    loudness_target_lufs: float | None = Field(
        default=None,
        description="Integrated loudness the mix is normalised to. -14 for most social platforms, -23 for broadcast-style masters.",
    )


class StoryArcTemplate(str, Enum):
    HOOK_BUILD_PEAK_BUTTON = "hook_build_peak_button"
    THREE_ACT = "three_act"
    CHRONOLOGICAL = "chronological"
    DAY_IN_THE_LIFE = "day_in_the_life"
    BEFORE_AFTER = "before_after"
    MONTAGE = "montage"
    CUSTOM = "custom"


class StoryArcEnergyCurveItem(ContractModel):
    time: RationalTime

    energy: Unit


class StoryArcSource(str, Enum):
    TEMPLATE = "template"
    TIER2_VLM = "tier2_vlm"
    TIER3_MODEL = "tier3_model"
    USER = "user"


class StoryArc(ContractModel):
    """
    The narrative intention, kept with the plan so that a revision instruction such
    as 'more of her' or 'less drone' re-satisfies the SAME arc instead of re-
    planning from scratch. That persistence is what makes iterative editing feel
    like direction rather than dice-rolling.
    """

    arc_id: Slug

    template: StoryArcTemplate

    acts: list[Act]

    # Who authored the arc. A tier3_model arc must carry its ConsentRef, because
    # producing it meant sending a contact sheet off the device.
    source: StoryArcSource = Field(
        description="Who authored the arc. A tier3_model arc must carry its ConsentRef, because producing it meant sending a contact sheet off the device.",
    )

    title: str | None = Field(default=None)

    # One sentence the arc is trying to say. Written by the frontier model and shown
    # to the user, so a plan can be judged before it is rendered.
    logline: str | None = Field(
        default=None,
        description="One sentence the arc is trying to say. Written by the frontier model and shown to the user, so a plan can be judged before it is rendered.",
    )

    # Target energy over the timeline, as sampled control points. The planner
    # satisfies it mechanically by choosing moments whose features match; it is a
    # specification, not a description.
    energy_curve: list[StoryArcEnergyCurveItem] = Field(
        default_factory=list,
        description="Target energy over the timeline, as sampled control points. The planner satisfies it mechanically by choosing moments whose features match; it is a specification, not a description.",
    )

    model: ModelRef | None = Field(default=None)

    # Which prompt-engine template produced it, so a prompt regression is traceable to
    # the outputs it affected.
    prompt_id: Slug | None = Field(
        default=None,
        description="Which prompt-engine template produced it, so a prompt regression is traceable to the outputs it affected.",
    )

    consent: ConsentRef | None = Field(default=None)

    # The model's stated reasoning, kept for the user-facing 'why this cut'
    # explanation and for eval review.
    rationale: str | None = Field(
        default=None,
        description="The model's stated reasoning, kept for the user-facing 'why this cut' explanation and for eval review.",
    )


class StoryBeat(ContractModel):
    """
    A required narrative element and the clips that satisfy it. An unsatisfied
    required beat is a validation failure -- that is the mechanism by which 'the
    film has an arc' is checked rather than hoped for.
    """

    beat_id: Slug

    description: str

    required: bool

    satisfied_by_clip_ids: list[Slug] = Field(default_factory=list)

    # Moments the planner considered for this beat. Retained so a revision can swap in
    # an alternative without re-running retrieval.
    candidate_moment_ids: list[Blake3Hash] = Field(
        default_factory=list,
        description="Moments the planner considered for this beat. Retained so a revision can swap in an alternative without re-running retrieval.",
    )


class SubjectLockSource(str, Enum):
    SAM2_TRACK = "sam2_track"
    FACE_TRACK = "face_track"
    SALIENCY = "saliency"
    MANUAL = "manual"


class SubjectLockKeepInFrame(str, Enum):
    HEAD = "head"
    FULL_BODY = "full_body"
    BBOX_CENTER = "bbox_center"
    BBOX_FULL = "bbox_full"


class SubjectLock(ContractModel):
    source: SubjectLockSource

    # The tracked entity: a SAM 2 object id, a face track uuid, or null for saliency.
    subject_ref: str | None = Field(
        default=None,
        description="The tracked entity: a SAM 2 object id, a face track uuid, or null for saliency.",
    )

    person_id: Uuid | None = Field(default=None)

    # The part of the subject that must never leave the crop. `head` is the one that
    # matters for people: a technically-centred crop that clips a forehead reads as a
    # mistake.
    keep_in_frame: SubjectLockKeepInFrame = Field(
        default="head",
        description="The part of the subject that must never leave the crop. `head` is the one that matters for people: a technically-centred crop that clips a forehead reads as a mistake.",
    )

    # Fraction of the crop height kept above the subject's head. Composition rule,
    # planned rather than hard-coded in the renderer.
    headroom: Unit | None = Field(
        default=None,
        description="Fraction of the crop height kept above the subject's head. Composition rule, planned rather than hard-coded in the renderer.",
    )


class TimeEffectKind(str, Enum):
    LINEAR_SPEED = "linear_speed"
    FREEZE_FRAME = "freeze_frame"


class TimeEffectAudioHandling(str, Enum):
    MUTE = "mute"
    RESAMPLE = "resample"
    PRESERVE_PITCH = "preserve_pitch"


class TimeEffect(ContractModel):
    """
    Speed change. Restricted to what OTIO models natively, because a speed ramp that
    cannot round-trip is a speed ramp that silently disappears in Resolve.
    `source_range` stays authoritative under an effect and the timeline extent is
    derived from it -- see the $comment, which is the rule the renderer implements.
    """

    kind: TimeEffectKind

    # OTIO LinearTimeWarp.time_scalar: the ratio of media time to timeline time. 0.5
    # is half speed (twice the timeline extent), 2.0 is double. Required for
    # linear_speed, and must divide source_range.duration into a whole number of
    # timeline frames.
    time_scalar: float | None = Field(
        default=None,
        description="OTIO LinearTimeWarp.time_scalar: the ratio of media time to timeline time. 0.5 is half speed (twice the timeline extent), 2.0 is double. Required for linear_speed, and must divide source_range.duration into a whole number of timeline fra...",
    )

    # Source time to hold. Required for freeze_frame, and must equal
    # source_range.start_time -- the frozen frame is the one frame the clip reads.
    freeze_at: RationalTime | None = Field(
        default=None,
        description="Source time to hold. Required for freeze_frame, and must equal source_range.start_time -- the frozen frame is the one frame the clip reads.",
    )

    # How long the frozen frame is held, in TIMELINE time. Required for freeze_frame,
    # forbidden otherwise: it is the clip's timeline extent, and without it a freeze
    # has a start and no end.
    hold_duration: RationalTime | None = Field(
        default=None,
        description="How long the frozen frame is held, in TIMELINE time. Required for freeze_frame, forbidden otherwise: it is the clip's timeline extent, and without it a freeze has a start and no end.",
    )

    # What happens to this clip's audio under a speed change. Almost always `mute` for
    # slow motion, because pitch-shifted ambient sounds broken. `mute` suppresses this
    # clip's ambient bed entirely, whatever AmbientPlan says about it.
    audio_handling: TimeEffectAudioHandling = Field(
        default="mute",
        description="What happens to this clip's audio under a speed change. Almost always `mute` for slow motion, because pitch-shifted ambient sounds broken. `mute` suppresses this clip's ambient bed entirely, whatever AmbientPlan says about it.",
    )


class TrackKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"


class TrackRole(str, Enum):
    PRIMARY = "primary"
    OVERLAY = "overlay"
    TITLES = "titles"
    AMBIENT = "ambient"
    MUSIC = "music"
    VOICEOVER = "voiceover"
    SFX = "sfx"


class Track(ContractModel):
    track_id: Slug

    # Maps to OTIO Track.kind, which recognises exactly "Video" and "Audio".
    kind: TrackKind = Field(
        description="Maps to OTIO Track.kind, which recognises exactly \"Video\" and \"Audio\".",
    )

    # Ordered children. Clips and gaps tile the track; transitions sit between two
    # neighbours and overlap them.
    items: list[Clip | Gap | Transition] = Field(
        description="Ordered children. Clips and gaps tile the track; transitions sit between two neighbours and overlap them.",
    )

    name: str = Field(default="")

    # What the track is for. Purely ours -- it rides in metadata -- but it lets the
    # renderer build the filtergraph without inspecting contents.
    role: TrackRole = Field(
        default="primary",
        description="What the track is for. Purely ours -- it rides in metadata -- but it lets the renderer build the filtergraph without inspecting contents.",
    )

    enabled: bool = Field(default=True)


class TransitionTransitionType(str, Enum):
    DISSOLVE = "dissolve"
    DIP_TO_BLACK = "dip_to_black"
    DIP_TO_WHITE = "dip_to_white"
    WIPE = "wipe"
    PUSH = "push"
    BLUR_DISSOLVE = "blur_dissolve"
    MATCH_CUT = "match_cut"
    CUSTOM = "custom"


class TransitionEasing(str, Enum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"


class Transition(ContractModel):
    """
    An overlap between the two neighbouring items. A hard cut is the absence of one
    of these, never a zero-length instance -- that is OTIO's convention and
    departing from it breaks the round trip.
    """

    item_type: Literal["transition"]

    # `dissolve` maps to OTIO's standard SMPTE_Dissolve. Everything else maps to OTIO
    # "Custom" with the specific kind preserved in metadata, which is how OTIO itself
    # handles non-standard transitions.
    transition_type: TransitionTransitionType = Field(
        description="`dissolve` maps to OTIO's standard SMPTE_Dissolve. Everything else maps to OTIO \"Custom\" with the specific kind preserved in metadata, which is how OTIO itself handles non-standard transitions.",
    )

    # How far the transition extends backwards into the outgoing item.
    in_offset: RationalTime = Field(
        description="How far the transition extends backwards into the outgoing item.",
    )

    # How far it extends forwards into the incoming item.
    out_offset: RationalTime = Field(
        description="How far it extends forwards into the incoming item.",
    )

    transition_id: Slug | None = Field(default=None)

    easing: TransitionEasing = Field(default="linear")

    # Kind-specific settings, e.g. wipe angle. Free-form because the set is open-
    # ended; it rides in OTIO metadata untouched.
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Kind-specific settings, e.g. wipe angle. Free-form because the set is open-ended; it rides in OTIO metadata untouched.",
    )


class VariantInfoStrategy(str, Enum):
    MOMENT_SUBSET = "moment_subset"
    PACING_SEED = "pacing_seed"
    ENERGY_TEMPLATE = "energy_template"
    MUSIC_ALTERNATE = "music_alternate"
    REFRAME_STYLE = "reframe_style"
    DURATION_ALTERNATE = "duration_alternate"


class VariantInfo(ContractModel):
    variant_id: Slug

    variant_index: int

    # What was varied. Variants must differ along a stated axis, so the user's pick is
    # interpretable as a preference rather than as noise.
    strategy: VariantInfoStrategy = Field(
        description="What was varied. Variants must differ along a stated axis, so the user's pick is interpretable as a preference rather than as noise.",
    )

    sibling_edl_ids: list[Blake3Hash] = Field(default_factory=list)

    # One line the variant picker shows: 'faster, more action'.
    description: str | None = Field(
        default=None,
        description="One line the variant picker shows: 'faster, more action'.",
    )


class EDLKind(str, Enum):
    REEL = "reel"
    FILM = "film"
    HIGHLIGHT = "highlight"
    CHAPTER_PREVIEW = "chapter_preview"
    CUSTOM = "custom"


class EDL(ContractModel):
    """
    The deterministic edit plan for one video output. Every creative decision in the
    finished film or reel is expressed here; the renderer executes it and decides
    nothing.
    """

    schema_version: SchemaVersion

    # BLAKE3 over the canonical JSON of this EDL with the volatile fields removed. Two
    # EDLs with the same id render identically.
    edl_id: Blake3Hash = Field(
        description="BLAKE3 over the canonical JSON of this EDL with the volatile fields removed. Two EDLs with the same id render identically.",
    )

    kind: EDLKind

    # Timeline rate in units per second. All video-track RationalTimes are expressed
    # at this rate. Use exact NTSC rationals where the source demands it (30000/1001),
    # never the rounded decimal.
    rate: float = Field(
        description="Timeline rate in units per second. All video-track RationalTimes are expressed at this rate. Use exact NTSC rationals where the source demands it (30000/1001), never the rounded decimal.",
    )

    target: RenderTarget

    # Every source this EDL touches, addressed by content hash. Declared once at the
    # top so a renderer can resolve, verify and pre-open all sources before it starts,
    # and so a missing source is a clean up-front failure rather than a crash at 80%.
    media_refs: list[MediaRef] = Field(
        description="Every source this EDL touches, addressed by content hash. Declared once at the top so a renderer can resolve, verify and pre-open all sources before it starts, and so a missing source is a clean up-front failure rather than a crash at 80%.",
    )

    # Ordered tracks. Index 0 is the bottom video layer, matching OTIO Stack ordering.
    tracks: list[Track] = Field(
        description="Ordered tracks. Index 0 is the bottom video layer, matching OTIO Stack ordering.",
    )

    determinism: Determinism

    name: str | None = Field(default=None)

    # Timeline zero, exported as OTIO Timeline.global_start_time. Normally 0; non-zero
    # when the output must carry a broadcast start timecode such as 01:00:00:00.
    global_start_time: RationalTime | None = Field(
        default=None,
        description="Timeline zero, exported as OTIO Timeline.global_start_time. Normally 0; non-zero when the output must carry a broadcast start timecode such as 01:00:00:00.",
    )

    # Crop/reframe keyframe tracks, referenced by clips. Held at EDL level rather than
    # inline on the clip so one subject-lock track can drive several clips from the
    # same source shot.
    reframe_tracks: list[ReframeTrack] = Field(
        default_factory=list,
        description="Crop/reframe keyframe tracks, referenced by clips. Held at EDL level rather than inline on the clip so one subject-lock track can drive several clips from the same source shot.",
    )

    audio_plan: AudioPlan | None = Field(default=None)

    beat_grid: BeatGrid | None = Field(default=None)

    story_arc: StoryArc | None = Field(default=None)

    color_pipeline: ColorPipeline | None = Field(default=None)

    # Present when this EDL is one of several alternatives offered to the user. The
    # reel planner emits 3-5; whichever the user picks becomes a PrefEvent, and the
    # losers are training signal too.
    variant: VariantInfo | None = Field(
        default=None,
        description="Present when this EDL is one of several alternatives offered to the user. The reel planner emits 3-5; whichever the user picks becomes a PrefEvent, and the losers are training signal too.",
    )

    validation: EdlValidation | None = Field(default=None)

    otio: OtioExportInfo | None = Field(default=None)


class ClusterMembershipMethod(str, Enum):
    HDBSCAN_COSINE = "hdbscan_cosine"
    AGGLOMERATIVE_COSINE = "agglomerative_cosine"
    USER_GROUPED = "user_grouped"
    SINGLETON = "singleton"


class ClusterMembership(ContractModel):
    """
    Unsupervised grouping over embedding distance (HDBSCAN over cosine). A cluster
    is a hypothesis, not an identity: it is allowed to be wrong, which is exactly
    why it is stored separately from `identity`.
    """

    cluster_id: Uuid

    method: ClusterMembershipMethod

    # HDBSCAN membership probability. Low values sit near the decision boundary and
    # are precisely the ones the active-learning loop should ask a human about first
    # -- ten well-chosen taps fix a thousand photos.
    membership_strength: Unit | None = Field(
        default=None,
        description="HDBSCAN membership probability. Low values sit near the decision boundary and are precisely the ones the active-learning loop should ask a human about first -- ten well-chosen taps fix a thousand photos.",
    )

    # HDBSCAN noise point: too far from any cluster. Never surfaces in automated
    # output.
    is_noise: bool = Field(
        default=False,
        description="HDBSCAN noise point: too far from any cluster. Never surfaces in automated output.",
    )

    distance_to_centroid: float | None = Field(default=None)

    # Which clustering pass produced this. Re-clustering a growing library reshuffles
    # cluster ids; pinning the run makes the reshuffle auditable instead of
    # mysterious.
    clustering_run_id: Slug | None = Field(
        default=None,
        description="Which clustering pass produced this. Re-clustering a growing library reshuffles cluster ids; pinning the run makes the reshuffle auditable instead of mysterious.",
    )


class DetectionDetectedOn(str, Enum):
    THUMBNAIL_512 = "thumbnail_512"
    PREVIEW_2048 = "preview_2048"
    VIDEO_PROXY_480P = "video_proxy_480p"
    ORIGINAL = "original"


class Detection(ContractModel):
    # Normalised against the ORIENTED frame, so the same box is valid on the 512px
    # thumbnail the detector saw and on the 6000px original the renderer will crop.
    bbox: NormalizedBox = Field(
        description="Normalised against the ORIENTED frame, so the same box is valid on the 512px thumbnail the detector saw and on the 6000px original the renderer will crop.",
    )

    detection_score: Confidence

    detector: ModelRef

    # Which rendition the detector ran against. Small faces missed on a thumbnail are
    # re-detected at full resolution on demand; recording this makes 'have we already
    # looked properly' answerable.
    detected_on: DetectionDetectedOn = Field(
        default="thumbnail_512",
        description="Which rendition the detector ran against. Small faces missed on a thumbnail are re-detected at full resolution on demand; recording this makes 'have we already looked properly' answerable.",
    )

    # Fraction of the frame the box covers. The single best predictor of whether an
    # embedding will be trustworthy, and a hard input to the automated-output
    # threshold.
    face_area_ratio: Unit | None = Field(
        default=None,
        description="Fraction of the frame the box covers. The single best predictor of whether an embedding will be trustworthy, and a hard input to the automated-output threshold.",
    )


class FaceAttributes(ContractModel):
    """
    Everything that decides whether this is a GOOD photo of this person, as opposed
    to whether it is this person. Feeds face-quality scoring, album hero selection,
    and the 'is anyone blinking' check.
    """

    # Head pose. Beyond about +/-45 degrees yaw, recognition confidence degrades
    # sharply and the automated-output threshold should tighten.
    yaw_deg: float | None = Field(
        default=None,
        description="Head pose. Beyond about +/-45 degrees yaw, recognition confidence degrades sharply and the automated-output threshold should tighten.",
    )

    pitch_deg: float | None = Field(default=None)

    roll_deg: float | None = Field(default=None)

    # Probability both eyes are open. The blink check that saves an album spread.
    eyes_open: Confidence | None = Field(
        default=None,
        description="Probability both eyes are open. The blink check that saves an album spread.",
    )

    smile: Confidence | None = Field(default=None)

    mouth_open: Confidence | None = Field(default=None)

    gaze_on_camera: Confidence | None = Field(default=None)

    sharpness: Unit | None = Field(default=None)

    # How much of the face is hidden by a hand, hair, mask or another person.
    occlusion: Unit | None = Field(
        default=None,
        description="How much of the face is hidden by a hand, hair, mask or another person.",
    )

    wearing_sunglasses: Confidence | None = Field(default=None)

    wearing_mask: Confidence | None = Field(default=None)

    # Fused face quality, the value album selection actually sorts on.
    quality: Score | None = Field(
        default=None,
        description="Fused face quality, the value album selection actually sorts on.",
    )


class FaceTrack(ContractModel):
    track_id: Uuid

    # Source-time span the track covers.
    track_range: TimeRange = Field(description="Source-time span the track covers.")

    position_in_track: int | None = Field(default=None)

    track_length: int | None = Field(default=None)

    # The single best frame of this track, chosen by face quality. Identity is decided
    # once per track from the representative, not voted per frame -- 120 correlated
    # votes are not 120 pieces of evidence.
    is_track_representative: bool = Field(
        default=False,
        description="The single best frame of this track, chosen by face quality. Identity is decided once per track from the representative, not voted per frame -- 120 correlated votes are not 120 pieces of evidence.",
    )


class IdentityAssignment(str, Enum):
    UNASSIGNED = "unassigned"
    USER_CONFIRMED = "user_confirmed"
    USER_REJECTED = "user_rejected"
    AUTO_HIGH_CONFIDENCE = "auto_high_confidence"
    AUTO_BELOW_THRESHOLD = "auto_below_threshold"
    REVIEW_QUEUED = "review_queued"
    AMBIGUOUS_MULTIPLE_CANDIDATES = "ambiguous_multiple_candidates"


class IdentityThresholdProfile(str, Enum):
    AUTOMATED_OUTPUT = "automated_output"
    REVIEW_QUEUE = "review_queue"
    SEARCH_ONLY = "search_only"


class IdentityCandidatesItem(ContractModel):
    person_id: Uuid

    confidence: Confidence


class IdentityReviewReason(str, Enum):
    BELOW_THRESHOLD = "below_threshold"
    NEAR_BOUNDARY = "near_boundary"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    NEW_CLUSTER = "new_cluster"
    USER_REPORTED_ERROR = "user_reported_error"
    LOW_FACE_QUALITY = "low_face_quality"
    EXTREME_POSE = "extreme_pose"


class IdentityDecidedBy(str, Enum):
    MODEL = "model"
    USER = "user"
    RULE = "rule"


class Identity(ContractModel):
    """
    Who we say this is, how sure we are, and whether that is sure enough to act on
    unattended.
    """

    # How the person_id was arrived at. `auto_below_threshold` is a real, common
    # state: the model has a guess it is not allowed to use, which is the entire point
    # of precision-first.
    assignment: IdentityAssignment = Field(
        description="How the person_id was arrived at. `auto_below_threshold` is a real, common state: the model has a guess it is not allowed to use, which is the entire point of precision-first.",
    )

    # THE GATE. Album, film and reel selection may only treat this face as a known
    # person when this is true. Invariant, enforced in tests: true requires assignment
    # to be user_confirmed, or auto_high_confidence with confidence >= threshold_used.
    # Every other state is false.
    eligible_for_automated_output: bool = Field(
        description="THE GATE. Album, film and reel selection may only treat this face as a known person when this is true. Invariant, enforced in tests: true requires assignment to be user_confirmed, or auto_high_confidence with confidence >= threshold_used...",
    )

    # Null until an assignment exists. A person is a user-facing entity created by
    # labeling, never by clustering alone.
    person_id: Uuid | None = Field(
        default=None,
        description="Null until an assignment exists. A person is a user-facing entity created by labeling, never by clustering alone.",
    )

    # Calibrated similarity-to-person confidence. Null when unassigned.
    confidence: Confidence | None = Field(
        default=None,
        description="Calibrated similarity-to-person confidence. Null when unassigned.",
    )

    # Which operating point was applied. `automated_output` is the strict one tuned
    # for >=99% precision (build plan section 7); `search_only` is permissive because
    # a wrong hit in a search result is a shrug, not a catastrophe.
    threshold_profile: IdentityThresholdProfile = Field(
        default="automated_output",
        description="Which operating point was applied. `automated_output` is the strict one tuned for >=99% precision (build plan section 7); `search_only` is permissive because a wrong hit in a search result is a shrug, not a catastrophe.",
    )

    # The actual numeric threshold applied, stored so that retuning the operating
    # point is a replayable decision rather than a silent behaviour change.
    threshold_used: Confidence | None = Field(
        default=None,
        description="The actual numeric threshold applied, stored so that retuning the operating point is a replayable decision rather than a silent behaviour change.",
    )

    # Runner-up people considered. Populated when assignment is
    # ambiguous_multiple_candidates, which is the twins-and-siblings case that a
    # single best-match number hides.
    candidates: list[IdentityCandidatesItem] = Field(
        default_factory=list,
        description="Runner-up people considered. Populated when assignment is ambiguous_multiple_candidates, which is the twins-and-siblings case that a single best-match number hides.",
    )

    review_reason: IdentityReviewReason | None = Field(default=None)

    decided_by: IdentityDecidedBy | None = Field(default=None)

    decided_at: Timestamp | None = Field(default=None)


class LandmarksScheme(str, Enum):
    INSIGHTFACE_5 = "insightface_5"
    INSIGHTFACE_106 = "insightface_106"
    MEDIAPIPE_468 = "mediapipe_468"
    YUNET_5 = "yunet_5"


class Landmarks(ContractModel):
    # Point count and ordering convention. Consumers must switch on this rather than
    # assuming an index layout. yunet_5 and insightface_5 are both five points and are
    # NOT interchangeable: feeding one to an alignment template built for the other
    # produces a plausible warp and a wrong embedding, which is the worst failure mode
    # in this system because nothing downstream can detect it.
    scheme: LandmarksScheme = Field(
        description="Point count and ordering convention. Consumers must switch on this rather than assuming an index layout. yunet_5 and insightface_5 are both five points and are NOT interchangeable: feeding one to an alignment template built for the other...",
    )

    points: list[Point2D]

    score: Confidence | None = Field(default=None)


class SensitiveFlagsMinorStatus(str, Enum):
    UNKNOWN = "unknown"
    ESTIMATED_MINOR = "estimated_minor"
    CONFIRMED_MINOR = "confirmed_minor"
    CONFIRMED_ADULT = "confirmed_adult"


class SensitiveFlags(ContractModel):
    """
    Child-face labeling sits behind separate explicit consent (build plan section
    8). Modelled here rather than on the person so that the gate is evaluated at the
    point of use.
    """

    # `unknown` is the default and is NOT treated as adult. Estimated age is a signal
    # for asking the user, never a licence to proceed.
    minor_status: SensitiveFlagsMinorStatus = Field(
        default="unknown",
        description="`unknown` is the default and is NOT treated as adult. Estimated age is a signal for asking the user, never a licence to proceed.",
    )

    # Required before a confirmed_minor face may be labeled with a person identity.
    # Absent consent, the face is still detected and counted, but never named. Must be
    # scoped to minor_face_labeling specifically -- a consent granted for cloud
    # rendering does not authorise naming a child.
    labeling_consent: ConsentRef | None = Field(
        default=None,
        description="Required before a confirmed_minor face may be labeled with a person identity. Absent consent, the face is still detected and counted, but never named. Must be scoped to minor_face_labeling specifically -- a consent granted for cloud rend...",
    )

    excluded_from_sharing: bool = Field(default=False)


class FaceRecord(ContractModel):
    """
    One detected face in one frame: where it is, what it looks like as an embedding,
    which cluster it fell into, and -- separately and much more cautiously -- which
    person we are willing to say it is.
    """

    schema_version: SchemaVersion

    # BLAKE3 over (media_id, frame_time, quantised bbox, detector model_id + version).
    # Content-addressed, so re-running the same detector on the same frame produces
    # the same id and re-detection is idempotent. Changing detector version
    # deliberately produces new ids rather than silently mutating old ones.
    #
    # CANONICAL ENCODING (issue #34), because 'BLAKE3 over the tuple' does not
    # determine the bytes and every writer picked its own. The hashed byte string is
    # exactly:
    #
    # face_id = BLAKE3( utf8( DOMAIN US media_id US TIME US BBOX US MODEL_ID US
    # VERSION ) )
    #
    # where US is U+001F INFORMATION SEPARATOR ONE, written once between adjacent
    # fields and nowhere else, and the six fields are:
    #
    # DOMAIN -- the literal ASCII string 'face:v1'. This versions THIS ENCODING, not
    # the detector. Changing the encoding means bumping it, so a re-encoding produces
    # new ids on purpose rather than colliding with old ones by accident.
    #
    # media_id -- the 64 lowercase hex characters, verbatim.
    #
    # TIME -- the EMPTY STRING when frame_time is null, i.e. for a still. Otherwise
    # `<value>/<rate>`, each number rendered in RFC 8785 / ECMAScript Number::toString
    # form, which is the same numeric rule edl_id already uses. So 1001 and 1001.0
    # both render as `1001`, and a rate of 30000/1001 renders as `29.97002997002997`.
    # THIS IS THE FIELD THAT BREAKS FIRST ACROSS LANGUAGES: Python's repr writes `1.0`
    # where JavaScript writes `1`, and the same frame then gets two ids. A number that
    # cannot be rendered without an exponent is REJECTED rather than written, because
    # exponent formatting is where the two languages stop agreeing and no real frame
    # rate needs one. The still case cannot be confused with the video case: a
    # rendered time always contains a `/`, and the empty string never does.
    #
    # BBOX -- `<qx>,<qy>,<qw>,<qh>` where q(v) = round_half_away_from_zero(v * 10000),
    # rendered as a base-10 integer with no padding and no sign (every component is
    # non-negative by schema, so half-away-from-zero and half-up coincide). NOT
    # banker's rounding: Python's round() sends 3002.5 to 3002 while JavaScript's
    # Math.round and Rust's f64::round both send it to 3003, and 8855 of the 10000
    # half-quantum positions in [0,1] are exactly representable as doubles, so this is
    # reachable rather than theoretical. The quantum is 1e-4 of the frame -- 0.6px on
    # a 6000px original, finer than any detector's own precision and coarse enough
    # that the last-bit disagreement between two execution providers cannot turn one
    # face into two. `rotation_deg` does NOT participate, which is why Detection pins
    # it to 0.
    #
    # MODEL_ID, VERSION -- detection.detector.model_id and .version, verbatim.
    # model_id is a Slug and cannot contain the separator; ModelRef.version is
    # pattern-constrained to exclude control characters for exactly this reason, so
    # the join is injective and needs no length prefix.
    #
    # DELIBERATELY NOT IN THE TUPLE: weights_blake3, config_blake3, runtime,
    # precision, detected_on, detection_score, landmarks and embedding. All of them
    # are still RECORDED, on the detector ModelRef and in model_runs, so provenance is
    # not lost -- but none of them may move the id. weights_blake3 is nullable in
    # development mode, so including it would rename every face the moment the same
    # detection is re-recorded against a pinned registry: duplicated rows where
    # deduplication was the point. config_blake3 moves when a score threshold moves,
    # which changes WHICH faces are found rather than the identity of one that was
    # found -- and a config change that really does move a box further than the
    # quantum already changes BBOX. `version` is the deliberate, human-controlled
    # switch for 'issue new ids'.
    #
    # contracts/tests recomputes this for every face fixture and for
    # contracts/vectors/face-id.json, in Python and again in TypeScript against the
    # generated bindings. An identity that is asserted rather than computed is how
    # issue #26's invented span_id happened, and this is the same failure one schema
    # over.
    face_id: Blake3Hash = Field(
        description="BLAKE3 over (media_id, frame_time, quantised bbox, detector model_id + version). Content-addressed, so re-running the same detector on the same frame produces the same id and re-detection is idempotent. Changing detector version delibera...",
    )

    # The MediaRecord this face was found in. For spanned video this is the assembly
    # record, so a face track can cross a chapter boundary.
    media_id: Blake3Hash = Field(
        description="The MediaRecord this face was found in. For spanned video this is the assembly record, so a face track can cross a chapter boundary.",
    )

    detection: Detection

    identity: Identity

    sensitive: SensitiveFlags

    # Position within the video, in SOURCE time (already mapped back through the proxy
    # frame index). Null for stills.
    frame_time: RationalTime | None = Field(
        default=None,
        description="Position within the video, in SOURCE time (already mapped back through the proxy frame index). Null for stills.",
    )

    # Face track membership for video. One person walking through a 4-second shot is
    # ~120 FaceRecords sharing a track_id; the ranking and moment layers work on
    # tracks, not frames.
    track: FaceTrack | None = Field(
        default=None,
        description="Face track membership for video. One person walking through a 4-second shot is ~120 FaceRecords sharing a track_id; the ranking and moment layers work on tracks, not frames.",
    )

    landmarks: Landmarks | None = Field(default=None)

    # Recognition embedding. Null when the face was detected but was too small, too
    # blurred or too occluded to embed reliably -- an unembeddable face is still worth
    # recording, because it counts toward 'how many people are in this photo'.
    embedding: VectorRef | None = Field(
        default=None,
        description="Recognition embedding. Null when the face was detected but was too small, too blurred or too occluded to embed reliably -- an unembeddable face is still worth recording, because it counts toward 'how many people are in this photo'.",
    )

    attributes: FaceAttributes | None = Field(default=None)

    cluster: ClusterMembership | None = Field(default=None)

    model_runs: list[ModelRun] = Field(default_factory=list)

    created_at: Timestamp | None = Field(default=None)

    updated_at: Timestamp | None = Field(default=None)


class Checkpoint(ContractModel):
    """
    Durable resumption state. The cursor is opaque on purpose: the contract promises
    to persist and return it, and declines to speculate about what a directory
    walker or a video encoder needs to remember.
    """

    # False for jobs that must restart from zero -- a short atomic render, say.
    # Stating it explicitly stops a scheduler from assuming either way.
    resumable: bool = Field(
        description="False for jobs that must restart from zero -- a short atomic render, say. Stating it explicitly stops a scheduler from assuming either way.",
    )

    # Worker-owned opaque state. Null means 'resumable but not yet started'.
    cursor: str | None = Field(
        description="Worker-owned opaque state. Null means 'resumable but not yet started'.",
    )

    # Bumped when a worker changes its cursor format. A cursor from an older version
    # is discarded and the job restarts, rather than being handed to code that will
    # misparse it.
    checkpoint_version: int = Field(
        default=0,
        description="Bumped when a worker changes its cursor format. A cursor from an older version is discarded and the job restarts, rather than being handed to code that will misparse it.",
    )

    updated_at: Timestamp | None = Field(default=None)

    # Inputs already finished. On resume these are skipped, which is what stops a
    # resumed 3TB scan from rehashing the first 2TB.
    completed_input_ids: list[Blake3Hash] = Field(
        default_factory=list,
        description="Inputs already finished. On resume these are skipped, which is what stops a resumed 3TB scan from rehashing the first 2TB.",
    )

    # Outputs written before the interruption. Recorded so they are neither orphaned
    # nor recreated.
    partial_output_ids: list[Blake3Hash] = Field(
        default_factory=list,
        description="Outputs written before the interruption. Recorded so they are neither orphaned nor recreated.",
    )


class EgressDeclarationDestination(str, Enum):
    TIER3_INFERENCE = "tier3_inference"
    CLOUD_RENDER = "cloud_render"
    BILLING = "billing"
    SYNC = "sync"
    SHARE = "share"
    PRINT_VENDOR = "print_vendor"
    TELEMETRY = "telemetry"


class EgressDeclarationPayloadKind(str, Enum):
    CONTACT_SHEET = "contact_sheet"
    THUMBNAIL = "thumbnail"
    FEATURE_VECTOR = "feature_vector"
    METADATA_ONLY = "metadata_only"
    STRUCTURED_DECISION = "structured_decision"
    ORIGINAL_MEDIA = "original_media"
    RENDERED_OUTPUT = "rendered_output"


class EgressDeclaration(ContractModel):
    """
    Whether this job talks to the network, and on whose authority. Declared on every
    job including the overwhelming majority that declare `false`, because an absent
    declaration and a negative one must not look the same.
    """

    requires_egress: bool

    consent: ConsentRef | None = Field(default=None)

    destination: EgressDeclarationDestination | None = Field(default=None)

    # What actually leaves the device. `contact_sheet` and `thumbnail` are the only
    # image-bearing values permitted for Tier 3, and `original_media` requires its own
    # explicit consent scope -- originals never leave without one.
    payload_kind: EgressDeclarationPayloadKind | None = Field(
        default=None,
        description="What actually leaves the device. `contact_sheet` and `thumbnail` are the only image-bearing values permitted for Tier 3, and `original_media` requires its own explicit consent scope -- originals never leave without one.",
    )

    estimated_bytes: int | None = Field(default=None)


class JobErrorCode(str, Enum):
    FILE_NOT_FOUND = "file_not_found"
    FILE_UNREADABLE = "file_unreadable"
    FILE_CORRUPT = "file_corrupt"
    ZERO_BYTE_FILE = "zero_byte_file"
    UNSUPPORTED_CODEC = "unsupported_codec"
    UNSUPPORTED_FORMAT = "unsupported_format"
    SYMLINK_LOOP = "symlink_loop"
    PERMISSION_DENIED = "permission_denied"
    DISK_FULL = "disk_full"
    OUT_OF_MEMORY = "out_of_memory"
    GPU_UNAVAILABLE = "gpu_unavailable"
    MODEL_LOAD_FAILED = "model_load_failed"
    MODEL_INFERENCE_FAILED = "model_inference_failed"
    TIMEOUT = "timeout"
    CANCELLED_BY_USER = "cancelled_by_user"
    DEPENDENCY_FAILED = "dependency_failed"
    CONSENT_MISSING = "consent_missing"
    CONSENT_REVOKED = "consent_revoked"
    NETWORK_UNAVAILABLE = "network_unavailable"
    RATE_LIMITED = "rate_limited"
    VALIDATION_FAILED = "validation_failed"
    INTERNAL_ERROR = "internal_error"


class JobError(ContractModel):
    code: JobErrorCode

    # Already redacted: no paths, no filenames, no EXIF. Crash reporting forwards this
    # verbatim, so redaction happens at write time rather than at send time.
    message: str = Field(
        description="Already redacted: no paths, no filenames, no EXIF. Crash reporting forwards this verbatim, so redaction happens at write time rather than at send time.",
    )

    retryable: bool

    attempt: int = Field(default=0)

    occurred_at: Timestamp | None = Field(default=None)

    # Which specific input broke, so a 300k-file scan reports one bad file rather than
    # failing wholesale.
    failed_input_id: Blake3Hash | None = Field(
        default=None,
        description="Which specific input broke, so a 300k-file scan reports one bad file rather than failing wholesale.",
    )


class JobInputs(ContractModel):
    """
    Everything the job reads, addressed by hash. Content addressing is what makes
    the whole pipeline idempotent: identical inputs cannot produce a different job.
    """

    # Sorted before hashing into job_id, so input order never changes job identity.
    media_ids: list[Blake3Hash] = Field(
        default_factory=list,
        description="Sorted before hashing into job_id, so input order never changes job identity.",
    )

    moment_ids: list[Blake3Hash] = Field(default_factory=list)

    face_ids: list[Blake3Hash] = Field(default_factory=list)

    edl_id: Blake3Hash | None = Field(default=None)

    album_id: Blake3Hash | None = Field(default=None)

    # Only for scan_source, which by definition starts before anything has a hash.
    # Every other job type addresses content, never location.
    source_paths: list[str] = Field(
        default_factory=list,
        description="Only for scan_source, which by definition starts before anything has a hash. Every other job type addresses content, never location.",
    )

    # BLAKE3 over the canonical form of `source_paths`: each path resolved to
    # absolute, symlinks followed, trailing separators stripped, NFC-normalised, then
    # sorted and joined with a NUL separator. Required for scan_source and the only
    # thing distinguishing two scans of different folders, since neither has content
    # hashes yet. Canonicalisation matters as much as the digest -- '/Volumes/Archive'
    # and '/Volumes/Archive/' must not be two jobs, or a re-scan re-walks a whole
    # drive.
    source_locator_digest: Blake3Hash | None = Field(
        default=None,
        description="BLAKE3 over the canonical form of `source_paths`: each path resolved to absolute, symlinks followed, trailing separators stripped, NFC-normalised, then sorted and joined with a NUL separator. Required for scan_source and the only thing d...",
    )

    # The job that spawned this one. A scan spawns a hash job per file; the tree is
    # what makes 'cancel this import' a well-defined operation.
    parent_job_id: Blake3Hash | None = Field(
        default=None,
        description="The job that spawned this one. A scan spawns a hash job per file; the tree is what makes 'cancel this import' a well-defined operation.",
    )

    # Jobs that must reach completed before this one may start.
    depends_on_job_ids: list[Blake3Hash] = Field(
        default_factory=list,
        description="Jobs that must reach completed before this one may start.",
    )

    # Model pins. Part of the params digest, so re-running with a swapped model is a
    # different job and the old result is never silently reused.
    models: list[ModelRef] = Field(
        default_factory=list,
        description="Model pins. Part of the params digest, so re-running with a swapped model is a different job and the old result is never silently reused.",
    )


class JobOutputKind(str, Enum):
    MEDIA_RECORD = "media_record"
    FACE_RECORD = "face_record"
    MOMENT_RECORD = "moment_record"
    EDL = "edl"
    ALBUM_SPEC = "album_spec"
    PROXY = "proxy"
    RENDERED_VIDEO = "rendered_video"
    RENDERED_PDF = "rendered_pdf"
    OTIO_FILE = "otio_file"
    VECTOR_INDEX_ENTRY = "vector_index_entry"
    EVAL_REPORT = "eval_report"
    PREF_EVENT = "pref_event"


class JobOutput(ContractModel):
    kind: JobOutputKind

    # Content hash of the produced artifact, so an output can be verified rather than
    # trusted.
    id: Blake3Hash = Field(
        description="Content hash of the produced artifact, so an output can be verified rather than trusted.",
    )

    path: str | None = Field(default=None)

    byte_size: int | None = Field(default=None)

    produced_at: Timestamp | None = Field(default=None)


class JobRequirementsCompute(str, Enum):
    CPU = "cpu"
    GPU = "gpu"
    NEURAL_ENGINE = "neural_engine"
    ANY = "any"


class JobRequirements(ContractModel):
    """
    What the job needs to run. The scheduler matches these against the machine
    rather than discovering mid-render that there is no GPU.
    """

    compute: JobRequirementsCompute = Field(default="any")

    min_vram_mb: int | None = Field(default=None)

    min_ram_mb: int | None = Field(default=None)

    min_disk_mb: int | None = Field(default=None)

    # Proxy generation must saturate disk I/O rather than CPU, which is only possible
    # with VideoToolbox/NVDEC/QSV. A proxy job that would fall back to software decode
    # should queue rather than crawl.
    hardware_decode: bool = Field(
        default=False,
        description="Proxy generation must saturate disk I/O rather than CPU, which is only possible with VideoToolbox/NVDEC/QSV. A proxy job that would fall back to software decode should queue rather than crawl.",
    )

    # True only for proxy generation and final render. Everything else works on
    # proxies -- sources are opened exactly twice in a file's life.
    requires_source_file: bool = Field(
        default=False,
        description="True only for proxy generation and final render. Everything else works on proxies -- sources are opened exactly twice in a file's life.",
    )

    estimated_duration_ms: float | None = Field(default=None)


class JobStateStatus(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    QUARANTINED = "quarantined"


class JobState(ContractModel):
    # `paused` is a deliberate user action; `pending` after a crash is what a killed
    # `running` job becomes on relaunch. Distinguishing them is what makes resumption
    # safe -- a paused job must not restart itself.
    status: JobStateStatus = Field(
        description="`paused` is a deliberate user action; `pending` after a crash is what a killed `running` job becomes on relaunch. Distinguishing them is what makes resumption safe -- a paused job must not restart itself.",
    )

    attempts: int

    worker_id: str | None = Field(default=None)

    started_at: Timestamp | None = Field(default=None)

    # Last sign of life. A running job whose heartbeat has gone stale was killed with
    # the process and is safe to reclaim -- without it, a crashed job is
    # indistinguishable from a slow one and blocks its queue forever.
    heartbeat_at: Timestamp | None = Field(
        default=None,
        description="Last sign of life. A running job whose heartbeat has gone stale was killed with the process and is safe to reclaim -- without it, a crashed job is indistinguishable from a slow one and blocks its queue forever.",
    )

    finished_at: Timestamp | None = Field(default=None)

    progress: Progress | None = Field(default=None)


class JournalEntriesItemAction(str, Enum):
    DELETE_FILE = "delete_file"
    OVERWRITE_FILE = "overwrite_file"
    MOVE_FILE = "move_file"
    PRUNE_PROXY = "prune_proxy"
    NETWORK_SEND = "network_send"
    NETWORK_RECEIVE = "network_receive"
    CONSENT_RECORDED = "consent_recorded"
    CONSENT_REVOKED = "consent_revoked"
    MODEL_SWAPPED = "model_swapped"
    INDEX_REBUILT = "index_rebuilt"


class JournalEntriesItem(ContractModel):
    action: JournalEntriesItemAction

    at: Timestamp

    target_id: Blake3Hash | None = Field(default=None)

    reversible: bool = Field(default=False)

    detail: str = Field(default="")


class Journal(ContractModel):
    """
    Record of every destructive or externally-visible action the job took. 'No
    silent data loss' means anything irreversible is written down before it happens,
    not after it succeeds.
    """

    entries: list[JournalEntriesItem] = Field(default_factory=list)


class ProgressUnit(str, Enum):
    FILES = "files"
    BYTES = "bytes"
    FRAMES = "frames"
    SECONDS = "seconds"
    IMAGES = "images"
    MOMENTS = "moments"
    PAGES = "pages"
    ITEMS = "items"


class Progress(ContractModel):
    """
    Progress in real units, not a synthetic percentage. '12,400 of 318,000 files'
    survives a restart honestly; a percentage that jumps backwards does not.
    """

    units_done: float

    unit: ProgressUnit

    # Null while still being discovered -- a scan does not know how many files exist
    # until it has walked them, and claiming otherwise produces a progress bar that
    # lies.
    units_total: float | None = Field(
        default=None,
        description="Null while still being discovered -- a scan does not know how many files exist until it has walked them, and claiming otherwise produces a progress bar that lies.",
    )

    bytes_processed: int | None = Field(default=None)

    message: str | None = Field(default=None)


class RetryPolicyBackoff(str, Enum):
    NONE = "none"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


class RetryPolicy(ContractModel):
    max_attempts: int = Field(default=3)

    backoff: RetryPolicyBackoff = Field(default="exponential")

    initial_delay_ms: float = Field(default=1000)

    max_delay_ms: float = Field(default=60000)

    # Move to quarantined rather than failed once attempts are exhausted, so a hostile
    # file is never retried automatically again but is still visible in the
    # diagnostics view. Nothing is dropped silently.
    quarantine_after_max: bool = Field(
        default=True,
        description="Move to quarantined rather than failed once attempts are exhausted, so a hostile file is never retried automatically again but is still visible in the diagnostics view. Nothing is dropped silently.",
    )


class JobSpecJobType(str, Enum):
    SCAN_SOURCE = "scan_source"
    HASH_FILE = "hash_file"
    EXTRACT_METADATA = "extract_metadata"
    GENERATE_THUMBNAIL = "generate_thumbnail"
    GENERATE_VIDEO_PROXY = "generate_video_proxy"
    PERCEPTUAL_HASH = "perceptual_hash"
    ANALYZE_IMAGE = "analyze_image"
    ANALYZE_VIDEO = "analyze_video"
    DETECT_FACES = "detect_faces"
    CLUSTER_FACES = "cluster_faces"
    TRANSCRIBE_AUDIO = "transcribe_audio"
    DETECT_SHOTS = "detect_shots"
    SCORE_MOMENTS = "score_moments"
    RANK_MEDIA = "rank_media"
    DEDUPE_CLUSTER = "dedupe_cluster"
    CLUSTER_EVENTS = "cluster_events"
    PLAN_REEL = "plan_reel"
    PLAN_FILM = "plan_film"
    PLAN_ALBUM = "plan_album"
    TIER3_REQUEST = "tier3_request"
    ENHANCE_IMAGE = "enhance_image"
    RENDER_VIDEO = "render_video"
    RENDER_PRINT = "render_print"
    EXPORT_OTIO = "export_otio"
    EVAL_RUN = "eval_run"
    REINDEX_VECTORS = "reindex_vectors"
    CONSENT_EXPORT = "consent_export"


class JobSpec(ContractModel):
    """
    Any unit of work in the system: what to do, to which inputs, with which
    parameters, how far it got, and how to pick it up again.
    """

    schema_version: SchemaVersion

    # BLAKE3 over (job_type, sorted input ids, source_locator_digest or the empty
    # string, params_digest, scope). Doubles as the idempotency key -- there is
    # deliberately no second field for that, because two sources of truth for identity
    # is how duplicate work gets in.
    #
    # CANONICAL ENCODING, for the same reason face_id and span_id have one: a named
    # tuple is not a byte string, and two workers that separate the fields differently
    # compute different ids for identical work -- which means the second one redoes
    # it, or worse, a genuinely different job collides with a completed one and is
    # skipped. The hashed bytes are:
    #
    # job_id = BLAKE3( utf8( job_type US IDS US LOCATOR US params_digest US scope ) )
    #
    # where US is U+001F INFORMATION SEPARATOR ONE. IDS is the media ids followed by
    # the moment ids, sorted as one list and joined with a single comma; empty when
    # there are none. LOCATOR is source_locator_digest, or the EMPTY STRING when it is
    # null -- absent and empty must render the same way, or a job with no locator gets
    # two ids. scope renders as the empty string when null.
    #
    # The fields are all fixed-alphabet (hex digests, an enumerated job_type, a slug-
    # shaped scope), so the separator is sufficient and no length prefix is needed.
    job_id: Blake3Hash = Field(
        description="BLAKE3 over (job_type, sorted input ids, source_locator_digest or the empty string, params_digest, scope). Doubles as the idempotency key -- there is deliberately no second field for that, because two sources of truth for identity is how...",
    )

    # What kind of work. Enumerated rather than free-form so that a worker cannot be
    # handed a job type it has never heard of and improvise.
    job_type: JobSpecJobType = Field(
        description="What kind of work. Enumerated rather than free-form so that a worker cannot be handed a job type it has never heard of and improvise.",
    )

    inputs: JobInputs

    # BLAKE3 over the canonical JSON of `params`. Part of job_id, which is what makes
    # 'same job with different settings' a genuinely different job rather than a
    # silent overwrite of the first result.
    #
    # Canonical JSON here is the same rule `edl_id` states: keys sorted, no
    # insignificant whitespace, numbers in RFC 8785 / ECMAScript Number::toString form
    # so that 1.0 and 1 are one value, UTF-8 bytes. One canonicalisation for the whole
    # contract, deliberately -- a second one is how a digest starts disagreeing with
    # itself across languages.
    #
    # EVERYTHING THAT CHANGES THE RESULT MUST BE IN `params`. In particular the MODEL
    # PINS: naming a model by id alone meant that editing its config -- a detection
    # threshold, an NMS IoU -- left job_id unchanged, so the completed job was found
    # and every already-analysed record skipped. The library kept an analysis produced
    # by settings it was no longer configured with, and nothing said so.
    # `inputs.models` carries the same pins for provenance; the copy in `params` is
    # the one that affects identity, and a writer that fills one and not the other has
    # a bug.
    params_digest: Blake3Hash = Field(
        description="BLAKE3 over the canonical JSON of `params`. Part of job_id, which is what makes 'same job with different settings' a genuinely different job rather than a silent overwrite of the first result. Canonical JSON here is the same rule `edl_id...",
    )

    egress: EgressDeclaration

    state: JobState

    # Type-specific parameters. Free-form because the parameter shape of `plan_reel`
    # and `hash_file` have nothing in common, but never anonymous: params_digest pins
    # it. A worker validates its own params against its own local schema.
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific parameters. Free-form because the parameter shape of `plan_reel` and `hash_file` have nothing in common, but never anonymous: params_digest pins it. A worker validates its own params against its own local schema.",
    )

    # Namespace separating otherwise-identical work: a library id, a project id, or a
    # user id. Without it, two users analysing the same stock photo would collide on
    # job_id.
    scope: str | None = Field(
        default=None,
        description="Namespace separating otherwise-identical work: a library id, a project id, or a user id. Without it, two users analysing the same stock photo would collide on job_id.",
    )

    # Higher runs first. Interactive work (the user is watching a progress bar)
    # outranks background sweeps.
    priority: int = Field(
        default=100,
        description="Higher runs first. Interactive work (the user is watching a progress bar) outranks background sweeps.",
    )

    requirements: JobRequirements | None = Field(default=None)

    checkpoint: Checkpoint | None = Field(default=None)

    outputs: list[JobOutput] = Field(default_factory=list)

    error: JobError | None = Field(default=None)

    retry_policy: RetryPolicy | None = Field(default=None)

    journal: Journal | None = Field(default=None)

    created_at: Timestamp | None = Field(default=None)

    deadline: Timestamp | None = Field(default=None)


class AudioStream(ContractModel):
    stream_index: int

    channels: int

    sample_rate: int

    codec: str | None = Field(default=None)

    language: str | None = Field(default=None)

    # Detected during proxy generation. A silent track means the ambient mix has
    # nothing to preserve and music can sit at full level.
    is_silent: bool | None = Field(
        default=None,
        description="Detected during proxy generation. A silent track means the ambient mix has nothing to preserve and music can sit at full level.",
    )


class CaptureMetadataPresentItem(str, Enum):
    EXIF = "exif"
    XMP = "xmp"
    IPTC = "iptc"
    QUICKTIME = "quicktime"
    GOPRO_GPMF = "gopro_gpmf"
    TAKEOUT_JSON = "takeout_json"
    MAKER_NOTE = "maker_note"


class Capture(ContractModel):
    # Always a TimeAssertion, never a bare timestamp. A file with no EXIF gets an
    # assertion with source 'unknown' and precision 'unknown', not a fabricated date.
    captured_at: TimeAssertion = Field(
        description="Always a TimeAssertion, never a bare timestamp. A file with no EXIF gets an assertion with source 'unknown' and precision 'unknown', not a fabricated date.",
    )

    # Which metadata blocks were actually found. Empty array is the EXIF-less case and
    # is completely normal for WhatsApp media and screenshots.
    metadata_present: list[CaptureMetadataPresentItem] = Field(
        description="Which metadata blocks were actually found. Empty array is the EXIF-less case and is completely normal for WhatsApp media and screenshots.",
    )

    gps: GeoPoint | None = Field(default=None)

    device: DeviceInfo | None = Field(default=None)

    exposure: ExposureInfo | None = Field(default=None)


class ContentAnalysisTagsItemSource(str, Enum):
    ZERO_SHOT_SIGLIP = "zero_shot_siglip"
    OCR = "ocr"
    EXIF = "exif"
    USER = "user"
    TIER2_VLM = "tier2_vlm"
    TIER3_MODEL = "tier3_model"


class ContentAnalysisTagsItem(ContractModel):
    label: str

    score: Unit

    source: ContentAnalysisTagsItemSource


class ContentAnalysisSceneType(str, Enum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    FOOD = "food"
    DOCUMENT = "document"
    SCREENSHOT = "screenshot"
    NIGHT = "night"
    UNDERWATER = "underwater"
    AERIAL = "aerial"
    UNKNOWN = "unknown"


class ContentAnalysisOcr(ContractModel):
    """
    Present when text was found. Screenshot and document detection ride on this, and
    both are auto-excluded from memories.
    """

    has_text: bool

    text_area_ratio: Unit

    languages: list[str] = Field(default_factory=list)

    is_screenshot: bool = Field(default=False)

    is_document: bool = Field(default=False)


class ContentAnalysis(ContractModel):
    # One SigLIP embedding powers search, dedupe refinement, diversity constraints and
    # zero-shot tagging. This single field is the highest-leverage thing in the
    # record.
    embedding: VectorRef | None = Field(
        default=None,
        description="One SigLIP embedding powers search, dedupe refinement, diversity constraints and zero-shot tagging. This single field is the highest-leverage thing in the record.",
    )

    tags: list[ContentAnalysisTagsItem] = Field(default_factory=list)

    scene_type: ContentAnalysisSceneType | None = Field(default=None)

    # Present when text was found. Screenshot and document detection ride on this, and
    # both are auto-excluded from memories.
    ocr: ContentAnalysisOcr | None = Field(
        default=None,
        description="Present when text was found. Screenshot and document detection ride on this, and both are auto-excluded from memories.",
    )

    safety: SafetyAssessment | None = Field(default=None)


class DedupeMembershipMethod(str, Enum):
    EXACT_CONTENT_HASH = "exact_content_hash"
    PHASH_BUCKET = "phash_bucket"
    PHASH_BUCKET_EMBEDDING_REFINED = "phash_bucket_embedding_refined"
    BURST_METADATA = "burst_metadata"
    USER_GROUPED = "user_grouped"


class DedupeMembership(ContractModel):
    """
    Near-duplicate grouping. Exactly one member of a group is primary; the ranking
    engine picks it, and only the primary is eligible for automated output so a
    burst of 12 near-identical frames contributes one photo, not twelve.
    """

    group_id: Uuid

    is_primary: bool

    method: DedupeMembershipMethod

    primary_media_id: Blake3Hash | None = Field(default=None)

    similarity_to_primary: Unit | None = Field(default=None)

    group_size: int | None = Field(default=None)


class DeviceInfo(ContractModel):
    make: str | None = Field(default=None)

    model: str | None = Field(default=None)

    lens: str | None = Field(default=None)

    software: str | None = Field(default=None)

    # Hashed, never raw. A camera serial is a personal identifier; we want 'same body'
    # equality without storing the number.
    body_serial_hash: Blake3Hash | None = Field(
        default=None,
        description="Hashed, never raw. A camera serial is a personal identifier; we want 'same body' equality without storing the number.",
    )


class ErrorInfo(ContractModel):
    code: Slug

    # Human-readable, already redacted: no paths, no filenames, no EXIF. Crash
    # reporting forwards this verbatim.
    message: str = Field(
        description="Human-readable, already redacted: no paths, no filenames, no EXIF. Crash reporting forwards this verbatim.",
    )

    retryable: bool

    occurred_at: Timestamp | None = Field(default=None)


class ExclusionStateReasonsItem(str, Enum):
    SCREENSHOT = "screenshot"
    DOCUMENT = "document"
    NSFW = "nsfw"
    SENSITIVE = "sensitive"
    CORRUPT = "corrupt"
    UNREADABLE = "unreadable"
    DUPLICATE_SECONDARY = "duplicate_secondary"
    BELOW_QUALITY_FLOOR = "below_quality_floor"
    BLACK_FRAME = "black_frame"
    LENS_OBSTRUCTED = "lens_obstructed"
    TOO_SHORT = "too_short"
    USER_HIDDEN = "user_hidden"
    UNSUPPORTED_CODEC = "unsupported_codec"


class ExclusionState(ContractModel):
    """
    Whether this file may appear in unattended output. Separate from user hiding:
    exclusion is a system judgement with a stated reason, and every reason is
    individually overridable.
    """

    excluded_from_automation: bool

    reasons: list[ExclusionStateReasonsItem] = Field(default_factory=list)

    # Tri-state on purpose. null = no opinion, true = user forced it in, false = user
    # forced it out. Distinguishing 'user said include' from 'system did not exclude'
    # matters when the exclusion rules later change.
    user_override: bool | None = Field(
        default=None,
        description="Tri-state on purpose. null = no opinion, true = user forced it in, false = user forced it out. Distinguishing 'user said include' from 'system did not exclude' matters when the exclusion rules later change.",
    )


class ExposureInfo(ContractModel):
    """
    Shot settings, used as priors by the technical quality pass: a 1/8s handheld
    exposure predicts motion blur before any model runs.
    """

    iso: int | None = Field(default=None)

    exposure_time_s: float | None = Field(default=None)

    f_number: float | None = Field(default=None)

    focal_length_mm: float | None = Field(default=None)

    focal_length_35mm: float | None = Field(default=None)

    flash_fired: bool | None = Field(default=None)

    metering_mode: str | None = Field(default=None)


class FaceSummary(ContractModel):
    face_count: int

    face_ids: list[Blake3Hash]

    # Only people whose assignment is eligible for automated output. A person
    # appearing here is safe to use for 'album of Avika'; anything less certain is
    # deliberately absent.
    confirmed_person_ids: list[Uuid] = Field(
        default_factory=list,
        description="Only people whose assignment is eligible for automated output. A person appearing here is safe to use for 'album of Avika'; anything less certain is deliberately absent.",
    )

    pending_review_count: int = Field(default=0)

    largest_face_area_ratio: Unit | None = Field(default=None)


class FrameIndexSidecarMapping(str, Enum):
    IDENTITY = "identity"
    TABLE = "table"


class FrameIndexSidecar(ContractModel):
    """
    Mapping from proxy time to source timecode. The intelligence layer works
    entirely in proxy time; this is the single point where that is converted to
    something the renderer can seek to in the original.
    """

    path: str

    entry_count: int

    # `identity` when proxy and source share a frame timeline and no lookup is needed
    # -- the common CFR case. `table` when the sidecar must be consulted per frame,
    # which is the VFR phone-video case.
    mapping: FrameIndexSidecarMapping = Field(
        description="`identity` when proxy and source share a frame timeline and no lookup is needed -- the common CFR case. `table` when the sidecar must be consulted per frame, which is the VFR phone-video case.",
    )

    source_rate: float | None = Field(default=None)

    proxy_rate: float | None = Field(default=None)


class ImagePropertiesColorSpace(str, Enum):
    SRGB = "srgb"
    DISPLAY_P3 = "display_p3"
    ADOBE_RGB = "adobe_rgb"
    PROPHOTO_RGB = "prophoto_rgb"
    REC2020 = "rec2020"
    LINEAR = "linear"
    UNKNOWN = "unknown"


class ImageProperties(ContractModel):
    # Pixel dimensions as stored in the file, before EXIF orientation is applied.
    stored_size: PixelSize = Field(
        description="Pixel dimensions as stored in the file, before EXIF orientation is applied.",
    )

    # Dimensions after orientation. Every NormalizedBox in the system is relative to
    # THIS, which removes an entire class of rotated-crop bugs.
    oriented_size: PixelSize = Field(
        description="Dimensions after orientation. Every NormalizedBox in the system is relative to THIS, which removes an entire class of rotated-crop bugs.",
    )

    # EXIF orientation tag, 1-8.
    orientation: int = Field(default=1, description="EXIF orientation tag, 1-8.")

    bit_depth: int | None = Field(default=None)

    color_space: ImagePropertiesColorSpace | None = Field(default=None)

    icc_profile_name: str | None = Field(default=None)

    has_alpha: bool = Field(default=False)

    is_raw: bool = Field(default=False)

    is_hdr: bool = Field(default=False)

    # For a Live Photo or Motion Photo, the record holding the motion track.
    paired_motion_media_id: Blake3Hash | None = Field(
        default=None,
        description="For a Live Photo or Motion Photo, the record holding the motion track.",
    )


class PerceptualFingerprintKeyframeHashesItem(ContractModel):
    time: RationalTime

    hash: PerceptualHash


class PerceptualFingerprint(ContractModel):
    image_hash: PerceptualHash | None = Field(default=None)

    # Per-keyframe hashes for video, so a clip that appears in two exports of the same
    # trip is recognised as duplicate footage.
    keyframe_hashes: list[PerceptualFingerprintKeyframeHashesItem] = Field(
        default_factory=list,
        description="Per-keyframe hashes for video, so a clip that appears in two exports of the same trip is recognised as duplicate footage.",
    )


class ProcessingStateState(str, Enum):
    DISCOVERED = "discovered"
    HASHED = "hashed"
    PROXIED = "proxied"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class ProcessingStateStages(ContractModel):
    hash: StageState | None = Field(default=None)

    metadata: StageState | None = Field(default=None)

    thumbnail: StageState | None = Field(default=None)

    video_proxy: StageState | None = Field(default=None)

    perceptual_hash: StageState | None = Field(default=None)

    classical_quality: StageState | None = Field(default=None)

    image_embedding: StageState | None = Field(default=None)

    face_detection: StageState | None = Field(default=None)

    # Aligning each detected face onto the recognition model's template and embedding
    # it. Separate from `face_detection` because they fail and resume separately: a
    # detector that ran and an embedder that was missing must leave the library with
    # face BOXES -- which the print validator's trim-zone check needs and which have
    # nothing to do with identity -- rather than with neither.
    face_embedding: StageState | None = Field(
        default=None,
        description="Aligning each detected face onto the recognition model's template and embedding it. Separate from `face_detection` because they fail and resume separately: a detector that ran and an embedder that was missing must leave the library with ...",
    )

    iqa: StageState | None = Field(default=None)

    aesthetic: StageState | None = Field(default=None)

    tagging: StageState | None = Field(default=None)

    safety: StageState | None = Field(default=None)

    ocr: StageState | None = Field(default=None)

    shot_detection: StageState | None = Field(default=None)

    transcription: StageState | None = Field(default=None)

    audio_events: StageState | None = Field(default=None)

    moment_scoring: StageState | None = Field(default=None)


class ProcessingState(ContractModel):
    """
    Per-stage pipeline state. Granular per stage rather than one status field
    because a 3TB scan is killed and resumed constantly, and 'hashed but not yet
    proxied' has to be a first-class, restartable position.
    """

    # Rollup for UI and query. `quarantined` means the file is unreadable or hostile
    # (zero-byte, truncated, symlink loop) and must never be retried automatically.
    state: ProcessingStateState = Field(
        description="Rollup for UI and query. `quarantined` means the file is unreadable or hostile (zero-byte, truncated, symlink loop) and must never be retried automatically.",
    )

    stages: ProcessingStateStages


class ProxyRefKind(str, Enum):
    THUMBNAIL_512 = "thumbnail_512"
    PREVIEW_2048 = "preview_2048"
    VIDEO_PROXY_480P = "video_proxy_480p"
    WAVEFORM = "waveform"
    CONTACT_SHEET_TILE = "contact_sheet_tile"
    AUDIO_WAV_16K = "audio_wav_16k"


class ProxyRef(ContractModel):
    """
    A derived rendition on local disk. Content-addressed like everything else, so a
    proxy regenerated with the same tool version is recognised as the same artifact.
    """

    proxy_id: Blake3Hash

    kind: ProxyRefKind

    path: str

    size: PixelSize | None = Field(default=None)

    byte_size: int | None = Field(default=None)

    # Tool + settings that produced it. A change here invalidates the proxy without
    # deleting anything.
    generator_version: str | None = Field(
        default=None,
        description="Tool + settings that produced it. A change here invalidates the proxy without deleting anything.",
    )

    # Present on video_proxy_480p only. The proxy is single-pass and may not be frame-
    # exact against the source, so analysis results measured in proxy time must be
    # mapped back through this sidecar before they can address source timecode.
    frame_index: FrameIndexSidecar | None = Field(
        default=None,
        description="Present on video_proxy_480p only. The proxy is single-pass and may not be frame-exact against the source, so analysis results measured in proxy time must be mapped back through this sidecar before they can address source timecode.",
    )


class QualityScores(ContractModel):
    """
    Technical quality, cheapest measures first. The classical measures alone reject
    most of the junk for free (build plan 4.2), so they are required and the learned
    scores are optional.
    """

    # Laplacian-variance derived, normalised. Low means blurred, whether from focus or
    # motion.
    sharpness: Score = Field(
        description="Laplacian-variance derived, normalised. Low means blurred, whether from focus or motion.",
    )

    # Histogram-derived. Penalises clipped highlights and crushed shadows; 1.0 is a
    # well-distributed histogram.
    exposure: Score = Field(
        description="Histogram-derived. Penalises clipped highlights and crushed shadows; 1.0 is a well-distributed histogram.",
    )

    noise: Score | None = Field(default=None)

    contrast: Score | None = Field(default=None)

    # Learned no-reference IQA (MUSIQ/TOPIQ class).
    technical_iqa: Score | None = Field(
        default=None,
        description="Learned no-reference IQA (MUSIQ/TOPIQ class).",
    )

    # Aesthetic prior. Explicitly a PRIOR: the ranking engine reweights this per user
    # from PrefEvents, so it must never be treated as ground truth.
    aesthetic: Score | None = Field(
        default=None,
        description="Aesthetic prior. Explicitly a PRIOR: the ranking engine reweights this per user from PrefEvents, so it must never be treated as ground truth.",
    )

    composition: Score | None = Field(default=None)

    # Best face quality in the frame: eyes open, unblurred, forward-facing. Null when
    # there are no faces.
    face_quality: Score | None = Field(
        default=None,
        description="Best face quality in the frame: eyes open, unblurred, forward-facing. Null when there are no faces.",
    )

    is_black_frame: bool = Field(default=False)

    # Lens cap, pocket footage, finger over the lens. Cheap to detect and enormously
    # common on action-camera cards.
    is_lens_obstructed: bool = Field(
        default=False,
        description="Lens cap, pocket footage, finger over the lens. Cheap to detect and enormously common on action-camera cards.",
    )


class SafetyAssessmentCategoriesItem(str, Enum):
    NUDITY = "nudity"
    SEXUAL = "sexual"
    VIOLENCE = "violence"
    GORE = "gore"
    MEDICAL = "medical"
    DOCUMENT_PII = "document_pii"
    UNKNOWN = "unknown"


class SafetyAssessment(ContractModel):
    nsfw_score: Confidence

    # Excluded by default from all automated output. The user can override per item;
    # the override is recorded in UserAnnotations, never by mutating this field.
    auto_excluded: bool = Field(
        description="Excluded by default from all automated output. The user can override per item; the override is recorded in UserAnnotations, never by mutating this field.",
    )

    categories: list[SafetyAssessmentCategoriesItem] = Field(default_factory=list)

    threshold_used: Confidence | None = Field(default=None)


class SourceLocationAdapter(str, Enum):
    FILESYSTEM = "filesystem"
    GOOGLE_TAKEOUT = "google_takeout"
    ICLOUD_EXPORT = "icloud_export"
    WHATSAPP = "whatsapp"
    GOPRO_CARD = "gopro_card"
    DSLR_CARD = "dslr_card"
    PHONE_GALLERY = "phone_gallery"
    INSTA360 = "insta360"
    DRONE_CARD = "drone_card"
    MANUAL_IMPORT = "manual_import"


class SourceLocation(ContractModel):
    # Absolute path as seen at scan time. Local only -- this string never leaves the
    # device and is stripped by the crash-reporter privacy filter.
    path: str = Field(
        description="Absolute path as seen at scan time. Local only -- this string never leaves the device and is stripped by the crash-reporter privacy filter.",
    )

    # Which ingest adapter found it. Drives source-specific metadata recovery: Takeout
    # puts the real date in a sidecar JSON, WhatsApp puts it in the filename, and
    # neither has usable EXIF.
    adapter: SourceLocationAdapter = Field(
        description="Which ingest adapter found it. Drives source-specific metadata recovery: Takeout puts the real date in a sidecar JSON, WhatsApp puts it in the filename, and neither has usable EXIF.",
    )

    first_seen_at: Timestamp

    # False when the path no longer resolves. The record is retained: losing sight of
    # a file is not permission to forget everything we learned about it (hard rule 7,
    # no silent data loss).
    present: bool = Field(
        description="False when the path no longer resolves. The record is retained: losing sight of a file is not permission to forget everything we learned about it (hard rule 7, no silent data loss).",
    )

    # Stable identifier for the containing volume, so an unplugged external drive is
    # reported as 'offline' rather than 'deleted'.
    volume_id: str | None = Field(
        default=None,
        description="Stable identifier for the containing volume, so an unplugged external drive is reported as 'offline' rather than 'deleted'.",
    )

    # Companion files: XMP, Takeout's .json, GoPro's .THM/.LRV, Live Photo's paired
    # .MOV.
    sidecar_paths: list[str] = Field(
        default_factory=list,
        description="Companion files: XMP, Takeout's .json, GoPro's .THM/.LRV, Live Photo's paired .MOV.",
    )

    # Filename as it appeared, kept after any rename because WhatsApp and camera
    # naming conventions are the only date source for a large share of real libraries.
    original_filename: str | None = Field(
        default=None,
        description="Filename as it appeared, kept after any rename because WhatsApp and camera naming conventions are the only date source for a large share of real libraries.",
    )

    last_verified_at: Timestamp | None = Field(default=None)


class SpanRole(str, Enum):
    MEMBER = "member"
    ASSEMBLY = "assembly"


class SpanSpanKind(str, Enum):
    GOPRO_CHAPTER = "gopro_chapter"
    DSLR_SIZE_SPLIT = "dslr_size_split"
    INSTA360_LENS_PAIR = "insta360_lens_pair"
    MANUAL_GROUP = "manual_group"


class SpanContinuity(str, Enum):
    VERIFIED_GAPLESS = "verified_gapless"
    VERIFIED_GAP = "verified_gap"
    UNVERIFIED = "unverified"
    INCOMPLETE_SET = "incomplete_set"


class Span(ContractModel):
    """
    Membership in a multi-file recording. Modelled as a role on each record rather
    than a nested structure so that a chapter can be discovered, hashed and analysed
    before its siblings have even been walked -- which is exactly what happens on a
    400-file GoPro card.
    """

    # BLAKE3 over the ordered member media_ids once the set is closed. Before closure,
    # a provisional id derived from the camera's own group identifier (GoPro's file
    # number, e.g. 1234 in GH011234.MP4). On the assembly record this is also the
    # media_id -- the assembly has no bytes to hash, so its members' identity is its
    # identity.
    #
    # CANONICAL ENCODING, because 'BLAKE3 over the ids' does not determine the bytes
    # and three plausible readings gave three different identities: span_id = BLAKE3(
    # concat(member_media_ids in index order) ) where each id contributes its 64
    # lowercase ASCII hex characters, with NO delimiter, NO length prefix and NO
    # domain separator.
    #
    # The absence of a delimiter is safe rather than lucky: every Blake3Hash is
    # exactly 64 hex characters, so the concatenation is fixed-width and therefore
    # prefix-free -- no two different member lists can produce the same byte string. A
    # variable-length encoding would need a delimiter to avoid that, which is where
    # this class of bug usually starts.
    #
    # ORDER IS INDEX ORDER, NOT SORTED ORDER. Chapters are a sequence: GH011234,
    # GH021234, GH031234 concatenate into one recording in that order, and sorting by
    # hash would scramble a timeline. The assembly's identity therefore changes if the
    # chapters are reordered, which is correct -- a different order is a different
    # recording.
    #
    # Codex raised this (issue #26) after finding that the golden fixture's span_id
    # matched none of the plausible readings. It matched none of them because it had
    # been written by hand rather than computed, so the fixture was not testing the
    # identity at all. contracts/tests recomputes it now.
    span_id: Blake3Hash = Field(
        description="BLAKE3 over the ordered member media_ids once the set is closed. Before closure, a provisional id derived from the camera's own group identifier (GoPro's file number, e.g. 1234 in GH011234.MP4). On the assembly record this is also the me...",
    )

    # `member` is a physical file on disk and always sits on an asset_kind of
    # physical_file. `assembly` is the virtual record representing the concatenated
    # recording; it is always asset_kind virtual_assembly, carries byte_size 0, no
    # sources and no proxies, and is what MomentRecords and EDL clips reference so a
    # cut can cross a chapter boundary without the planner knowing chapters exist.
    role: SpanRole = Field(
        description="`member` is a physical file on disk and always sits on an asset_kind of physical_file. `assembly` is the virtual record representing the concatenated recording; it is always asset_kind virtual_assembly, carries byte_size 0, no sources an...",
    )

    span_kind: SpanSpanKind

    # 0-based position within the recording. Required on members, null on the
    # assembly.
    index: int | None = Field(
        default=None,
        description="0-based position within the recording. Required on members, null on the assembly.",
    )

    member_count: int | None = Field(default=None)

    # Ordered member ids. Populated on the assembly record only.
    member_media_ids: list[Blake3Hash] = Field(
        default_factory=list,
        description="Ordered member ids. Populated on the assembly record only.",
    )

    # Where this member starts within the assembly's timeline. Lets a MomentRecord on
    # the assembly be resolved back to (file, timecode) at render without re-probing
    # every chapter.
    offset_in_span: RationalTime | None = Field(
        default=None,
        description="Where this member starts within the assembly's timeline. Lets a MomentRecord on the assembly be resolved back to (file, timecode) at render without re-probing every chapter.",
    )

    # Whether the chapters were verified to be gapless. Cameras occasionally drop a
    # frame at the split; the renderer must know before it concatenates.
    continuity: SpanContinuity = Field(
        default="unverified",
        description="Whether the chapters were verified to be gapless. Cameras occasionally drop a frame at the split; the renderer must know before it concatenates.",
    )


class StageStateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"


class StageState(ContractModel):
    status: StageStateStatus

    attempts: int = Field(default=0)

    completed_at: Timestamp | None = Field(default=None)

    job_id: Blake3Hash | None = Field(default=None)

    # Why a stage was skipped. Required reading for 'no silent anything': a skipped
    # stage must be explicable without reading logs.
    skip_reason: str | None = Field(
        default=None,
        description="Why a stage was skipped. Required reading for 'no silent anything': a skipped stage must be explicable without reading logs.",
    )

    last_error: ErrorInfo | None = Field(default=None)


class UserAnnotations(ContractModel):
    favorite: bool = Field(default=False)

    hidden: bool = Field(default=False)

    rating: int | None = Field(default=None)

    tags: list[str] = Field(default_factory=list)

    caption: str | None = Field(default=None)


class VideoPropertiesRotationDeg(int, Enum):
    V_0 = 0
    V_90 = 90
    V_180 = 180
    V_270 = 270


class VideoPropertiesFrameRate(ContractModel):
    """
    Exact frame rate as a rational. 30000/1001 must survive as such; storing 29.97
    as a float and reconstructing it later is how beat-locked cuts drift.
    """

    numerator: int

    denominator: int


class VideoProperties(ContractModel):
    oriented_size: PixelSize

    duration: RationalTime

    # Exact frame rate as a rational. 30000/1001 must survive as such; storing 29.97
    # as a float and reconstructing it later is how beat-locked cuts drift.
    frame_rate: VideoPropertiesFrameRate = Field(
        description="Exact frame rate as a rational. 30000/1001 must survive as such; storing 29.97 as a float and reconstructing it later is how beat-locked cuts drift.",
    )

    stored_size: PixelSize | None = Field(default=None)

    # Rotation from the container's display matrix. Phone video is almost always
    # stored landscape with a rotation flag.
    rotation_deg: VideoPropertiesRotationDeg = Field(
        default=0,
        description="Rotation from the container's display matrix. Phone video is almost always stored landscape with a rotation flag.",
    )

    # True for most phone video. A VFR source must be conformed before frame-accurate
    # cutting, and the renderer needs to be told, not surprised.
    is_variable_frame_rate: bool = Field(
        default=False,
        description="True for most phone video. A VFR source must be conformed before frame-accurate cutting, and the renderer needs to be told, not surprised.",
    )

    # Embedded SMPTE timecode track start, when present. Carried through to OTIO so a
    # professional round-trip lands on the right frame.
    start_timecode: RationalTime | None = Field(
        default=None,
        description="Embedded SMPTE timecode track start, when present. Carried through to OTIO so a professional round-trip lands on the right frame.",
    )

    video_codec: str | None = Field(default=None)

    bit_rate: int | None = Field(default=None)

    color_primaries: str | None = Field(default=None)

    # HLG or PQ here means HDR footage, which changes both the enhancement plan and
    # the encode profile.
    transfer_characteristics: str | None = Field(
        default=None,
        description="HLG or PQ here means HDR footage, which changes both the enhancement plan and the encode profile.",
    )

    audio_streams: list[AudioStream] = Field(default_factory=list)


class MediaRecordAssetKind(str, Enum):
    PHYSICAL_FILE = "physical_file"
    VIRTUAL_ASSEMBLY = "virtual_assembly"


class MediaRecordKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    LIVE_PHOTO = "live_photo"
    MOTION_PHOTO = "motion_photo"
    AUDIO = "audio"
    SIDECAR = "sidecar"
    UNKNOWN = "unknown"


class MediaRecordFileFormat(str, Enum):
    JPEG = "jpeg"
    PNG = "png"
    HEIC = "heic"
    HEIF = "heif"
    AVIF = "avif"
    WEBP = "webp"
    TIFF = "tiff"
    DNG = "dng"
    CR2 = "cr2"
    CR3 = "cr3"
    NEF = "nef"
    ARW = "arw"
    RAF = "raf"
    ORF = "orf"
    RW2 = "rw2"
    GIF = "gif"
    BMP = "bmp"
    MP4 = "mp4"
    MOV = "mov"
    AVI = "avi"
    MKV = "mkv"
    WEBM = "webm"
    M4V = "m4v"
    MTS = "mts"
    V_3GP = "3gp"
    INSV = "insv"
    WAV = "wav"
    MP3 = "mp3"
    M4A = "m4a"
    AAC = "aac"
    FLAC = "flac"
    UNKNOWN = "unknown"


class MediaRecord(ContractModel):
    """
    The identity of one media file and everything the analysis layer has learned
    about it. One record per physical file, always -- a GoPro chapter set is N
    member records plus one assembly record, never a record that pretends four files
    are one.
    """

    schema_version: SchemaVersion

    # Primary key. For a physical_file this is the BLAKE3 of the file's bytes; for a
    # virtual_assembly it is the span_id, a BLAKE3 over the ordered member media_ids.
    # Content-addressed either way, so re-importing is a no-op and every downstream
    # job keyed on it is idempotent.
    media_id: Blake3Hash = Field(
        description="Primary key. For a physical_file this is the BLAKE3 of the file's bytes; for a virtual_assembly it is the span_id, a BLAKE3 over the ordered member media_ids. Content-addressed either way, so re-importing is a no-op and every downstream ...",
    )

    # Whether this record describes bytes on disk or a virtual assembly of other
    # records. Required and explicit: the identity, size and source rules differ
    # between the two, and a reader must never have to infer which set applies.
    asset_kind: MediaRecordAssetKind = Field(
        description="Whether this record describes bytes on disk or a virtual assembly of other records. Required and explicit: the identity, size and source rules differ between the two, and a reader must never have to infer which set applies.",
    )

    # Top-level media class. `live_photo` and `motion_photo` are their own kind rather
    # than image-with-a-video because the still and the motion track are separately
    # renderable and separately rankable.
    kind: MediaRecordKind = Field(
        description="Top-level media class. `live_photo` and `motion_photo` are their own kind rather than image-with-a-video because the still and the motion track are separately renderable and separately rankable.",
    )

    byte_size: int

    # Every place on disk these exact bytes have been seen. Plural because
    # deduplication by content is the whole point: one record, many paths. A
    # physical_file always has at least one; a virtual_assembly always has none,
    # because its members own the paths and duplicating one of them here would make
    # the assembly look like a file that can be opened.
    sources: list[SourceLocation] = Field(
        description="Every place on disk these exact bytes have been seen. Plural because deduplication by content is the whole point: one record, many paths. A physical_file always has at least one; a virtual_assembly always has none, because its members ow...",
    )

    capture: Capture

    processing: ProcessingState

    mime_type: str | None = Field(default=None)

    # Container as detected from content, not from the extension. A .jpg that is
    # actually HEIC is common in exports and must not be trusted by extension.
    file_format: MediaRecordFileFormat | None = Field(
        default=None,
        description="Container as detected from content, not from the extension. A .jpg that is actually HEIC is common in exports and must not be trusted by extension.",
    )

    # Set membership for footage split across multiple files. Present on GoPro
    # chaptered MP4s (GH011234.MP4, GH021234.MP4, ...), DSLR 4GB-limit splits, and
    # Insta360 .insv sets. Null for the overwhelming majority of files.
    span: Span | None = Field(
        default=None,
        description="Set membership for footage split across multiple files. Present on GoPro chaptered MP4s (GH011234.MP4, GH021234.MP4, ...), DSLR 4GB-limit splits, and Insta360 .insv sets. Null for the overwhelming majority of files.",
    )

    image: ImageProperties | None = Field(default=None)

    video: VideoProperties | None = Field(default=None)

    perceptual: PerceptualFingerprint | None = Field(default=None)

    # Derived renditions. Analysis reads only these; the original is opened exactly
    # twice in the file's life, once to make these and once at final render.
    proxies: list[ProxyRef] = Field(
        default_factory=list,
        description="Derived renditions. Analysis reads only these; the original is opened exactly twice in the file's life, once to make these and once at final render.",
    )

    quality: QualityScores | None = Field(default=None)

    content: ContentAnalysis | None = Field(default=None)

    # Denormalised face summary. The authoritative per-face data lives in FaceRecord;
    # this block exists so the library grid can filter 'photos with 3+ people' without
    # joining.
    faces: FaceSummary | None = Field(
        default=None,
        description="Denormalised face summary. The authoritative per-face data lives in FaceRecord; this block exists so the library grid can filter 'photos with 3+ people' without joining.",
    )

    dedupe: DedupeMembership | None = Field(default=None)

    exclusion: ExclusionState | None = Field(default=None)

    user: UserAnnotations | None = Field(default=None)

    # Provenance for every score on this record. Scores reference these by run_id.
    model_runs: list[ModelRun] = Field(
        default_factory=list,
        description="Provenance for every score on this record. Scores reference these by run_id.",
    )

    first_seen_at: Timestamp | None = Field(default=None)

    updated_at: Timestamp | None = Field(default=None)


class AudioFeaturesEventsItemLabel(str, Enum):
    LAUGHTER = "laughter"
    CHEERING = "cheering"
    APPLAUSE = "applause"
    CRYING = "crying"
    SINGING = "singing"
    SHOUTING = "shouting"
    SPLASH = "splash"
    MUSIC = "music"
    SPEECH = "speech"
    WIND = "wind"
    SILENCE = "silence"
    ENGINE = "engine"
    ANIMAL = "animal"
    FIREWORKS = "fireworks"
    OTHER = "other"


class AudioFeaturesEventsItem(ContractModel):
    label: AudioFeaturesEventsItemLabel

    confidence: Confidence

    time: RationalTime | None = Field(default=None)


class AudioFeatures(ContractModel):
    loudness_lufs: float | None = Field(default=None)

    speech_ratio: Unit | None = Field(default=None)

    music_ratio: Unit | None = Field(default=None)

    # Wind and handling noise. High values are why a visually perfect action shot may
    # still need its ambient ducked to nothing.
    noise_ratio: Unit | None = Field(
        default=None,
        description="Wind and handling noise. High values are why a visually perfect action shot may still need its ambient ducked to nothing.",
    )

    # Detected audio events with their own confidences. Laughter and cheering are
    # among the strongest emotional-peak signals available to a local model.
    events: list[AudioFeaturesEventsItem] = Field(
        default_factory=list,
        description="Detected audio events with their own confidences. Laughter and cheering are among the strongest emotional-peak signals available to a local model.",
    )

    embedding: VectorRef | None = Field(default=None)


class EliminationReasonsItem(str, Enum):
    SHAKE = "shake"
    BLOWN_EXPOSURE = "blown_exposure"
    CRUSHED_EXPOSURE = "crushed_exposure"
    BLACK_FRAME = "black_frame"
    LENS_OBSTRUCTED = "lens_obstructed"
    NO_MOTION = "no_motion"
    TOO_SHORT = "too_short"
    NO_SUBJECT = "no_subject"
    DUPLICATE_FOOTAGE = "duplicate_footage"
    WIND_NOISE_DOMINANT = "wind_noise_dominant"
    BELOW_SCORE_FLOOR = "below_score_floor"
    USER_REJECTED = "user_rejected"


class EliminationStage(str, Enum):
    CLASSICAL = "classical"
    LOCAL_MODEL = "local_model"
    FUSION = "fusion"
    PLANNER = "planner"
    USER = "user"


class Elimination(ContractModel):
    """
    Elimination-first is the biggest cost and quality lever in the system, so its
    result is a required, structured field rather than the absence of a record.
    """

    eliminated: bool

    reasons: list[EliminationReasonsItem] = Field(default_factory=list)

    # How far the moment got before being dropped. `classical` eliminations are the
    # free ones and should account for the overwhelming majority.
    stage: EliminationStage | None = Field(
        default=None,
        description="How far the moment got before being dropped. `classical` eliminations are the free ones and should account for the overwhelming majority.",
    )


class MomentFeatures(ContractModel):
    """
    The fused feature stream over the moment's window. These are the inputs to score
    fusion, and they are exactly what a PrefEvent captures as decision context --
    which is why they are named, bounded and stable rather than an opaque vector.
    """

    # Mean optical-flow magnitude, normalised. High is action; near-zero over a long
    # window is tripod dead time and gets eliminated.
    motion_energy: Unit | None = Field(
        default=None,
        description="Mean optical-flow magnitude, normalised. High is action; near-zero over a long window is tripod dead time and gets eliminated.",
    )

    motion_peak: Unit | None = Field(default=None)

    # Camera instability distinct from subject motion. The single most common reason
    # handheld footage is unusable.
    shake: Unit | None = Field(
        default=None,
        description="Camera instability distinct from subject motion. The single most common reason handheld footage is unusable.",
    )

    # Low when the camera is hunting exposure, e.g. walking from indoors into sun.
    # Such a window looks bad no matter how good the content is.
    exposure_stability: Unit | None = Field(
        default=None,
        description="Low when the camera is hunting exposure, e.g. walking from indoors into sun. Such a window looks bad no matter how good the content is.",
    )

    sharpness: Unit | None = Field(default=None)

    # Fraction of frames in the window containing at least one face.
    face_presence: Unit | None = Field(
        default=None,
        description="Fraction of frames in the window containing at least one face.",
    )

    max_face_area_ratio: Unit | None = Field(default=None)

    smile_intensity: Unit | None = Field(default=None)

    audio: AudioFeatures | None = Field(default=None)

    # SigLIP embedding of the moment's representative keyframe. Drives diversity
    # constraints, so two moments that look alike cannot both make the cut.
    visual_embedding: VectorRef | None = Field(
        default=None,
        description="SigLIP embedding of the moment's representative keyframe. Drives diversity constraints, so two moments that look alike cannot both make the cut.",
    )

    # Distance from everything already selected. This is what stops a reel being six
    # near-identical drone shots.
    novelty: Unit | None = Field(
        default=None,
        description="Distance from everything already selected. This is what stops a reel being six near-identical drone shots.",
    )

    # Best single frame in the window, used as the contact-sheet tile shown to the
    # frontier model.
    representative_frame_time: RationalTime | None = Field(
        default=None,
        description="Best single frame in the window, used as the contact-sheet tile shown to the frontier model.",
    )


class MomentScoresSource(str, Enum):
    LOCAL_FUSION = "local_fusion"
    LOCAL_LEARNED = "local_learned"
    TIER2_VLM = "tier2_vlm"
    TIER3_MODEL = "tier3_model"
    USER_OVERRIDE = "user_override"


class MomentScores(ContractModel):
    """
    Fused judgements. `moment_score` is the only required one; the rest are the
    decomposition that makes it explainable and per-user reweightable.
    """

    # Overall keepworthiness. v1 is a hand-weighted linear fusion because a
    # transparent, tunable model beats an opaque one until PrefEvents exist to train
    # on.
    moment_score: Score = Field(
        description="Overall keepworthiness. v1 is a hand-weighted linear fusion because a transparent, tunable model beats an opaque one until PrefEvents exist to train on.",
    )

    technical: Score | None = Field(default=None)

    # Fitness for the first second of a reel, where retention is won or lost. Rewards
    # immediate motion or an immediate face, and punishes slow builds.
    hook_potential: Score | None = Field(
        default=None,
        description="Fitness for the first second of a reel, where retention is won or lost. Rewards immediate motion or an immediate face, and punishes slow builds.",
    )

    # How much this feels like a moment rather than merely looking like one. Local
    # features approximate it via laughter, smiles and motion onsets; the frontier
    # model is the one that can actually tell 'child sees the ocean' from 'child near
    # ocean', and when it has ruled, `source` says so.
    emotional_peak: Score | None = Field(
        default=None,
        description="How much this feels like a moment rather than merely looking like one. Local features approximate it via laughter, smiles and motion onsets; the frontier model is the one that can actually tell 'child sees the ocean' from 'child near oce...",
    )

    # Contribution to a story beat, only ever populated by a Tier 2/3 reasoning pass.
    narrative_value: Score | None = Field(
        default=None,
        description="Contribution to a story beat, only ever populated by a Tier 2/3 reasoning pass.",
    )

    # Which tier produced the judgement scores. Never let a frontier-model opinion be
    # mistaken for a local measurement.
    source: MomentScoresSource = Field(
        default="local_fusion",
        description="Which tier produced the judgement scores. Never let a frontier-model opinion be mistaken for a local measurement.",
    )

    # Which weight set produced moment_score. Per-user reweighting means the same
    # features legitimately yield different scores for different people, and the score
    # is meaningless without knowing which weights applied.
    fusion_weights_version: str | None = Field(
        default=None,
        description="Which weight set produced moment_score. Per-user reweighting means the same features legitimately yield different scores for different people, and the score is meaningless without knowing which weights applied.",
    )


class SafeTrim(ContractModel):
    """
    Hard bounds on trimming. `speech_safe_*` are the ones that make the no-mid-word
    guarantee exact: they are derived from word-level timestamps, not from voice-
    activity guesses.
    """

    earliest_in: RationalTime

    latest_out: RationalTime

    # Earliest in-point that does not land inside a spoken word. Null when the moment
    # contains no speech, in which case earliest_in applies.
    speech_safe_in: RationalTime | None = Field(
        default=None,
        description="Earliest in-point that does not land inside a spoken word. Null when the moment contains no speech, in which case earliest_in applies.",
    )

    speech_safe_out: RationalTime | None = Field(default=None)

    # Below this the moment reads as a flash frame rather than a shot.
    min_duration: RationalTime | None = Field(
        default=None,
        description="Below this the moment reads as a flash frame rather than a shot.",
    )

    # True when the audio meaningfully continues past the visual out-point -- a laugh
    # that lands after the cut. The renderer honours this with an audio-only extension
    # (an L-cut), which is a decision that must live in the plan.
    preserve_audio_tail: bool = Field(
        default=False,
        description="True when the audio meaningfully continues past the visual out-point -- a laugh that lands after the cut. The renderer honours this with an audio-only extension (an L-cut), which is a decision that must live in the plan.",
    )


class SnapPointKind(str, Enum):
    SHOT_BOUNDARY = "shot_boundary"
    MOTION_ONSET = "motion_onset"
    MOTION_OFFSET = "motion_offset"
    AUDIO_ONSET = "audio_onset"
    SPEECH_GAP = "speech_gap"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    SUBJECT_ENTRY = "subject_entry"
    SUBJECT_EXIT = "subject_exit"
    IMPACT = "impact"
    SCENE_BRIGHTNESS_CHANGE = "scene_brightness_change"


class SnapPointCutDirection(str, Enum):
    IN = "in"
    OUT = "out"
    BOTH = "both"


class SnapPoint(ContractModel):
    """
    A time at which cutting is defensible, with a reason. The planner may only place
    cuts on snap points; that constraint is what makes 'beat-alignment error < 50ms'
    and 'no mid-word cuts' testable properties of a plan rather than emergent
    behaviour.
    """

    # Source timecode. Exact rational, never rounded to milliseconds -- the 50ms beat-
    # alignment gate has no headroom to spare on rounding.
    time: RationalTime = Field(
        description="Source timecode. Exact rational, never rounded to milliseconds -- the 50ms beat-alignment gate has no headroom to spare on rounding.",
    )

    kind: SnapPointKind

    # How pronounced the boundary is. Cutting on a weak onset is worse than cutting
    # 40ms later on a strong one.
    strength: Unit = Field(
        description="How pronounced the boundary is. Cutting on a weak onset is worse than cutting 40ms later on a strong one.",
    )

    confidence: Confidence | None = Field(default=None)

    # Whether this point is usable as an in-point, an out-point, or both. A motion
    # onset is a great in-point and a poor out-point; encoding that asymmetry stops
    # the planner making technically-legal, visually-wrong cuts.
    cut_direction: SnapPointCutDirection = Field(
        default="both",
        description="Whether this point is usable as an in-point, an out-point, or both. A motion onset is a great in-point and a poor out-point; encoding that asymmetry stops the planner making technically-legal, visually-wrong cuts.",
    )


class TranscriptSegmentWordsItem(ContractModel):
    word: str

    start: RationalTime

    end: RationalTime

    confidence: Confidence | None = Field(default=None)


class TranscriptSegment(ContractModel):
    """
    Speech inside the moment, with word timing. Word timestamps are not a nicety:
    they are the mechanism behind speech-aware trimming and the mid-word-cut quality
    gate.
    """

    text: str

    # BCP-47. Indian-language libraries are a first-class target, so this is required
    # rather than assumed English.
    language: str = Field(
        description="BCP-47. Indian-language libraries are a first-class target, so this is required rather than assumed English.",
    )

    words: list[TranscriptSegmentWordsItem] = Field(default_factory=list)

    # Diarisation labels, mapped to person ids where a confident face-voice
    # association exists.
    speaker_ids: list[str] = Field(
        default_factory=list,
        description="Diarisation labels, mapped to person ids where a confident face-voice association exists.",
    )

    # Someone says a name. A strong and cheap signal that a window matters to the
    # family it belongs to.
    contains_name_mention: bool = Field(
        default=False,
        description="Someone says a name. A strong and cheap signal that a window matters to the family it belongs to.",
    )


class MomentRecordPeople(ContractModel):
    """
    Who is present, resolved through the automated-output face gate. A person named
    here has passed the precision bar; uncertain faces contribute to face_presence
    but not to this list.
    """

    person_ids: list[Uuid] = Field(default_factory=list)

    face_track_ids: list[Uuid] = Field(default_factory=list)

    unidentified_face_count: int = Field(default=0)


class MomentRecord(ContractModel):
    """
    A scored time interval in a video: what happens in it, how good it is, and where
    inside it a cut is allowed to land.
    """

    schema_version: SchemaVersion

    # BLAKE3 over (media_id, source_range, scorer model_id+version). Rescoring with a
    # new model yields new ids, so an EDL always points at the exact moment definition
    # it was planned against.
    moment_id: Blake3Hash = Field(
        description="BLAKE3 over (media_id, source_range, scorer model_id+version). Rescoring with a new model yields new ids, so an EDL always points at the exact moment definition it was planned against.",
    )

    # MediaRecord this moment lives in. For chaptered footage this is the span
    # ASSEMBLY id, so a moment may legally straddle a chapter boundary that the
    # planner never has to know about.
    media_id: Blake3Hash = Field(
        description="MediaRecord this moment lives in. For chaptered footage this is the span ASSEMBLY id, so a moment may legally straddle a chapter boundary that the planner never has to know about.",
    )

    # The moment's extent in SOURCE timecode -- already mapped back through the proxy
    # frame index. Everything downstream, including the EDL, addresses source time;
    # proxy time never escapes the analysis layer.
    source_range: TimeRange = Field(
        description="The moment's extent in SOURCE timecode -- already mapped back through the proxy frame index. Everything downstream, including the EDL, addresses source time; proxy time never escapes the analysis layer.",
    )

    scores: MomentScores

    elimination: Elimination

    # The same interval in proxy time, retained so a re-score can be run against the
    # cached proxy without redoing the mapping.
    proxy_range: TimeRange | None = Field(
        default=None,
        description="The same interval in proxy time, retained so a re-score can be run against the cached proxy without redoing the mapping.",
    )

    # Shot this moment sits inside, from TransNetV2 boundary detection. A moment never
    # crosses a shot boundary -- crossing one is a cut, and cuts belong to the
    # planner.
    shot_id: Slug | None = Field(
        default=None,
        description="Shot this moment sits inside, from TransNetV2 boundary detection. A moment never crosses a shot boundary -- crossing one is a cut, and cuts belong to the planner.",
    )

    features: MomentFeatures | None = Field(default=None)

    # Certified cut positions inside (and at the edges of) this moment. Ordered by
    # time. A beat-locked reel cut is the intersection of a beat grid entry and a snap
    # point of kind motion_onset or audio_onset -- that intersection is what makes a
    # cut feel deliberate rather than arbitrary.
    snap_points: list[SnapPoint] = Field(
        default_factory=list,
        description="Certified cut positions inside (and at the edges of) this moment. Ordered by time. A beat-locked reel cut is the intersection of a beat grid entry and a snap point of kind motion_onset or audio_onset -- that intersection is what makes a ...",
    )

    # The bounds a planner may trim to without damaging the moment. Absent only when
    # speech and motion analysis have not run.
    safe_trim: SafeTrim | None = Field(
        default=None,
        description="The bounds a planner may trim to without damaging the moment. Absent only when speech and motion analysis have not run.",
    )

    # Who is present, resolved through the automated-output face gate. A person named
    # here has passed the precision bar; uncertain faces contribute to face_presence
    # but not to this list.
    people: MomentRecordPeople = Field(
        default_factory=dict,
        description="Who is present, resolved through the automated-output face gate. A person named here has passed the precision bar; uncertain faces contribute to face_presence but not to this list.",
    )

    transcript: TranscriptSegment | None = Field(default=None)

    model_runs: list[ModelRun] = Field(default_factory=list)

    created_at: Timestamp | None = Field(default=None)


class Alternative(ContractModel):
    subject_id: str

    # True for the option that won. Included in the list rather than only in Subject
    # so a single array fully describes the comparison.
    chosen: bool = Field(
        description="True for the option that won. Included in the list rather than only in Subject so a single array fully describes the comparison.",
    )

    # Position as shown, 0-based. Position bias is real and strong; a model trained
    # without it will learn that the top-left option is beautiful.
    presented_rank: int | None = Field(
        default=None,
        description="Position as shown, 0-based. Position bias is real and strong; a model trained without it will learn that the top-left option is beautiful.",
    )

    # The score the system assigned at presentation time. The gap between this and the
    # human's choice is the error signal.
    presented_score: Unit | None = Field(
        default=None,
        description="The score the system assigned at presentation time. The gap between this and the human's choice is the error signal.",
    )

    # Features for this alternative, in the same feature_set_id as the subject's.
    # Required for pairwise training; a comparison where only the winner has features
    # cannot be learned from.
    feature_vector: DenseFeatures | None = Field(
        default=None,
        description="Features for this alternative, in the same feature_set_id as the subject's. Required for pairwise training; a comparison where only the winner has features cannot be learned from.",
    )


class DecisionKind(str, Enum):
    KEPT = "kept"
    REJECTED = "rejected"
    REORDERED = "reordered"
    RECROPPED = "recropped"
    VARIANT_PICKED = "variant_picked"
    HERO_SWAPPED = "hero_swapped"
    REPLACED = "replaced"
    PERSON_CONFIRMED = "person_confirmed"
    PERSON_REJECTED = "person_rejected"
    MOMENT_TRIMMED = "moment_trimmed"
    MUSIC_CHANGED = "music_changed"
    ENHANCEMENT_ACCEPTED = "enhancement_accepted"
    ENHANCEMENT_REJECTED = "enhancement_rejected"
    PAGE_REORDERED = "page_reordered"
    EXPORTED = "exported"
    PRINTED = "printed"
    SHARED = "shared"
    DELETED = "deleted"
    FAVORITED = "favorited"
    REVISION_REQUESTED = "revision_requested"


class DecisionSurface(str, Enum):
    CULLING_UI = "culling_ui"
    LIBRARY_GRID = "library_grid"
    ALBUM_REVIEW = "album_review"
    SPREAD_EDITOR = "spread_editor"
    VARIANT_PICKER = "variant_picker"
    PERSON_LABELING = "person_labeling"
    PROJECT_EDITOR = "project_editor"
    SHARE_FLOW = "share_flow"
    CHECKOUT = "checkout"
    CONCIERGE_REVIEW = "concierge_review"


class Decision(ContractModel):
    # What the human did. Note that `exported`, `printed` and `shared` are included:
    # acting on an output is the strongest positive signal available, far stronger
    # than a thumbs-up, and it costs nothing to capture.
    kind: DecisionKind = Field(
        description="What the human did. Note that `exported`, `printed` and `shared` are included: acting on an output is the strongest positive signal available, far stronger than a thumbs-up, and it costs nothing to capture.",
    )

    # Where in the product it happened. The same `kept` means different things in a
    # culling sweep and in a final album review, and a model that cannot tell them
    # apart will learn the average of two different tastes.
    surface: DecisionSurface = Field(
        description="Where in the product it happened. The same `kept` means different things in a culling sweep and in a final album review, and a model that cannot tell them apart will learn the average of two different tastes.",
    )

    # True when the human deliberately expressed a preference. False for inferred
    # signals such as dwelling on a frame or scrolling past. Inferred events are far
    # weaker evidence and must be weighted accordingly rather than mixed in silently.
    explicit: bool = Field(
        description="True when the human deliberately expressed a preference. False for inferred signals such as dwelling on a frame or scrolling past. Inferred events are far weaker evidence and must be weighted accordingly rather than mixed in silently.",
    )

    # How much to trust an inferred signal. Null for explicit ones, which need no
    # discount.
    confidence: Confidence | None = Field(
        default=None,
        description="How much to trust an inferred signal. Null for explicit ones, which need no discount.",
    )

    # Set when the human undid this within the session. A reversed decision is
    # training data about the reversal, not about the original action, and must never
    # be fed in as a plain positive.
    reversed_at: Timestamp | None = Field(
        default=None,
        description="Set when the human undid this within the session. A reversed decision is training data about the reversal, not about the original action, and must never be fed in as a plain positive.",
    )


class DecisionContextTask(str, Enum):
    CULL = "cull"
    ALBUM = "album"
    REEL = "reel"
    FILM = "film"
    PERSON_LABELING = "person_labeling"
    SEARCH = "search"
    SHARE = "share"
    CONCIERGE = "concierge"


class DecisionContextDeviceClass(str, Enum):
    DESKTOP = "desktop"
    LAPTOP = "laptop"
    TABLET = "tablet"
    PHONE = "phone"


class DecisionContext(ContractModel):
    # What the human was trying to accomplish. Taste is task-relative: a photo
    # rejected for a print album may be perfectly good for a reel.
    task: DecisionContextTask = Field(
        description="What the human was trying to accomplish. Taste is task-relative: a photo rejected for a print album may be perfectly good for a reel.",
    )

    project_id: Uuid | None = Field(default=None)

    # Groups decisions made in one sitting. Decisions late in a long session are
    # noisier -- fatigue is measurable and worth modelling rather than ignoring.
    session_id: Uuid | None = Field(
        default=None,
        description="Groups decisions made in one sitting. Decisions late in a long session are noisier -- fatigue is measurable and worth modelling rather than ignoring.",
    )

    # How many options existed in total, which may exceed the number displayed.
    candidate_set_size: int | None = Field(
        default=None,
        description="How many options existed in total, which may exceed the number displayed.",
    )

    presented_count: int | None = Field(default=None)

    subject_presented_rank: int | None = Field(default=None)

    # Time from presentation to decision. A 400ms rejection and a 30s agonised one are
    # different strengths of evidence.
    deliberation_ms: float | None = Field(
        default=None,
        description="Time from presentation to decision. A 400ms rejection and a 30s agonised one are different strengths of evidence.",
    )

    position_in_session: int | None = Field(default=None)

    # Which ranking model produced the scores the human was reacting to. Without it,
    # an event cannot be attributed to the model that generated the ordering, and
    # offline evaluation becomes guesswork.
    ranker_version: str | None = Field(
        default=None,
        description="Which ranking model produced the scores the human was reacting to. Without it, an event cannot be attributed to the model that generated the ordering, and offline evaluation becomes guesswork.",
    )

    fusion_weights_version: str | None = Field(default=None)

    # Screen size changes what a person can even perceive; a crop judged on a phone is
    # not a crop judged on a 27-inch display.
    device_class: DecisionContextDeviceClass | None = Field(
        default=None,
        description="Screen size changes what a person can even perceive; a crop judged on a phone is not a crop judged on a 27-inch display.",
    )


class DecisionDelta(ContractModel):
    """
    The before and after of an edit. A re-crop is the richest signal the system ever
    receives: the human has not merely judged, they have demonstrated the correct
    answer.
    """

    crop_before: NormalizedBox | None = Field(default=None)

    crop_after: NormalizedBox | None = Field(default=None)

    position_before: int | None = Field(default=None)

    position_after: int | None = Field(default=None)

    trim_before: TimeRange | None = Field(default=None)

    trim_after: TimeRange | None = Field(default=None)

    replaced_with_subject_id: str | None = Field(default=None)

    # For revision_requested: the human's own words, such as 'more of her' or 'less
    # drone'. Free text, local-only, and stripped before any export -- it can contain
    # names.
    instruction_text: str | None = Field(
        default=None,
        description="For revision_requested: the human's own words, such as 'more of her' or 'less drone'. Free text, local-only, and stripped before any export -- it can contain names.",
    )


class DenseFeatures(ContractModel):
    feature_set_id: Slug

    values: list[float]


class FeatureContextPersonContext(ContractModel):
    """
    Whether confirmed people were present, and how many. Who is in a photo is often
    the entire reason it was kept, and this captures that without naming anybody in
    an exportable record -- ids stay local, counts travel.
    """

    confirmed_person_count: int = Field(default=0)

    # True when someone the user has marked as important is present. The single
    # strongest predictor of a keep decision in family libraries.
    includes_priority_person: bool = Field(
        default=False,
        description="True when someone the user has marked as important is present. The single strongest predictor of a keep decision in family libraries.",
    )

    # Local-only. Stripped by the anonymisation pass before any event leaves the
    # device.
    person_ids: list[Uuid] = Field(
        default_factory=list,
        description="Local-only. Stripped by the anonymisation pass before any event leaves the device.",
    )


class FeatureContext(ContractModel):
    """
    The subject's features as they stood at decision time. Both a named map and an
    optional dense vector: the named map keeps the data interpretable and debuggable
    for years, the dense vector keeps training cheap. The named map is the source of
    truth.
    """

    # Names the exact ordered feature list in use. Bumped whenever a feature is added,
    # removed or redefined. A dense vector is meaningless without it.
    feature_set_id: Slug = Field(
        description="Names the exact ordered feature list in use. Bumped whenever a feature is added, removed or redefined. A dense vector is meaningless without it.",
    )

    # Feature name to value. Values are numeric and normalised. Deliberately an open
    # map rather than a fixed property list, because the feature set is expected to
    # grow -- but every key must be declared in the named feature_set_id, and the eval
    # harness checks that.
    named: dict[str, float] = Field(
        description="Feature name to value. Values are numeric and normalised. Deliberately an open map rather than a fixed property list, because the feature set is expected to grow -- but every key must be declared in the named feature_set_id, and the eval...",
    )

    dense: DenseFeatures | None = Field(default=None)

    # Embedding references, not the floats themselves for local storage, and resolved
    # to values only when a training export is built under an explicit consent scope.
    # An embedding is derived from pixels but is not pixels; treating it as sensitive
    # anyway is the conservative reading of the privacy promise.
    embeddings: list[VectorRef] = Field(
        default_factory=list,
        description="Embedding references, not the floats themselves for local storage, and resolved to values only when a training export is built under an explicit consent scope. An embedding is derived from pixels but is not pixels; treating it as sensiti...",
    )

    # The system's own scores as presented. Frozen: never recomputed against a later
    # model.
    scores_at_decision: dict[str, float] = Field(
        default_factory=dict,
        description="The system's own scores as presented. Frozen: never recomputed against a later model.",
    )

    # Whether confirmed people were present, and how many. Who is in a photo is often
    # the entire reason it was kept, and this captures that without naming anybody in
    # an exportable record -- ids stay local, counts travel.
    person_context: FeatureContextPersonContext | None = Field(
        default=None,
        description="Whether confirmed people were present, and how many. Who is in a photo is often the entire reason it was kept, and this captures that without naming anybody in an exportable record -- ids stay local, counts travel.",
    )


class PrivacyEnvelope(ContractModel):
    """
    Who this event belongs to and how far it is allowed to travel. Present on every
    event because the decision about sharing is made once, at write time, and never
    re-litigated by whatever code later reads the record.
    """

    # A per-install salted hash, not a user id and not an email. Sufficient to group
    # one person's events for a per-user model, insufficient to identify them.
    user_pseudonym: Blake3Hash = Field(
        description="A per-install salted hash, not a user id and not an email. Sufficient to group one person's events for a per-user model, insufficient to identify them.",
    )

    # Whether this event may join the anonymised global training pool. Defaults to
    # false in practice: the user opts in, and the absence of a decision is not
    # consent.
    shareable_for_global_model: bool = Field(
        description="Whether this event may join the anonymised global training pool. Defaults to false in practice: the user opts in, and the absence of a decision is not consent.",
    )

    # Required when shareable_for_global_model is true. Same rule as everywhere else
    # -- nothing leaves without a ledger entry.
    consent: ConsentRef | None = Field(
        default=None,
        description="Required when shareable_for_global_model is true. Same rule as everywhere else -- nothing leaves without a ledger entry.",
    )

    # True when the record still holds person ids or free text. Such an event must
    # pass the anonymisation step before export; this flag is what makes that check
    # cheap and unambiguous.
    contains_local_identifiers: bool = Field(
        default=True,
        description="True when the record still holds person ids or free text. Such an event must pass the anonymisation step before export; this flag is what makes that check cheap and unambiguous.",
    )

    # Which redaction pass was applied. Set once the event has been stripped for
    # export.
    anonymization_version: str | None = Field(
        default=None,
        description="Which redaction pass was applied. Set once the event has been stripped for export.",
    )


class SubjectSubjectType(str, Enum):
    MEDIA = "media"
    MOMENT = "moment"
    FACE = "face"
    PERSON = "person"
    PLACEMENT = "placement"
    SPREAD = "spread"
    PAGE = "page"
    EDL_VARIANT = "edl_variant"
    ALBUM = "album"
    ENHANCEMENT_OP = "enhancement_op"
    MUSIC_CUE = "music_cue"


class Subject(ContractModel):
    """
    What was decided about, and crucially what it was decided AGAINST.
    """

    subject_type: SubjectSubjectType

    # Content hash for content-addressed subjects, uuid or slug otherwise. Kept as a
    # string so one field serves every subject type.
    subject_id: str = Field(
        description="Content hash for content-addressed subjects, uuid or slug otherwise. Kept as a string so one field serves every subject type.",
    )

    # The other options on screen when the decision was made, each with the score it
    # was presented with. This is what converts an outcome into a pairwise preference,
    # and it is the single most valuable field in the record. Empty only for decisions
    # with genuinely no alternatives, such as favouriting one photo in a grid.
    alternatives: list[Alternative] = Field(
        default_factory=list,
        description="The other options on screen when the decision was made, each with the score it was presented with. This is what converts an outcome into a pairwise preference, and it is the single most valuable field in the record. Empty only for decisi...",
    )

    # Containing entity: the album a placement is on, the reel a variant belongs to.
    parent_id: str | None = Field(
        default=None,
        description="Containing entity: the album a placement is on, the reel a variant belongs to.",
    )


class PrefEvent(ContractModel):
    """
    One human reaction, captured with the feature context that existed at the moment
    of the decision.
    """

    schema_version: SchemaVersion

    event_id: Uuid

    occurred_at: Timestamp

    decision: Decision

    subject: Subject

    context: DecisionContext

    features: FeatureContext

    privacy: PrivacyEnvelope

    # Structurally false, always. Present as a field rather than as an unwritten rule
    # so that any pipeline stage, any reviewer, and any test can assert the privacy
    # property directly on the record.
    pixel_data_present: Literal[False] = Field(
        description="Structurally false, always. Present as a field rather than as an unwritten rule so that any pipeline stage, any reviewer, and any test can assert the privacy property directly on the record.",
    )

    # What actually changed, for decisions that are an edit rather than a choice: the
    # crop before and after, the position before and after. The edit itself is the
    # label.
    delta: DecisionDelta | None = Field(
        default=None,
        description="What actually changed, for decisions that are an edit rather than a choice: the crop before and after, the position before and after. The edit itself is the label.",
    )


class ClassScores(ContractModel):
    explicit: Unit

    suggestive: Unit

    medical_or_artistic: Unit


class ClassifierPinLoadMode(str, Enum):
    RELEASE = "release"
    DEVELOPMENT = "development"


class ClassifierPin(ContractModel):
    """
    Which model produced these verdicts. A verdict from a model you cannot identify
    is not evidence, and a verdict produced under a different config is a verdict
    about a different decision boundary -- score_threshold 0.3 and 0.5 are different
    classifiers to every consumer.
    """

    model: ModelRef

    ran_at: Timestamp

    # Which gate the host was running under. A verdict produced by a DEVELOPMENT-mode
    # host -- unpinned weights, unverified licence -- must never clear a real
    # publication, and a verifier serving a release sink must refuse it. Recorded
    # rather than assumed, because 'we were only testing' is how unverified weights
    # reach production.
    load_mode: ClassifierPinLoadMode | None = Field(
        default=None,
        description="Which gate the host was running under. A verdict produced by a DEVELOPMENT-mode host -- unpinned weights, unverified licence -- must never clear a real publication, and a verifier serving a release sink must refuse it. Recorded rather th...",
    )


class ClearanceDecision(ContractModel):
    """
    The aggregate, derived from `items` and recomputed by every verifier rather than
    trusted. It is stored so a rejected publication can be explained without re-
    running anything -- not so a reader can skip checking the items.
    """

    # True only when EVERY item is `cleared`, or is `blocked` with a valid override
    # for this sink. One indeterminate item denies the whole publication -- a book is
    # printed as a unit and a share is published as a unit, so partial clearance is
    # not a state either can be in.
    cleared_for_publication: bool = Field(
        description="True only when EVERY item is `cleared`, or is `blocked` with a valid override for this sink. One indeterminate item denies the whole publication -- a book is printed as a unit and a share is published as a unit, so partial clearance is n...",
    )

    item_count: int

    cleared_count: int

    blocked_count: int

    indeterminate_count: int

    denied_reason: str | None = Field(default=None)


class ItemVerdictVerdict(str, Enum):
    CLEARED = "cleared"
    BLOCKED = "blocked"
    INDETERMINATE = "indeterminate"


class ItemVerdictIndeterminateReason(str, Enum):
    NO_RESULT = "no_result"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_UNLOADABLE = "model_unloadable"
    LOAD_GATE_DENIED = "load_gate_denied"
    CONFIG_DIGEST_MISMATCH = "config_digest_mismatch"
    INFERENCE_ERROR = "inference_error"
    INFERENCE_TIMEOUT = "inference_timeout"
    EVIDENCE_STALE = "evidence_stale"
    VERIFIER_EXCEPTION = "verifier_exception"


class ItemVerdict(ContractModel):
    """
    One media id's clearance, bound to the exact bytes that were classified.
    """

    media_id: Blake3Hash

    # The PROXY the classifier actually saw. Not the media id: a proxy can be
    # regenerated -- a better decoder, a corrected orientation, a different size --
    # and a verdict about the old proxy is not evidence about the new one. A verifier
    # must confirm this matches the proxy the publication is built from, or treat the
    # verdict as stale, which is indeterminate, which blocks.
    evidence_id: Blake3Hash = Field(
        description="The PROXY the classifier actually saw. Not the media id: a proxy can be regenerated -- a better decoder, a corrected orientation, a different size -- and a verdict about the old proxy is not evidence about the new one. A verifier must co...",
    )

    # `cleared` is the ONLY value that permits automatic publication.
    #
    # `blocked` means the classifier scored above a threshold. It may be overridden
    # per item by a human, because the classifier does not get a veto over a parent's
    # judgement about their own family.
    #
    # `indeterminate` means nobody knows: no result row, model unavailable or
    # unloadable, load-gate denial, config digest mismatch, inference error or
    # timeout, or evidence that no longer matches. It may NOT be overridden by
    # anything, because 'nobody checked' is not a decision somebody made.
    verdict: ItemVerdictVerdict = Field(
        description="`cleared` is the ONLY value that permits automatic publication. `blocked` means the classifier scored above a threshold. It may be overridden per item by a human, because the classifier does not get a veto over a parent's judgement about...",
    )

    # Per-class probabilities, when the classifier ran. Null on `indeterminate` -- an
    # indeterminate verdict with scores attached is a contradiction, and the
    # conditional below rejects it.
    scores: ClassScores | None = Field(
        default=None,
        description="Per-class probabilities, when the classifier ran. Null on `indeterminate` -- an indeterminate verdict with scores attached is a contradiction, and the conditional below rejects it.",
    )

    # Why nobody knows. Required on `indeterminate`, because 'blocked for an unknown
    # reason' is unactionable and the remedies differ completely: a missing model
    # needs installing, a stale evidence id needs re-running, a digest mismatch needs
    # investigating.
    indeterminate_reason: ItemVerdictIndeterminateReason | None = Field(
        default=None,
        description="Why nobody knows. Required on `indeterminate`, because 'blocked for an unknown reason' is unactionable and the remedies differ completely: a missing model needs installing, a stale evidence id needs re-running, a digest mismatch needs in...",
    )

    # A human decision to publish despite a `blocked` verdict. Permitted ONLY on
    # `blocked`; the conditional below refuses it on `indeterminate`, which is the
    # single most important rule in this file.
    override: Override | None = Field(
        default=None,
        description="A human decision to publish despite a `blocked` verdict. Permitted ONLY on `blocked`; the conditional below refuses it on `indeterminate`, which is the single most important rule in this file.",
    )


class Override(ContractModel):
    """
    A recorded human decision. Attributable on purpose: an override that nobody owns
    is a bypass.
    """

    decided_at: Timestamp

    # Local user identifier. Never a service account, never a config value -- a
    # machine cannot consent on a person's behalf about their own photographs.
    decided_by: str = Field(
        description="Local user identifier. Never a service account, never a config value -- a machine cannot consent on a person's behalf about their own photographs.",
    )

    # `item_and_sink` is the only value, deliberately. There is no 'always allow this
    # photo' and no 'always allow this class': a decision to print a photo in a
    # private family book is not a decision to publish it, and the whole design fails
    # if an override can outlive the publication it was made for.
    scope: Literal["item_and_sink"] = Field(
        description="`item_and_sink` is the only value, deliberately. There is no 'always allow this photo' and no 'always allow this class': a decision to print a photo in a private family book is not a decision to publish it, and the whole design fails if ...",
    )

    note: str | None = Field(default=None)


class Thresholds(ContractModel):
    """
    The decision boundaries actually applied, per class. Recorded rather than
    referenced because the config can change underneath a stored verdict, and a
    verdict whose threshold you cannot reconstruct cannot be re-audited.

    The classes are separate on purpose. Collapsing them into one 'nsfw' bit
    produces the two classic failures: a breastfeeding photo or a post-surgery
    record treated as pornography, and a bikini holiday photo treated as safe for a
    public share. A family library contains all three, and the right handling
    differs for each.
    """

    explicit: Unit

    suggestive: Unit

    medical_or_artistic: Unit


class SafetyClearanceSink(str, Enum):
    PRINT = "print"
    SHARE = "share"
    FRONTIER_EGRESS = "frontier_egress"
    LOCAL_EXPORT = "local_export"


class SafetyClearance(ContractModel):
    """
    The manifest that must exist, verify, and be COMPLETE before anything leaves the
    device or reaches a printer. Designed by Codex on issue #21; this is the
    contract form of it.

    WHY A MANIFEST AND NOT A FIELD ON EACH RECORD

    A per-record `is_safe` flag is checked at some point and acted on at another,
    and the gap between them is where the failure lives: the selection changes, a
    photo is swapped in, and the check that passed was about a different set. So
    clearance is bound to an EXACT publication -- this sink, these media ids, in
    this order, under this classifier and this config digest -- and hashed. The
    renderer or service verifies the hash against the inputs it is ACTUALLY about to
    publish, inside the same operation that creates the export. There is no window
    in which the checked set and the published set can differ.

    THE RULE THAT MATTERS MOST

    Absence is `indeterminate`, and indeterminate BLOCKS. A missing verdict, an
    unloadable classifier, a config digest mismatch, an inference timeout, a stale
    verdict for a proxy that has since changed, a row that simply is not there --
    all of them are indeterminate. Only `cleared` proceeds.

    This is the opposite of how safety checks usually fail. The common shape is a
    check that silently no-ops when its model is missing, so everything downstream
    reads the absence as a pass. This project has already shipped one gate with
    exactly that defect (a model load gate that permitted weights whose hash had
    never been computed), and the fix cost more than building it correctly would
    have.

    WHAT MAY AND MAY NOT BE OVERRIDDEN

    A POSITIVE classifier result may be overridden per item by a human: a parent may
    decide a breastfeeding photo belongs in the family album, and the classifier
    does not get a veto over that. The override is recorded in the manifest with who
    and when, so the decision is attributable.

    A MISSING result may NOT be overridden -- not by a flag, not by a default, not
    by a global bypass, not by an empty override list. 'Nobody checked' and
    'somebody checked and disagreed' are different states, and only the second is a
    decision.
    """

    schema_version: SchemaVersion

    # BLAKE3 over the canonical manifest body, computed exactly as
    # models/policy/digest.py computes a config digest: over the SERIALISED BYTES of
    # this document with `manifest_id` and `decision` removed. Bytes rather than a re-
    # serialisation, because Python writes the float 1.0 as `1.0` and JavaScript
    # writes `1`, and a manifest that verifies in the pipeline and fails in the Rust
    # renderer is a gate that blocks correct output -- which is how gates get
    # disabled.
    manifest_id: Blake3Hash = Field(
        description="BLAKE3 over the canonical manifest body, computed exactly as models/policy/digest.py computes a config digest: over the SERIALISED BYTES of this document with `manifest_id` and `decision` removed. Bytes rather than a re-serialisation, be...",
    )

    # Format version of the manifest itself. A verifier that does not recognise this
    # value MUST DENY rather than attempt a best-effort parse. Deny-by-default on an
    # unknown version is what stops an old renderer from ignoring a field a newer
    # planner added -- and the field it ignores will be the one that was added because
    # something went wrong.
    manifest_version: Literal[1] = Field(
        description="Format version of the manifest itself. A verifier that does not recognise this value MUST DENY rather than attempt a best-effort parse. Deny-by-default on an unknown version is what stops an old renderer from ignoring a field a newer pla...",
    )

    created_at: Timestamp

    # Where this publication is going. Clearance is NOT transferable between sinks: a
    # photo cleared for a private printed book has not thereby been cleared for a
    # public share link, and the thresholds differ. A verifier must check that the
    # sink it is serving matches this value exactly.
    sink: SafetyClearanceSink = Field(
        description="Where this publication is going. Clearance is NOT transferable between sinks: a photo cleared for a private printed book has not thereby been cleared for a public share link, and the thresholds differ. A verifier must check that the sink...",
    )

    classifier: ClassifierPin

    thresholds: Thresholds

    # One entry per media id in the publication, in PUBLICATION ORDER. Order is part
    # of the identity: a manifest whose items match by set but not by order describes
    # a different publication, and a verifier comparing sets rather than sequences
    # would accept a reordered book.
    items: list[ItemVerdict] = Field(
        description="One entry per media id in the publication, in PUBLICATION ORDER. Order is part of the identity: a manifest whose items match by set but not by order describes a different publication, and a verifier comparing sets rather than sequences w...",
    )

    decision: ClearanceDecision

    # Free text naming the specific destination (vendor, recipient scope, model
    # provider) for the audit trail. NEVER parsed, never used to make a decision -- a
    # decision that depends on a free-text field is a decision an attacker can
    # influence.
    sink_detail: str | None = Field(
        default=None,
        description="Free text naming the specific destination (vendor, recipient scope, model provider) for the audit trail. NEVER parsed, never used to make a decision -- a decision that depends on a free-text field is a decision an attacker can influence.",
    )


# Resolve forward references between models.
EnhancementOp.model_rebuild()
EventContextDateRange.model_rebuild()
EventContext.model_rebuild()
FaceSafety.model_rebuild()
LayoutInfoGrid.model_rebuild()
LayoutInfo.model_rebuild()
PageBackground.model_rebuild()
Page.model_rebuild()
PlacementBorder.model_rebuild()
Placement.model_rebuild()
PrintValidationReport.model_rebuild()
RectMm.model_rebuild()
SelectionReportDiversityConstraintsItem.model_rebuild()
SelectionReportRejectedItem.model_rebuild()
SelectionReport.model_rebuild()
SizeMm.model_rebuild()
SpreadHarmonySpreadsItemAdjustmentsItem.model_rebuild()
SpreadHarmonySpreadsItem.model_rebuild()
SpreadHarmony.model_rebuild()
SpreadReviewFindingsItem.model_rebuild()
SpreadReview.model_rebuild()
TextBlock.model_rebuild()
ValidationCheck.model_rebuild()
VendorProfileColorProfile.model_rebuild()
VendorProfilePageCount.model_rebuild()
VendorProfile.model_rebuild()
AlbumSpec.model_rebuild()
AspectRatio.model_rebuild()
ConsentRef.model_rebuild()
Determinism.model_rebuild()
GeoPoint.model_rebuild()
ModelRef.model_rebuild()
ModelRun.model_rebuild()
NormalizedBox.model_rebuild()
PerceptualHash.model_rebuild()
PixelSize.model_rebuild()
Point2D.model_rebuild()
RationalTime.model_rebuild()
Score.model_rebuild()
TimeAssertion.model_rebuild()
TimeRange.model_rebuild()
VectorRef.model_rebuild()
Act.model_rebuild()
AmbientPlanPerClipGainDbItem.model_rebuild()
AmbientPlan.model_rebuild()
AudioPlan.model_rebuild()
Beat.model_rebuild()
BeatGridTimeSignature.model_rebuild()
BeatGrid.model_rebuild()
BeatLock.model_rebuild()
Clip.model_rebuild()
ClipAudio.model_rebuild()
ColorOp.model_rebuild()
ColorPipeline.model_rebuild()
DuckingRule.model_rebuild()
EdlValidationChecksItem.model_rebuild()
EdlValidation.model_rebuild()
Gap.model_rebuild()
Marker.model_rebuild()
MediaRef.model_rebuild()
MixPlan.model_rebuild()
MusicCue.model_rebuild()
MusicLicense.model_rebuild()
OtioExportInfo.model_rebuild()
ReframeKeyframe.model_rebuild()
ReframeSmoothing.model_rebuild()
ReframeTrack.model_rebuild()
RenderTarget.model_rebuild()
StoryArcEnergyCurveItem.model_rebuild()
StoryArc.model_rebuild()
StoryBeat.model_rebuild()
SubjectLock.model_rebuild()
TimeEffect.model_rebuild()
Track.model_rebuild()
Transition.model_rebuild()
VariantInfo.model_rebuild()
EDL.model_rebuild()
ClusterMembership.model_rebuild()
Detection.model_rebuild()
FaceAttributes.model_rebuild()
FaceTrack.model_rebuild()
IdentityCandidatesItem.model_rebuild()
Identity.model_rebuild()
Landmarks.model_rebuild()
SensitiveFlags.model_rebuild()
FaceRecord.model_rebuild()
Checkpoint.model_rebuild()
EgressDeclaration.model_rebuild()
JobError.model_rebuild()
JobInputs.model_rebuild()
JobOutput.model_rebuild()
JobRequirements.model_rebuild()
JobState.model_rebuild()
JournalEntriesItem.model_rebuild()
Journal.model_rebuild()
Progress.model_rebuild()
RetryPolicy.model_rebuild()
JobSpec.model_rebuild()
AudioStream.model_rebuild()
Capture.model_rebuild()
ContentAnalysisTagsItem.model_rebuild()
ContentAnalysisOcr.model_rebuild()
ContentAnalysis.model_rebuild()
DedupeMembership.model_rebuild()
DeviceInfo.model_rebuild()
ErrorInfo.model_rebuild()
ExclusionState.model_rebuild()
ExposureInfo.model_rebuild()
FaceSummary.model_rebuild()
FrameIndexSidecar.model_rebuild()
ImageProperties.model_rebuild()
PerceptualFingerprintKeyframeHashesItem.model_rebuild()
PerceptualFingerprint.model_rebuild()
ProcessingStateStages.model_rebuild()
ProcessingState.model_rebuild()
ProxyRef.model_rebuild()
QualityScores.model_rebuild()
SafetyAssessment.model_rebuild()
SourceLocation.model_rebuild()
Span.model_rebuild()
StageState.model_rebuild()
UserAnnotations.model_rebuild()
VideoPropertiesFrameRate.model_rebuild()
VideoProperties.model_rebuild()
MediaRecord.model_rebuild()
AudioFeaturesEventsItem.model_rebuild()
AudioFeatures.model_rebuild()
Elimination.model_rebuild()
MomentFeatures.model_rebuild()
MomentScores.model_rebuild()
SafeTrim.model_rebuild()
SnapPoint.model_rebuild()
TranscriptSegmentWordsItem.model_rebuild()
TranscriptSegment.model_rebuild()
MomentRecordPeople.model_rebuild()
MomentRecord.model_rebuild()
Alternative.model_rebuild()
Decision.model_rebuild()
DecisionContext.model_rebuild()
DecisionDelta.model_rebuild()
DenseFeatures.model_rebuild()
FeatureContextPersonContext.model_rebuild()
FeatureContext.model_rebuild()
PrivacyEnvelope.model_rebuild()
Subject.model_rebuild()
PrefEvent.model_rebuild()
ClassScores.model_rebuild()
ClassifierPin.model_rebuild()
ClearanceDecision.model_rebuild()
ItemVerdict.model_rebuild()
Override.model_rebuild()
Thresholds.model_rebuild()
SafetyClearance.model_rebuild()


#: Root contract types, keyed by schema title.
ROOT_MODELS: dict[str, type[ContractModel]] = {
    "AlbumSpec": AlbumSpec,
    "EDL": EDL,
    "FaceRecord": FaceRecord,
    "JobSpec": JobSpec,
    "MediaRecord": MediaRecord,
    "MomentRecord": MomentRecord,
    "PrefEvent": PrefEvent,
    "SafetyClearance": SafetyClearance,
}

__all__ = [
    "ContractModel",
    "ROOT_MODELS",
    "EnhancementOpKind",
    "EnhancementOp",
    "EventContextDateRange",
    "EventContext",
    "FaceSafety",
    "LayoutInfoSolver",
    "LayoutInfoGrid",
    "LayoutInfo",
    "PageSide",
    "PageBackgroundKind",
    "PageBackground",
    "Page",
    "PlacementBleedsItem",
    "PlacementBorder",
    "Placement",
    "PrintValidationReportStatus",
    "PrintValidationReport",
    "RectMm",
    "SelectionReportDiversityConstraintsItemConstraint",
    "SelectionReportDiversityConstraintsItem",
    "SelectionReportRejectedItemReason",
    "SelectionReportRejectedItem",
    "SelectionReport",
    "SizeMm",
    "SpreadHarmonySpreadsItemAdjustmentsItem",
    "SpreadHarmonySpreadsItem",
    "SpreadHarmony",
    "SpreadReviewStatus",
    "SpreadReviewFindingsItemKind",
    "SpreadReviewFindingsItemSeverity",
    "SpreadReviewFindingsItemResolution",
    "SpreadReviewFindingsItem",
    "SpreadReview",
    "TextBlockRole",
    "TextBlockAlignment",
    "TextBlock",
    "ValidationCheckCheckId",
    "ValidationCheckSeverity",
    "ValidationCheck",
    "VendorProfileColorProfileIntent",
    "VendorProfileColorProfile",
    "VendorProfilePageCount",
    "VendorProfileBinding",
    "VendorProfilePdfStandard",
    "VendorProfile",
    "AlbumSpec",
    "AspectRatio",
    "Blake3Hash",
    "Confidence",
    "ConsentRefScope",
    "ConsentRef",
    "Determinism",
    "GeoPointSource",
    "GeoPoint",
    "LocalDateTime",
    "ModelRefPrecision",
    "ModelRef",
    "ModelRun",
    "NormalizedBox",
    "PerceptualHashAlgorithm",
    "PerceptualHashBits",
    "PerceptualHash",
    "PixelSize",
    "Point2D",
    "RationalTime",
    "RuntimeTarget",
    "SchemaVersion",
    "Score",
    "Slug",
    "TimeAssertionPrecision",
    "TimeAssertion",
    "TimeRange",
    "TimeSource",
    "Timestamp",
    "Unit",
    "Uuid",
    "VectorRefStorage",
    "VectorRefQuantization",
    "VectorRef",
    "VectorSpace",
    "Act",
    "AmbientPlanNoiseSuppression",
    "AmbientPlanPerClipGainDbItem",
    "AmbientPlan",
    "AudioPlan",
    "BeatSection",
    "Beat",
    "BeatGridTimeSignatureBeatUnit",
    "BeatGridTimeSignature",
    "BeatGrid",
    "BeatLock",
    "Clip",
    "ClipAudio",
    "ColorOpOp",
    "ColorOp",
    "ColorPipelineWorkingSpace",
    "ColorPipeline",
    "DuckingRuleTarget",
    "DuckingRuleTrigger",
    "DuckingRule",
    "EdlValidationStatus",
    "EdlValidationChecksItemCheckId",
    "EdlValidationChecksItemSeverity",
    "EdlValidationChecksItem",
    "EdlValidation",
    "GapFill",
    "Gap",
    "MarkerColor",
    "MarkerKind",
    "Marker",
    "MediaRefMediaKind",
    "MediaRef",
    "MixPlanChannels",
    "MixPlanSampleRate",
    "MixPlan",
    "MusicCue",
    "MusicLicenseProvider",
    "MusicLicenseLicenseType",
    "MusicLicenseClearedForItem",
    "MusicLicense",
    "OtioExportInfo",
    "ReframeKeyframeInterpolation",
    "ReframeKeyframe",
    "ReframeSmoothingMethod",
    "ReframeSmoothing",
    "ReframeTrackFallback",
    "ReframeTrack",
    "RenderTargetDestination",
    "RenderTarget",
    "StoryArcTemplate",
    "StoryArcEnergyCurveItem",
    "StoryArcSource",
    "StoryArc",
    "StoryBeat",
    "SubjectLockSource",
    "SubjectLockKeepInFrame",
    "SubjectLock",
    "TimeEffectKind",
    "TimeEffectAudioHandling",
    "TimeEffect",
    "TrackKind",
    "TrackRole",
    "Track",
    "TransitionTransitionType",
    "TransitionEasing",
    "Transition",
    "VariantInfoStrategy",
    "VariantInfo",
    "EDLKind",
    "EDL",
    "ClusterMembershipMethod",
    "ClusterMembership",
    "DetectionDetectedOn",
    "Detection",
    "FaceAttributes",
    "FaceTrack",
    "IdentityAssignment",
    "IdentityThresholdProfile",
    "IdentityCandidatesItem",
    "IdentityReviewReason",
    "IdentityDecidedBy",
    "Identity",
    "LandmarksScheme",
    "Landmarks",
    "SensitiveFlagsMinorStatus",
    "SensitiveFlags",
    "FaceRecord",
    "Checkpoint",
    "EgressDeclarationDestination",
    "EgressDeclarationPayloadKind",
    "EgressDeclaration",
    "JobErrorCode",
    "JobError",
    "JobInputs",
    "JobOutputKind",
    "JobOutput",
    "JobRequirementsCompute",
    "JobRequirements",
    "JobStateStatus",
    "JobState",
    "JournalEntriesItemAction",
    "JournalEntriesItem",
    "Journal",
    "ProgressUnit",
    "Progress",
    "RetryPolicyBackoff",
    "RetryPolicy",
    "JobSpecJobType",
    "JobSpec",
    "AudioStream",
    "CaptureMetadataPresentItem",
    "Capture",
    "ContentAnalysisTagsItemSource",
    "ContentAnalysisTagsItem",
    "ContentAnalysisSceneType",
    "ContentAnalysisOcr",
    "ContentAnalysis",
    "DedupeMembershipMethod",
    "DedupeMembership",
    "DeviceInfo",
    "ErrorInfo",
    "ExclusionStateReasonsItem",
    "ExclusionState",
    "ExposureInfo",
    "FaceSummary",
    "FrameIndexSidecarMapping",
    "FrameIndexSidecar",
    "ImagePropertiesColorSpace",
    "ImageProperties",
    "PerceptualFingerprintKeyframeHashesItem",
    "PerceptualFingerprint",
    "ProcessingStateState",
    "ProcessingStateStages",
    "ProcessingState",
    "ProxyRefKind",
    "ProxyRef",
    "QualityScores",
    "SafetyAssessmentCategoriesItem",
    "SafetyAssessment",
    "SourceLocationAdapter",
    "SourceLocation",
    "SpanRole",
    "SpanSpanKind",
    "SpanContinuity",
    "Span",
    "StageStateStatus",
    "StageState",
    "UserAnnotations",
    "VideoPropertiesRotationDeg",
    "VideoPropertiesFrameRate",
    "VideoProperties",
    "MediaRecordAssetKind",
    "MediaRecordKind",
    "MediaRecordFileFormat",
    "MediaRecord",
    "AudioFeaturesEventsItemLabel",
    "AudioFeaturesEventsItem",
    "AudioFeatures",
    "EliminationReasonsItem",
    "EliminationStage",
    "Elimination",
    "MomentFeatures",
    "MomentScoresSource",
    "MomentScores",
    "SafeTrim",
    "SnapPointKind",
    "SnapPointCutDirection",
    "SnapPoint",
    "TranscriptSegmentWordsItem",
    "TranscriptSegment",
    "MomentRecordPeople",
    "MomentRecord",
    "Alternative",
    "DecisionKind",
    "DecisionSurface",
    "Decision",
    "DecisionContextTask",
    "DecisionContextDeviceClass",
    "DecisionContext",
    "DecisionDelta",
    "DenseFeatures",
    "FeatureContextPersonContext",
    "FeatureContext",
    "PrivacyEnvelope",
    "SubjectSubjectType",
    "Subject",
    "PrefEvent",
    "ClassScores",
    "ClassifierPinLoadMode",
    "ClassifierPin",
    "ClearanceDecision",
    "ItemVerdictVerdict",
    "ItemVerdictIndeterminateReason",
    "ItemVerdict",
    "Override",
    "Thresholds",
    "SafetyClearanceSink",
    "SafetyClearance",
]
