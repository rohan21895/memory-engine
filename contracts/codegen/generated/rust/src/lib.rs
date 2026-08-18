//! GENERATED FILE -- DO NOT EDIT.
//!
//! Produced by contracts/codegen/generate.py from contracts/schemas/*.schema.json.
//! Edit the schemas and re-run `npm run codegen`. CI fails if these files drift
//! from the schemas (see scripts/ci/check-codegen-freshness.mjs).

#![allow(clippy::all)]

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EnhancementOpKind {
    #[serde(rename = "denoise")]
    Denoise,

    #[serde(rename = "upscale")]
    Upscale,

    #[serde(rename = "face_restore")]
    FaceRestore,

    #[serde(rename = "sharpen")]
    Sharpen,

    #[serde(rename = "exposure")]
    Exposure,

    #[serde(rename = "white_balance")]
    WhiteBalance,

    #[serde(rename = "color_transfer")]
    ColorTransfer,

    #[serde(rename = "spread_harmonize")]
    SpreadHarmonize,

    #[serde(rename = "outpaint_to_fit")]
    OutpaintToFit,

    #[serde(rename = "straighten")]
    Straighten,

    #[serde(rename = "perspective_correct")]
    PerspectiveCorrect,

    #[serde(rename = "dust_removal")]
    DustRemoval,
}

/// One planned image improvement. `license_cleared` is required and defaults to
/// nothing: half the popular restoration models are non-commercial (CodeFormer S-Lab,
/// FLUX.1-dev), and the contract is where that gets caught rather than discovered at
/// launch.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EnhancementOp {
    pub op_id: Slug,

    pub kind: EnhancementOpKind,

    /// Execution order within the placement. Explicit integers rather than array position
    /// so a re-plan can insert an op without renumbering the world.
    pub order: i64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelRef>,

    /// Whether the model behind this op passed the licence audit for commercial use. False
    /// must block export -- an unlicensed enhancement is a legal defect that ships inside a
    /// physical book.
    pub license_cleared: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parameters: Option<BTreeMap<String, serde_json::Value>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub strength: Option<Unit>,

    /// Why the op was planned: 'source is 1600px on a 300mm edge'. Feeds the user-facing
    /// explanation and the review queue.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EventContextDateRange {
    pub start: Timestamp,

    pub end: Timestamp,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EventContext {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub event_cluster_id: Option<Uuid>,

    /// Human-readable event name, typically produced by a Tier 3 pass over contact sheets:
    /// 'beach day', 'night market'.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub label: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub date_range: Option<EventContextDateRange>,

    /// People the album is about. Only ids that passed the automated-output face gate
    /// appear here.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub person_ids: Option<Vec<Uuid>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub place_label: Option<String>,
}

/// Where the faces ended up after cropping and placement. Computed by the album engine
/// and checked by the render worker -- a face in the trim zone or the gutter is a hard
/// export block.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FaceSafety {
    pub face_count: i64,

    pub all_faces_in_safe_zone: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub faces_in_gutter: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub faces_in_trim_zone: Option<i64>,

    /// Distance from the nearest face to the nearest unsafe boundary. Negative means a face
    /// has already crossed it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_face_margin_mm: Option<f64>,

    /// Faces the crop cut through. Sometimes deliberate on a background figure, never
    /// acceptable on a subject -- so it is recorded rather than merely prevented.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cropped_face_ids: Option<Vec<Blake3Hash>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum LayoutInfoSolver {
    #[serde(rename = "constraint_solver")]
    ConstraintSolver,

    #[serde(rename = "template")]
    Template,

    #[serde(rename = "manual")]
    Manual,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LayoutInfoGrid {
    pub columns: i64,

    pub rows: i64,

    pub gutter_mm: f64,
}

/// How the page arrangement was arrived at. Layout is constraint solving, not template
/// filling (build plan 4.6), so the record is of a solver run rather than of a chosen
/// template id.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LayoutInfo {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub solver: Option<LayoutInfoSolver>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub template_id: Option<Slug>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub grid: Option<LayoutInfoGrid>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub constraints_satisfied: Option<Vec<String>>,

    /// Soft constraints the solver had to give up on, and which therefore deserve a human
    /// glance. Recording them is the difference between a solver that reports its
    /// compromises and one that hides them.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub constraints_relaxed: Option<Vec<String>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub solver_cost: Option<f64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum PageSide {
    #[serde(rename = "left")]
    Left,

    #[serde(rename = "right")]
    Right,

    #[serde(rename = "single")]
    Single,

    #[serde(rename = "front_cover")]
    FrontCover,

    #[serde(rename = "back_cover")]
    BackCover,

    #[serde(rename = "inside_flap")]
    InsideFlap,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum PageBackgroundKind {
    #[serde(rename = "solid")]
    Solid,

    #[serde(rename = "none")]
    None,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PageBackground {
    pub kind: PageBackgroundKind,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub color_hex: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Page {
    pub page_index: i64,

    /// Pages sharing a spread_id are viewed together and are colour-harmonised together.
    /// Null for a cover or a single page.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub spread_id: Option<Slug>,

    pub side: PageSide,

    /// Narrative section this page belongs to, used by the diversity constraints
    /// (people/scenery/detail balance per section).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub section_id: Option<Slug>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub background: Option<PageBackground>,

    pub placements: Vec<Placement>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub text_blocks: Option<Vec<TextBlock>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub layout: Option<LayoutInfo>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum PlacementBleedsItem {
    #[serde(rename = "top")]
    Top,

    #[serde(rename = "bottom")]
    Bottom,

    #[serde(rename = "left")]
    Left,

    #[serde(rename = "right")]
    Right,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlacementBorder {
    pub width_mm: f64,

    pub color_hex: String,
}

/// One photo on one page. Carries the mm frame it occupies, the normalised crop taken
/// from the source, and the effective DPI that results -- the three numbers the print
/// validator reasons about.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Placement {
    pub placement_id: Slug,

    pub media_id: Blake3Hash,

    /// Where it sits on the page, in mm from the bleed-box origin.
    pub frame: RectMm,

    /// The region of the SOURCE image used, in normalised oriented-image coordinates. Its
    /// aspect ratio must match the frame's, or the renderer would have to decide how to
    /// reconcile them -- and the renderer decides nothing.
    pub crop: NormalizedBox,

    /// Computed as (cropped source pixels along an edge) / (printed length of that edge in
    /// inches). Compared against vendor_profile.dpi_floor by the hard validator. The whole
    /// reason geometry is in mm.
    pub effective_dpi: f64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub z_index: Option<i64>,

    /// Which page edges this placement runs off. A full-bleed photo must extend past the
    /// trim by the profile's bleed_mm on every edge listed here.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bleeds: Option<Vec<PlacementBleedsItem>>,

    /// The anchor image of its spread. Heroes get the DPI headroom and the composition
    /// attention; supporting images fill around them.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_hero: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_safety: Option<FaceSafety>,

    /// Ordered ops applied to this image before composition. Order matters: denoise before
    /// upscale, upscale before face restore.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub enhancement_ops: Option<Vec<EnhancementOp>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub caption: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub border: Option<PlacementBorder>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum PrintValidationReportStatus {
    #[serde(rename = "pass")]
    Pass,

    #[serde(rename = "fail")]
    Fail,

    #[serde(rename = "not_run")]
    NotRun,
}

/// THE HARD GATE. workers/render-print refuses to export a PDF unless status is 'pass'.
/// There is no override flag by design (AGENTS.md): a print defect cannot be patched
/// after the book is in the post, so the only safe place to fail is before the PDF
/// exists.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PrintValidationReport {
    pub status: PrintValidationReportStatus,

    pub checks: Vec<ValidationCheck>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validated_at: Option<Timestamp>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validator_version: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error_count: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub warning_count: Option<i64>,
}

/// A rectangle on the page in millimetres. Origin is the top-left of the BLEED box, not
/// the trim box, so a full-bleed placement has negative-free coordinates and the
/// renderer never has to guess which origin a number is relative to.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RectMm {
    pub x_mm: f64,

    pub y_mm: f64,

    pub width_mm: f64,

    pub height_mm: f64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rotation_deg: Option<f64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SelectionReportDiversityConstraintsItemConstraint {
    #[serde(rename = "no_near_duplicates_on_spread")]
    NoNearDuplicatesOnSpread,

    #[serde(rename = "people_scenery_detail_balance")]
    PeopleSceneryDetailBalance,

    #[serde(rename = "max_per_person_per_section")]
    MaxPerPersonPerSection,

    #[serde(rename = "chronological_within_section")]
    ChronologicalWithinSection,

    #[serde(rename = "no_consecutive_same_scene")]
    NoConsecutiveSameScene,

    #[serde(rename = "min_hero_quality")]
    MinHeroQuality,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SelectionReportDiversityConstraintsItem {
    pub constraint: SelectionReportDiversityConstraintsItemConstraint,

    pub satisfied: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SelectionReportRejectedItemReason {
    #[serde(rename = "near_duplicate")]
    NearDuplicate,

    #[serde(rename = "below_quality_floor")]
    BelowQualityFloor,

    #[serde(rename = "eyes_closed")]
    EyesClosed,

    #[serde(rename = "excluded_content")]
    ExcludedContent,

    #[serde(rename = "diversity_constraint")]
    DiversityConstraint,

    #[serde(rename = "no_space")]
    NoSpace,

    #[serde(rename = "person_not_confirmed")]
    PersonNotConfirmed,

    #[serde(rename = "dpi_too_low")]
    DpiTooLow,

    #[serde(rename = "user_hidden")]
    UserHidden,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SelectionReportRejectedItem {
    pub media_id: Blake3Hash,

    pub reason: SelectionReportRejectedItemReason,
}

/// Why these photos and not others. Kept with the spec because 'why is my best photo
/// missing' is the most common question a user will ever ask about an album.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SelectionReport {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub candidate_count: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub selected_count: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub diversity_constraints: Option<Vec<SelectionReportDiversityConstraintsItem>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rejected: Option<Vec<SelectionReportRejectedItem>>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SizeMm {
    pub width_mm: f64,

    pub height_mm: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SpreadHarmonySpreadsItemAdjustmentsItem {
    pub placement_id: Slug,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exposure_ev_delta: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temperature_k_delta: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tint_delta: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub saturation_delta: Option<f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SpreadHarmonySpreadsItem {
    pub spread_id: Slug,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_temperature_k: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_exposure_ev: Option<f64>,

    /// Per-placement deltas the solver settled on. Deltas rather than absolutes so the
    /// original image data stays the reference.
    pub adjustments: Vec<SpreadHarmonySpreadsItemAdjustmentsItem>,
}

/// Colour and exposure solved jointly across facing pages rather than per image. No
/// consumer tool does this, and the difference is instantly visible in print: two
/// photos of the same afternoon that disagree about white balance look like a mistake
/// when they are 30cm apart on the same sheet.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SpreadHarmony {
    pub enabled: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub spreads: Option<Vec<SpreadHarmonySpreadsItem>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SpreadReviewStatus {
    #[serde(rename = "not_run")]
    NotRun,

    #[serde(rename = "passed")]
    Passed,

    #[serde(rename = "issues_found")]
    IssuesFound,

    #[serde(rename = "issues_fixed")]
    IssuesFixed,

    #[serde(rename = "needs_human")]
    NeedsHuman,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SpreadReviewFindingsItemKind {
    #[serde(rename = "crop_hits_face")]
    CropHitsFace,

    #[serde(rename = "near_identical_pair")]
    NearIdenticalPair,

    #[serde(rename = "color_clash")]
    ColorClash,

    #[serde(rename = "exposure_mismatch")]
    ExposureMismatch,

    #[serde(rename = "weak_hero")]
    WeakHero,

    #[serde(rename = "cluttered_spread")]
    ClutteredSpread,

    #[serde(rename = "awkward_crop")]
    AwkwardCrop,

    #[serde(rename = "text_overlaps_subject")]
    TextOverlapsSubject,

    #[serde(rename = "eyes_closed")]
    EyesClosed,

    #[serde(rename = "orientation_mismatch")]
    OrientationMismatch,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SpreadReviewFindingsItemSeverity {
    #[serde(rename = "error")]
    Error,

    #[serde(rename = "warning")]
    Warning,

    #[serde(rename = "info")]
    Info,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SpreadReviewFindingsItemResolution {
    #[serde(rename = "recropped")]
    Recropped,

    #[serde(rename = "replaced")]
    Replaced,

    #[serde(rename = "reordered")]
    Reordered,

    #[serde(rename = "harmonized")]
    Harmonized,

    #[serde(rename = "removed")]
    Removed,

    #[serde(rename = "accepted_as_is")]
    AcceptedAsIs,

    #[serde(rename = "escalated_to_human")]
    EscalatedToHuman,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SpreadReviewFindingsItem {
    pub finding_id: Slug,

    pub kind: SpreadReviewFindingsItemKind,

    pub severity: SpreadReviewFindingsItemSeverity,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub spread_id: Option<Slug>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub placement_ids: Option<Vec<Slug>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub comment: Option<String>,

    pub resolved: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resolution: Option<SpreadReviewFindingsItemResolution>,
}

/// The automated QA pass: render each spread at low resolution, ask a frontier model
/// for a structured critique, fix, re-check. This is what makes unattended output
/// trustworthy, and like every Tier 3 call it sees only low-res renders and returns
/// only structured decisions against ids.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SpreadReview {
    pub status: SpreadReviewStatus,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelRef>,

    /// Required whenever the review ran in the cloud, because low-res spread renders left
    /// the device.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub consent: Option<ConsentRef>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prompt_id: Option<Slug>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub iterations: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub findings: Option<Vec<SpreadReviewFindingsItem>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TextBlockRole {
    #[serde(rename = "title")]
    Title,

    #[serde(rename = "subtitle")]
    Subtitle,

    #[serde(rename = "caption")]
    Caption,

    #[serde(rename = "date")]
    Date,

    #[serde(rename = "page_number")]
    PageNumber,

    #[serde(rename = "quote")]
    Quote,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TextBlockAlignment {
    #[serde(rename = "left")]
    Left,

    #[serde(rename = "center")]
    Center,

    #[serde(rename = "right")]
    Right,

    #[serde(rename = "justify")]
    Justify,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TextBlock {
    pub block_id: Slug,

    pub text: String,

    pub frame: RectMm,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub role: Option<TextBlockRole>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub font_family: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub font_size_pt: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub color_hex: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub alignment: Option<TextBlockAlignment>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ValidationCheckCheckId {
    #[serde(rename = "dpi_floor")]
    DpiFloor,

    #[serde(rename = "face_in_trim_zone")]
    FaceInTrimZone,

    #[serde(rename = "bleed_coverage")]
    BleedCoverage,

    #[serde(rename = "color_profile_match")]
    ColorProfileMatch,

    #[serde(rename = "face_in_gutter")]
    FaceInGutter,

    #[serde(rename = "page_count_valid")]
    PageCountValid,

    #[serde(rename = "placement_within_page")]
    PlacementWithinPage,

    #[serde(rename = "crop_aspect_matches_frame")]
    CropAspectMatchesFrame,

    #[serde(rename = "no_duplicate_on_spread")]
    NoDuplicateOnSpread,

    #[serde(rename = "text_within_safe_margin")]
    TextWithinSafeMargin,

    #[serde(rename = "enhancement_license_cleared")]
    EnhancementLicenseCleared,

    #[serde(rename = "source_media_available")]
    SourceMediaAvailable,

    #[serde(rename = "pdf_standard_supported")]
    PdfStandardSupported,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ValidationCheckSeverity {
    #[serde(rename = "error")]
    Error,

    #[serde(rename = "warning")]
    Warning,

    #[serde(rename = "info")]
    Info,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ValidationCheck {
    /// The first four are the hard gates named in the build plan: DPI floor, face in trim
    /// zone, bleed violation, mismatched colour profile. Any of them failing blocks export
    /// outright.
    pub check_id: ValidationCheckCheckId,

    pub severity: ValidationCheckSeverity,

    pub passed: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub page_index: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub placement_id: Option<Slug>,

    /// What was actually measured, e.g. 214.7 for a DPI check.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub measured_value: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub required_value: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,

    /// What would fix it: 'upscale source' or 'reduce frame to 180mm'. The review UI shows
    /// this, and the album engine can often act on it automatically and re-validate.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub remediation: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum VendorProfileColorProfileIntent {
    #[serde(rename = "perceptual")]
    Perceptual,

    #[serde(rename = "relative_colorimetric")]
    RelativeColorimetric,

    #[serde(rename = "saturation")]
    Saturation,

    #[serde(rename = "absolute_colorimetric")]
    AbsoluteColorimetric,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct VendorProfileColorProfile {
    pub icc_name: String,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub icc_hash: Option<Blake3Hash>,

    pub intent: VendorProfileColorProfileIntent,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct VendorProfilePageCount {
    pub minimum: i64,

    pub maximum: i64,

    /// Pages are added in physical sheets, so a book is typically constrained to multiples
    /// of 2 or 4. A spec with a page count off the increment is rejected by the printer,
    /// not by us -- so we reject it first.
    pub increment: i64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum VendorProfileBinding {
    #[serde(rename = "layflat")]
    Layflat,

    #[serde(rename = "perfect_bound")]
    PerfectBound,

    #[serde(rename = "saddle_stitch")]
    SaddleStitch,

    #[serde(rename = "spiral")]
    Spiral,

    #[serde(rename = "hardcover_case")]
    HardcoverCase,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum VendorProfilePdfStandard {
    #[serde(rename = "pdf_x_1a")]
    PdfX1a,

    #[serde(rename = "pdf_x_3")]
    PdfX3,

    #[serde(rename = "pdf_x_4")]
    PdfX4,

    #[serde(rename = "pdf_1_6")]
    Pdf16,
}

/// The printer's physical spec sheet, transcribed. Built to one real vendor first
/// (build plan 4.6) because a validator built against an imagined spec validates
/// nothing.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct VendorProfile {
    pub vendor_id: Slug,

    pub product_id: Slug,

    /// Vendors change their spec sheets. A spec validated against v1 is not automatically
    /// valid against v2, and the version pin is what makes that detectable.
    pub profile_version: String,

    pub trim_size_mm: SizeMm,

    /// How far artwork must extend beyond the trim line. Under-bleeding produces a white
    /// sliver on the finished edge.
    pub bleed_mm: f64,

    /// Inset from trim within which nothing important may sit, because guillotines drift.
    pub safe_margin_mm: f64,

    /// Dead zone at the spine. On a perfect-bound book this can swallow 10mm+ of a spread,
    /// which is why a face landing in it is a hard failure rather than a warning.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gutter_mm: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub spine_mm: Option<f64>,

    /// Minimum effective resolution AT PRINTED SIZE. The whole point of the phrase: a 24MP
    /// photo blown across a full spread can still fall below this.
    pub dpi_floor: f64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dpi_preferred: Option<f64>,

    pub color_profile: VendorProfileColorProfile,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub page_count: Option<VendorProfilePageCount>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binding: Option<VendorProfileBinding>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub paper_stock: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pdf_standard: Option<VendorProfilePdfStandard>,
}

/// The deterministic plan for one printed album: which photos, on which pages, cropped
/// how, enhanced how, against which vendor's physical spec.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AlbumSpec {
    pub schema_version: SchemaVersion,

    /// BLAKE3 over the canonical JSON of this spec with volatile fields removed. Two specs
    /// with the same id produce the same PDF.
    pub album_id: Blake3Hash,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subtitle: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub event: Option<EventContext>,

    pub vendor_profile: VendorProfile,

    /// Ordered pages. Page 0 is the front cover when the vendor profile includes one.
    /// Spreads are expressed by pairing pages via spread_id rather than by modelling a
    /// spread as a single wide page, because the gutter falls between two physically
    /// separate sheets and each has its own safe zone.
    pub pages: Vec<Page>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub selection: Option<SelectionReport>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub spread_harmony: Option<SpreadHarmony>,

    pub determinism: Determinism,

    pub validation: PrintValidationReport,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub review: Option<SpreadReview>,
}

/// Exact aspect ratio as integers, e.g. 9:16 for a reel. Integers rather than a float
/// so 'is this 16:9' is an equality test, not an epsilon comparison.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AspectRatio {
    pub numerator: i64,

    pub denominator: i64,
}

/// Lowercase hex BLAKE3-256 digest. The universal content address: same bytes anywhere
/// in the world produce the same id, which is what makes every job idempotent.
pub type Blake3Hash = String;

/// Calibrated probability in [0,1]. Distinct from Unit by intent: a Confidence is
/// expected to be calibrated against a validation set and is therefore comparable to a
/// threshold. A Unit is merely ordered.
pub type Confidence = f64;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ConsentRefScope {
    #[serde(rename = "tier3_contact_sheet")]
    Tier3ContactSheet,

    #[serde(rename = "cloud_render")]
    CloudRender,

    #[serde(rename = "cloud_backup")]
    CloudBackup,

    #[serde(rename = "share_link")]
    ShareLink,

    #[serde(rename = "print_order")]
    PrintOrder,

    #[serde(rename = "minor_face_labeling")]
    MinorFaceLabeling,

    #[serde(rename = "anonymized_preference_training")]
    AnonymizedPreferenceTraining,
}

/// Pointer into the consent ledger owned by services/api. Required on anything that
/// leaves the device or touches a child's face. Hard rule: no network egress without a
/// ledger entry, verified by the CI egress test.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ConsentRef {
    pub ledger_entry_id: Uuid,

    pub scope: ConsentRefScope,

    pub granted_at: Timestamp,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<Timestamp>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revoked_at: Option<Timestamp>,
}

/// Everything needed to reproduce a plan byte-for-byte. Present on every artifact a
/// planner emits (EDL, AlbumSpec). Hard rule 3: same plan + same sources = identical
/// output, and that is only auditable if the plan says what produced it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Determinism {
    pub planner: Slug,

    pub planner_version: String,

    /// Seed for every stochastic choice in planning (variant sampling, tie-breaking). Same
    /// seed + same inputs must yield the same plan.
    pub seed: i64,

    /// BLAKE3 over the canonical JSON of every input the planner read: candidate ids,
    /// parameters, model refs. Two plans with the same digest and the same planner version
    /// are guaranteed identical.
    pub inputs_digest: Blake3Hash,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub generated_at: Option<Timestamp>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum GeoPointSource {
    #[serde(rename = "exif_gps")]
    ExifGps,

    #[serde(rename = "quicktime_location")]
    QuicktimeLocation,

    #[serde(rename = "xmp")]
    Xmp,

    #[serde(rename = "sidecar_json")]
    SidecarJson,

    #[serde(rename = "user_supplied")]
    UserSupplied,

    #[serde(rename = "inferred")]
    Inferred,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct GeoPoint {
    pub latitude: f64,

    pub longitude: f64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub altitude_m: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub horizontal_accuracy_m: Option<f64>,

    pub source: GeoPointSource,
}

/// ISO 8601 date-time with NO offset, e.g. 2019-08-04T17:22:31. This is what a camera
/// actually writes into EXIF DateTimeOriginal: a wall-clock reading with no timezone.
/// Storing it as a naive local time and keeping the zone separate is the only lossless
/// representation.
pub type LocalDateTime = String;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ModelRefPrecision {
    #[serde(rename = "fp32")]
    Fp32,

    #[serde(rename = "fp16")]
    Fp16,

    #[serde(rename = "bf16")]
    Bf16,

    #[serde(rename = "int8")]
    Int8,

    #[serde(rename = "int4")]
    Int4,
}

/// Pin to an exact model in the registry. Carries the weights hash, not just a version
/// string, because 'the same version' of a HuggingFace repo has changed weights under
/// people before. A record produced by an unpinned model is not reproducible, and
/// reproducibility is the product.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ModelRef {
    pub model_id: Slug,

    /// The registry's version string for this model. Free-form apart from one exclusion: no
    /// C0 control character and no DEL. FaceRecord.face_id joins model_id and version with
    /// U+001F, and a version containing that separator would let two different (model,
    /// version) pairs produce one identical byte string and therefore one identical face
    /// id. Excluding the separator structurally is what lets that encoding skip length
    /// prefixes; leaving it to convention is how the collision gets found in a family album
    /// instead of here.
    pub version: String,

    /// BLAKE3 of the weights file, or null when the entry is unpinned. Null is permitted
    /// ONLY because development mode permits loading unpinned weights; a null here is
    /// exactly what makes a record non-reproducible, and release mode refuses to produce
    /// one.
    pub weights_blake3: Option<Blake3Hash>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub runtime: Option<RuntimeTarget>,

    /// Quantisation the weights were executed at. int8 and fp16 runs can differ from fp32
    /// at the third decimal, which is enough to flip a borderline face match, so it is part
    /// of provenance.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub precision: Option<ModelRefPrecision>,

    /// BLAKE3 of the model config file that governed this run. Weights alone do not pin
    /// behaviour: input size, normalisation constants, score threshold, NMS IoU and the
    /// alignment template all live in the config, and changing any of them changes every
    /// downstream decision while the weights hash stays byte-identical. Null only for
    /// classical measures with no model config.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub config_blake3: Option<Blake3Hash>,
}

/// One execution of one model against one record. Every score in this contract points
/// at a run id, so 'why is this photo ranked 0.82' is always answerable and a model
/// swap can be evaluated by replaying only the affected runs.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ModelRun {
    pub run_id: Slug,

    pub model: ModelRef,

    pub ran_at: Timestamp,

    /// Which proxy the model actually saw. Analysis never touches originals (AGENTS.md hard
    /// rule 5), so this is normally set; null only for classical measures computed during
    /// ingest.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input_proxy_id: Option<Blake3Hash>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub job_id: Option<Blake3Hash>,
}

/// Axis-aligned rectangle in normalised image coordinates: origin top-left, x to the
/// right, y down, all values in [0,1] relative to the ORIENTED image (after EXIF
/// rotation is applied). Normalised so a box computed on a 512px thumbnail is valid
/// against the 6000px original without rescaling -- this is what lets analysis run on
/// proxies and render run on sources.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct NormalizedBox {
    pub x: f64,

    pub y: f64,

    pub w: f64,

    pub h: f64,

    /// Clockwise rotation of the box about its own centre. Present only for crops that
    /// deliberately rotate, e.g. straightening a horizon in an album placement.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rotation_deg: Option<f64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum PerceptualHashAlgorithm {
    #[serde(rename = "phash-dct-64")]
    PhashDct64,

    #[serde(rename = "phash-dct-256")]
    PhashDct256,

    #[serde(rename = "dhash-64")]
    Dhash64,

    #[serde(rename = "ahash-64")]
    Ahash64,

    #[serde(rename = "wavelet-64")]
    Wavelet64,
}

/// Permitted values: 64, 128, 256.
pub type PerceptualHashBits = i64;

/// Perceptual hash used for near-duplicate bucketing. Bucketing is by Hamming distance
/// on this hash; the bucket is then refined by embedding distance (build plan 4.2).
/// Always carries its algorithm so a future algorithm change cannot silently invalidate
/// existing buckets.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PerceptualHash {
    pub algorithm: PerceptualHashAlgorithm,

    /// Hash length in bits. Enforced to equal 4 * len(hex).
    pub bits: PerceptualHashBits,

    /// Lowercase hex digest. Its length is pinned to `bits` by the constraints below.
    pub hex: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PixelSize {
    pub width: i64,

    pub height: i64,
}

/// Point in the same normalised, orientation-applied coordinate space as NormalizedBox.
/// Landmarks may fall slightly outside [0,1] when a face is clipped by the frame edge,
/// so the bounds here are deliberately loose.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Point2D {
    pub x: f64,

    pub y: f64,
}

/// A time expressed as an exact rational: value frames (or samples) at the given rate.
/// Maps 1:1 onto opentimelineio.opentime.RationalTime. Seconds = value / rate. Never
/// store seconds as a float in this contract.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RationalTime {
    /// Position or count in units of 1/rate. May be fractional to survive sample-accurate
    /// audio edits, but integral values are strongly preferred on video tracks.
    pub value: f64,

    /// Units per second. Use the exact NTSC rationals where applicable: 24000/1001 =
    /// 23.976023976023978, 30000/1001 = 29.97002997002997, 60000/1001 = 59.94005994005994.
    pub rate: f64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RuntimeTarget {
    #[serde(rename = "onnxruntime_cpu")]
    OnnxruntimeCpu,

    #[serde(rename = "onnxruntime_coreml")]
    OnnxruntimeCoreml,

    #[serde(rename = "onnxruntime_directml")]
    OnnxruntimeDirectml,

    #[serde(rename = "onnxruntime_cuda")]
    OnnxruntimeCuda,

    #[serde(rename = "ctranslate2")]
    Ctranslate2,

    #[serde(rename = "mlx")]
    Mlx,

    #[serde(rename = "llama_cpp")]
    LlamaCpp,

    #[serde(rename = "opencv")]
    Opencv,

    #[serde(rename = "librosa")]
    Librosa,

    #[serde(rename = "native")]
    Native,
}

/// Contract version this record was written against. Frozen at 'v0' for the Phase 0
/// contract. A reader that does not recognise the value must refuse the record rather
/// than guess -- see hard rule 7, no silent anything.
pub type SchemaVersion = String;

/// A single scored value with a pointer back to the run that produced it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Score {
    pub value: Unit,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_id: Option<Slug>,

    /// Model output before normalisation to [0,1], kept so a recalibration can be applied
    /// without re-running the model.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub raw_value: Option<f64>,
}

/// Short stable machine identifier, lowercase alphanumeric with hyphens and
/// underscores. Used for ids that are authored by us rather than generated: track ids,
/// act ids, check ids.
pub type Slug = String;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TimeAssertionPrecision {
    #[serde(rename = "second")]
    Second,

    #[serde(rename = "minute")]
    Minute,

    #[serde(rename = "hour")]
    Hour,

    #[serde(rename = "day")]
    Day,

    #[serde(rename = "month")]
    Month,

    #[serde(rename = "year")]
    Year,

    #[serde(rename = "unknown")]
    Unknown,
}

/// A claim about when something was captured, together with how much we believe it.
/// Modelling capture time as an assertion rather than a bare timestamp is what lets the
/// system ingest a library where a third of the files have no EXIF date at all without
/// either dropping them or lying about their chronology.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TimeAssertion {
    /// Wall-clock reading as recorded by the device, with no zone applied. Null when
    /// nothing in the file or its neighbours implies a time.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub local: Option<LocalDateTime>,

    /// Resolved instant, present only when the zone is actually known (explicit offset in
    /// metadata, or GPS-derived zone). Never fabricate this by assuming the machine's local
    /// zone -- that silently shifts an entire holiday by hours.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub utc: Option<Timestamp>,

    /// IANA zone name, e.g. Asia/Kolkata, when it could be determined from metadata or GPS
    /// coordinates.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timezone: Option<String>,

    /// Offset actually recorded in the file (EXIF OffsetTimeOriginal or QuickTime), when
    /// present.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub utc_offset_minutes: Option<i64>,

    pub source: TimeSource,

    /// Granularity the assertion is actually good to. 'unknown' means we have no usable
    /// time at all, and consumers must exclude the item from chronology-ordered output
    /// rather than sorting it to the epoch.
    pub precision: TimeAssertionPrecision,

    pub confidence: Confidence,

    /// When precision came from neighbour_interpolation, the sibling records that bracketed
    /// this one. Recorded so the inference is auditable and can be recomputed when a
    /// neighbour's date is later corrected.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub inferred_from_media_ids: Option<Vec<Blake3Hash>>,
}

/// Half-open interval [start_time, start_time + duration). Maps 1:1 onto
/// opentimelineio.opentime.TimeRange. Half-open is deliberate and matches OTIO: it
/// makes adjacent clips tile a timeline with no off-by-one frame.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TimeRange {
    pub start_time: RationalTime,

    pub duration: RationalTime,
}

/// Where a capture time came from, ordered from most to least trustworthy. The ranking
/// is load-bearing: event clustering weights a filename-derived date far less than an
/// EXIF original, and an inferred date not at all for chronology-critical decisions.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TimeSource {
    #[serde(rename = "exif_datetime_original")]
    ExifDatetimeOriginal,

    #[serde(rename = "exif_datetime_digitized")]
    ExifDatetimeDigitized,

    #[serde(rename = "quicktime_creation_date")]
    QuicktimeCreationDate,

    #[serde(rename = "xmp_create_date")]
    XmpCreateDate,

    #[serde(rename = "gps_timestamp")]
    GpsTimestamp,

    #[serde(rename = "sidecar_json")]
    SidecarJson,

    #[serde(rename = "filename_pattern")]
    FilenamePattern,

    #[serde(rename = "filesystem_mtime")]
    FilesystemMtime,

    #[serde(rename = "neighbour_interpolation")]
    NeighbourInterpolation,

    #[serde(rename = "user_supplied")]
    UserSupplied,

    #[serde(rename = "unknown")]
    Unknown,
}

/// RFC 3339 instant with an explicit offset. Every wall-clock moment the system itself
/// observes (ingest time, decision time, render time) is unambiguous by construction.
pub type Timestamp = String;

/// A score normalised to [0,1]. Every model output in this contract is normalised
/// before it is written, so fusion never has to know a model's native range.
pub type Unit = f64;

/// RFC 4122 UUID, lowercase. Used only for entities whose identity is NOT determined by
/// content: person ids, cluster ids, user sessions, projects.
pub type Uuid = String;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum VectorRefStorage {
    #[serde(rename = "index")]
    Index,

    #[serde(rename = "inline")]
    Inline,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum VectorRefQuantization {
    #[serde(rename = "float32")]
    Float32,

    #[serde(rename = "float16")]
    Float16,

    #[serde(rename = "int8")]
    Int8,

    #[serde(rename = "binary")]
    Binary,
}

/// Reference to an embedding held in the vector index. Embeddings are referenced rather
/// than inlined so a MediaRecord stays small enough to page through 100k of them in a
/// UI; the index owns the floats. Inline values are permitted ONLY for fixtures and
/// tests, where self-containment matters more than size.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct VectorRef {
    pub space: VectorSpace,

    pub dimensions: i64,

    pub storage: VectorRefStorage,

    /// Row key in the sqlite-vec table. Required when storage is 'index'.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub index_key: Option<String>,

    /// Raw float components. Permitted only when storage is 'inline'. Length must equal
    /// `dimensions`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub values: Option<Vec<f64>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quantization: Option<VectorRefQuantization>,

    /// True when the vector is L2-normalised, which makes cosine distance a dot product.
    /// Every space in this contract stores normalised vectors.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub normalized: Option<bool>,
}

/// Named embedding space. Two vectors may only be compared when their space matches
/// exactly, including the model version that produced them -- a SigLIP 2 upgrade
/// creates a NEW space, it does not reinterpret the old one.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum VectorSpace {
    #[serde(rename = "siglip2_base_768")]
    Siglip2Base768,

    #[serde(rename = "siglip2_so400m_1152")]
    Siglip2So400m1152,

    #[serde(rename = "arcface_buffalo_l_512")]
    ArcfaceBuffaloL512,

    #[serde(rename = "adaface_ir101_512")]
    AdafaceIr101512,

    #[serde(rename = "clap_audio_512")]
    ClapAudio512,

    #[serde(rename = "aesthetic_head_1")]
    AestheticHead1,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Act {
    pub act_id: Slug,

    pub name: String,

    /// What this act is for, in plain language: 'arrival, establish where we are'.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub intent: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeline_range: Option<TimeRange>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_energy: Option<Unit>,

    pub beats: Vec<StoryBeat>,
}

/// Processing applied to the location sound as a whole. Keeping real ambient under
/// music is most of what separates a film that feels like a memory from a slideshow
/// with a soundtrack -- but the LEVEL of each clip's bed lives on that clip
/// (ClipAudio.gain_db), and this type carries only what is a property of the group
/// rather than of one clip.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AmbientPlan {
    /// Removes wind rumble, which otherwise dominates every outdoor action clip. Null means
    /// no filter. Applied ONCE to the summed ambient group -- after each clip's gain, fades
    /// and L-cut tail, before any DuckingRule -- so that the plan's order of operations is
    /// stated rather than left to a mixer's internal graph.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub high_pass: Option<HighPassFilter>,
}

/// The complete audio intention: what music plays, how much of the original scene
/// survives under it, and how the two are balanced against each other over time.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AudioPlan {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub music: Option<Vec<MusicCue>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ambient: Option<AmbientPlan>,

    /// Ordered ducking rules. Later rules win where they overlap, which keeps the
    /// resolution deterministic instead of depending on the renderer's mixer
    /// implementation; DuckingRule's $comment states exactly what 'overlap' means once the
    /// rules have attack and release ramps.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ducking: Option<Vec<DuckingRule>>,

    pub mix: MixPlan,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum BeatSection {
    #[serde(rename = "intro")]
    Intro,

    #[serde(rename = "verse")]
    Verse,

    #[serde(rename = "pre_chorus")]
    PreChorus,

    #[serde(rename = "chorus")]
    Chorus,

    #[serde(rename = "drop")]
    Drop,

    #[serde(rename = "bridge")]
    Bridge,

    #[serde(rename = "breakdown")]
    Breakdown,

    #[serde(rename = "outro")]
    Outro,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Beat {
    pub index: i64,

    pub time: RationalTime,

    pub is_downbeat: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bar: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub beat_in_bar: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub strength: Option<Unit>,

    /// Musical section this beat falls in. Lets the planner put the visual peak on the drop
    /// rather than merely on a loud beat.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub section: Option<BeatSection>,
}

/// Permitted values: 1, 2, 4, 8, 16.
pub type BeatGridTimeSignatureBeatUnit = i64;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BeatGridTimeSignature {
    pub beats_per_bar: i64,

    pub beat_unit: BeatGridTimeSignatureBeatUnit,
}

/// The musical skeleton the cut is hung on. Stored as explicit per-beat times rather
/// than as a BPM to be extrapolated, because real tracks drift and an extrapolated grid
/// is 200ms out by the end of a 30-second reel.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BeatGrid {
    /// Which MusicCue this grid describes.
    pub source_cue_id: Slug,

    pub bpm: f64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bpm_confidence: Option<Confidence>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub time_signature: Option<BeatGridTimeSignature>,

    /// Every beat, in TIMELINE time, ordered. Downbeats are flagged rather than stored
    /// separately so a cut can reference one index regardless of which it turned out to be.
    pub beats: Vec<Beat>,

    /// Which beat tracker produced this. Recorded partly for reproducibility and partly
    /// because the licence-safe analyser (librosa, ISC) and the more accurate but non-
    /// commercial ones (madmom) must never be confused.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub analyzer: Option<ModelRef>,

    /// Maximum acceptable beat-alignment error for a cut claiming to be beat-locked. The
    /// quality gate is 50ms on downbeats.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tolerance_ms: Option<f64>,
}

/// Records that a cut was placed against the music rather than merely near it.
/// `alignment_error_ms` is the audit trail for the <50ms downbeat quality gate.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BeatLock {
    /// Index into BeatGrid.beats.
    pub beat_index: i64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_downbeat: Option<bool>,

    /// Signed distance from the clip's timeline in-point to the beat. Negative is early.
    /// Non-zero because a cut must also land on a certified snap point, and the nearest
    /// snap point is rarely exactly on the beat -- the planner trades a few milliseconds of
    /// beat error for a cut that lands on a real motion onset.
    pub alignment_error_ms: f64,

    /// Which MomentRecord snap point the cut actually landed on.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub snap_point_kind: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Clip {
    pub clip_id: Slug,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,

    pub media_ref_id: Slug,

    /// In and out in SOURCE time, half-open. The single most important field in the
    /// contract: it is what the renderer seeks to, and it must round-trip through OTIO
    /// unchanged.
    pub source_range: TimeRange,

    /// Derived position on the timeline. Carried for validation only; excluded from the
    /// determinism digest and not exported to OTIO, which recomputes it. Its DURATION is
    /// derived from source_range and any time_effect by the rule in TimeEffect's $comment
    /// -- equal to source_range.duration when there is no effect -- and its START is the
    /// running sum of the extents before it. A timeline_range that disagrees with that
    /// arithmetic is a validation failure, never a correction.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeline_range: Option<TimeRange>,

    /// The MomentRecord this clip realises. The provenance link that lets 'more of her' re-
    /// plan against the same candidate pool instead of starting over.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub moment_id: Option<Blake3Hash>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub time_effect: Option<TimeEffect>,

    /// Reframe track driving this clip's crop. Null means full frame, letterboxed or
    /// pillarboxed per the target.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reframe_track_id: Option<Slug>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub color_ops: Option<Vec<ColorOp>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio: Option<ClipAudio>,

    /// Present when this clip's in-point was snapped to the beat grid. Carries the
    /// alignment error so the <50ms quality gate is measurable from the plan alone, without
    /// rendering anything.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub beat_lock: Option<BeatLock>,

    /// Which story-arc beat this clip satisfies. Null on clips that are connective tissue
    /// rather than a required beat.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub story_beat_id: Option<Slug>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub markers: Option<Vec<Marker>>,
}

/// This clip's own sound, and the ONLY place its level is stated (contracts#53).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ClipAudio {
    /// Level of this clip's own audio, in dB relative to the source. Composes with nothing
    /// else in the plan except MixPlan.master_gain_db and any DuckingRule whose target role
    /// covers this clip's track.
    pub gain_db: f64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub muted: Option<bool>,

    /// Fade length at the clip's in-point. The curve is a LINEAR RAMP IN AMPLITUDE from 0
    /// to 1 over the declared frames -- not equal-power, not linear in dB (contracts#60).
    /// Equal-power is the usual choice for a music crossfade and would be audibly different
    /// on a long fade, so it is named here rather than left to the mixer; a planner that
    /// wants a different shape emits a Transition.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fade_in: Option<RationalTime>,

    /// Fade length ending on the clip's last frame, a linear ramp in amplitude from 1 to 0.
    /// Same convention as fade_in.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fade_out: Option<RationalTime>,

    /// L-cut: hold this clip's audio past its visual out-point, so a laugh finishes over
    /// the next shot. Realises MomentRecord.safe_trim.preserve_audio_tail.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio_extends_past_out: Option<RationalTime>,
}

/// What a set of code values MEANS: primaries, transfer function and matrix
/// coefficients, as one token. The spelling is closed -- see this def's $comment for
/// the table each token expands to.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ColorEncoding {
    #[serde(rename = "srgb")]
    Srgb,

    #[serde(rename = "bt709")]
    Bt709,

    #[serde(rename = "display_p3")]
    DisplayP3,

    #[serde(rename = "bt2020_sdr")]
    Bt2020Sdr,

    #[serde(rename = "bt2100_pq")]
    Bt2100Pq,

    #[serde(rename = "bt2100_hlg")]
    Bt2100Hlg,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ColorOpOp {
    #[serde(rename = "exposure")]
    Exposure,

    #[serde(rename = "saturation")]
    Saturation,
}

/// A per-clip colour adjustment, planned by the intelligence layer and merely applied
/// by the renderer. `amount` is normalised to [-1,1]; what that means in light is
/// stated in this def's $comment, per op, as a formula (contracts#49).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ColorOp {
    /// Which adjustment. The enum is short on purpose -- see the $comment for what was
    /// removed and why, and for the issue that carries each removed capability.
    pub op: ColorOpOp,

    /// Signed, normalised to [-1,1] where 0 is no change. Exposure: a * 2 stops.
    /// Saturation: a chroma scale of 1 + a. A single normalised scale keeps ops composable
    /// and makes 'is this grade aggressive' a question with an answer.
    pub amount: f64,

    /// PROVENANCE ONLY -- a renderer never reads this field. The clip whose look this
    /// adjustment was derived from, recorded so a shot-matching decision stays auditable in
    /// the plan after the planner has resolved it into primitive ops. It resolves nothing
    /// at render time: the ops beside it are the whole instruction.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reference_clip_id: Option<Slug>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ColorPipelineWorkingSpace {
    #[serde(rename = "linear_bt709")]
    LinearBt709,

    #[serde(rename = "linear_bt2020")]
    LinearBt2020,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ColorPipelineOutputEncoding {
    #[serde(rename = "srgb")]
    Srgb,

    #[serde(rename = "bt709")]
    Bt709,

    #[serde(rename = "display_p3")]
    DisplayP3,

    #[serde(rename = "bt2020_sdr")]
    Bt2020Sdr,
}

/// The colour path from every source to the delivered file. Every field is required and
/// none has a default: a colour decision a renderer supplies is invisible until print.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ColorPipeline {
    /// Where colour ops and tone mapping compose. LINEAR-LIGHT RGB only, scaled so 1.0 is
    /// reference white -- the earlier enum mixed transfer names ('rec709') with 'linear'
    /// and with a log space, so it could not say what an op meant. `aces_cct` went with it:
    /// no worker here implements it, and its log curve has constants nothing in this repo
    /// states.
    pub working_space: ColorPipelineWorkingSpace,

    /// What the delivered file's code values mean, and what its container-level colour tags
    /// must say. SDR only at v0 -- see the ColorEncoding $comment.
    pub output_encoding: ColorPipelineOutputEncoding,

    /// Required exactly when at least one source's `color_encoding` is an HDR member
    /// (bt2100_pq, bt2100_hlg); must be null otherwise. Checked as
    /// `color_pipeline_resolves`.
    pub tone_map: Option<ToneMap>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum DuckingRuleTarget {
    #[serde(rename = "music")]
    Music,

    #[serde(rename = "ambient")]
    Ambient,

    #[serde(rename = "sfx")]
    Sfx,
}

/// One duck: turn `target` down by `reduction_db` over these timeline ranges, on the
/// envelope this def's $comment states exactly.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DuckingRule {
    pub rule_id: Slug,

    /// Which Track.role gets turned down. A rule whose target matches no track in the plan
    /// states an intent about audio that does not exist, and is a validation failure.
    pub target: DuckingRuleTarget,

    /// Positive number of dB to reduce by, reached at the range start and held to the range
    /// end.
    pub reduction_db: f64,

    /// Length of the ramp DOWN, ending at the range start. 0 is a step.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attack_ms: Option<f64>,

    /// Length of the ramp back UP, beginning at the range end. 0 is a step.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub release_ms: Option<f64>,

    /// Timeline ranges held at the full reduction. Non-empty, and each must lie within the
    /// timeline.
    pub ranges: Vec<TimeRange>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EdlValidationStatus {
    #[serde(rename = "pass")]
    Pass,

    #[serde(rename = "warn")]
    Warn,

    #[serde(rename = "fail")]
    Fail,

    #[serde(rename = "not_run")]
    NotRun,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EdlValidationChecksItemCheckId {
    #[serde(rename = "source_range_within_available")]
    SourceRangeWithinAvailable,

    #[serde(rename = "media_refs_resolvable")]
    MediaRefsResolvable,

    #[serde(rename = "timeline_contiguous")]
    TimelineContiguous,

    #[serde(rename = "time_effect_extent_derived")]
    TimeEffectExtentDerived,

    #[serde(rename = "music_cues_placed_once")]
    MusicCuesPlacedOnce,

    #[serde(rename = "span_continuity_verified")]
    SpanContinuityVerified,

    #[serde(rename = "color_pipeline_resolves")]
    ColorPipelineResolves,

    #[serde(rename = "transition_handles_available")]
    TransitionHandlesAvailable,

    #[serde(rename = "beat_alignment_within_tolerance")]
    BeatAlignmentWithinTolerance,

    #[serde(rename = "no_mid_word_cut")]
    NoMidWordCut,

    #[serde(rename = "reframe_aspect_matches_target")]
    ReframeAspectMatchesTarget,

    #[serde(rename = "reframe_keyframes_ordered")]
    ReframeKeyframesOrdered,

    #[serde(rename = "duration_within_max")]
    DurationWithinMax,

    #[serde(rename = "music_license_covers_destination")]
    MusicLicenseCoversDestination,

    #[serde(rename = "required_story_beats_satisfied")]
    RequiredStoryBeatsSatisfied,

    #[serde(rename = "audio_loudness_target_set")]
    AudioLoudnessTargetSet,

    #[serde(rename = "determinism_digest_present")]
    DeterminismDigestPresent,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EdlValidationChecksItemSeverity {
    #[serde(rename = "error")]
    Error,

    #[serde(rename = "warning")]
    Warning,

    #[serde(rename = "info")]
    Info,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EdlValidationChecksItem {
    pub check_id: EdlValidationChecksItemCheckId,

    pub passed: bool,

    pub severity: EdlValidationChecksItemSeverity,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub clip_id: Option<Slug>,
}

/// Result of the pre-render checks. The renderer refuses an EDL that has not passed,
/// which keeps 'the renderer is dumb' from meaning 'the renderer is trusting'.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EdlValidation {
    pub status: EdlValidationStatus,

    pub checks: Vec<EdlValidationChecksItem>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validated_at: Option<Timestamp>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validator_version: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EncodeAudioCodec {
    #[serde(rename = "aac")]
    Aac,

    #[serde(rename = "opus")]
    Opus,

    #[serde(rename = "flac")]
    Flac,

    #[serde(rename = "pcm_s16le")]
    PcmS16le,

    #[serde(rename = "pcm_s24le")]
    PcmS24le,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EncodeAudioEncoder {
    #[serde(rename = "aac")]
    Aac,

    #[serde(rename = "aac_at")]
    AacAt,

    #[serde(rename = "libopus")]
    Libopus,

    #[serde(rename = "flac")]
    Flac,

    #[serde(rename = "pcm_s16le")]
    PcmS16le,

    #[serde(rename = "pcm_s24le")]
    PcmS24le,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EncodeAudioSampleFormat {
    #[serde(rename = "fltp")]
    Fltp,

    #[serde(rename = "s16")]
    S16,

    #[serde(rename = "s16p")]
    S16p,

    #[serde(rename = "s32")]
    S32,

    #[serde(rename = "s32p")]
    S32p,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EncodeAudio {
    pub codec: EncodeAudioCodec,

    pub encoder: EncodeAudioEncoder,

    pub sample_format: EncodeAudioSampleFormat,

    /// Null for the lossless codecs, which have no bit rate to set.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bit_rate_kbps: Option<f64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EncodeProfileContainer {
    #[serde(rename = "mp4")]
    Mp4,

    #[serde(rename = "mov")]
    Mov,

    #[serde(rename = "mkv")]
    Mkv,

    #[serde(rename = "webm")]
    Webm,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EncodeProfileScaler {
    #[serde(rename = "neighbor")]
    Neighbor,

    #[serde(rename = "bilinear")]
    Bilinear,

    #[serde(rename = "bicubic")]
    Bicubic,

    #[serde(rename = "lanczos")]
    Lanczos,

    #[serde(rename = "spline")]
    Spline,
}

/// The delivery encode, stated in the plan rather than chosen by the renderer
/// (contracts#56). Every field is mandatory somewhere in this object: there is no
/// default profile, and no destination-to-codec table anywhere in a worker. Two plans
/// that differ only in their encode are two different files, and
/// `determinism.inputs_digest` covers this block for exactly that reason.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EncodeProfile {
    /// Names this exact combination of settings, so a delivery preset is versioned contract
    /// data that review can see rather than a table inside a worker. Two profiles that
    /// differ in any field must not share an id.
    pub profile_id: Slug,

    /// The muxer. `mp4` and `mov` are delivery; `mkv` is what a lossless master goes in,
    /// because MP4 cannot carry FFV1.
    pub container: EncodeProfileContainer,

    /// Resampling kernel used to fit a crop to `resolution`. Pinned because the scaler
    /// touches every pixel of every frame, and two kernels are visibly different on a 480p
    /// proxy blown up to 1080p.
    pub scaler: EncodeProfileScaler,

    /// Encoder thread count. A determinism input, not a performance setting -- see this
    /// def's $comment.
    pub encoder_threads: i64,

    pub video: EncodeVideo,

    /// Null only when the plan carries no audio at all. A program with audio and a null
    /// audio profile is a validation failure, not a silent mute.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio: Option<EncodeAudio>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EncodeVideoCodec {
    #[serde(rename = "h264")]
    H264,

    #[serde(rename = "hevc")]
    Hevc,

    #[serde(rename = "av1")]
    Av1,

    #[serde(rename = "vp9")]
    Vp9,

    #[serde(rename = "ffv1")]
    Ffv1,

    #[serde(rename = "prores")]
    Prores,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EncodeVideoEncoder {
    #[serde(rename = "libx264")]
    Libx264,

    #[serde(rename = "libx265")]
    Libx265,

    #[serde(rename = "libsvtav1")]
    Libsvtav1,

    #[serde(rename = "libvpx-vp9")]
    LibvpxVp9,

    #[serde(rename = "ffv1")]
    Ffv1,

    #[serde(rename = "prores_ks")]
    ProresKs,

    #[serde(rename = "h264_videotoolbox")]
    H264Videotoolbox,

    #[serde(rename = "hevc_videotoolbox")]
    HevcVideotoolbox,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EncodeVideoPixelFormat {
    #[serde(rename = "yuv420p")]
    Yuv420p,

    #[serde(rename = "yuv422p")]
    Yuv422p,

    #[serde(rename = "yuv444p")]
    Yuv444p,

    #[serde(rename = "yuv420p10le")]
    Yuv420p10le,

    #[serde(rename = "yuv422p10le")]
    Yuv422p10le,

    #[serde(rename = "yuv444p10le")]
    Yuv444p10le,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EncodeVideo {
    /// What a player must decode.
    pub codec: EncodeVideoCodec,

    /// Which implementation writes the bytes. Must produce `codec`; a renderer refuses the
    /// pair if it does not.
    pub encoder: EncodeVideoEncoder,

    /// Chroma subsampling and bit depth in one value, which is how every encoder actually
    /// takes it.
    pub pixel_format: EncodeVideoPixelFormat,

    pub rate_control: RateControl,

    /// Encoder speed/efficiency preset, e.g. x264's `medium`. Null means the encoder's own
    /// default, which is only acceptable for encoders that have no preset axis (ffv1,
    /// prores_ks).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub preset: Option<String>,

    /// Codec profile, e.g. `high` for H.264 or `main10` for HEVC. Null means the encoder
    /// derives it from pixel_format.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub profile: Option<String>,

    /// Codec level, e.g. `4.0`. Null means the encoder derives it from resolution and rate.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub level: Option<String>,

    /// Maximum GOP length in frames. Stated because it is a delivery decision with
    /// consequences a viewer feels -- a platform re-encoding a 10-second GOP seeks worse
    /// than one re-encoding a 2-second GOP -- and because leaving it to the encoder's
    /// default makes the same plan produce different files on different builds.
    pub keyframe_interval_frames: i64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum GapFill {
    #[serde(rename = "black")]
    Black,

    #[serde(rename = "white")]
    White,

    #[serde(rename = "transparent")]
    Transparent,

    #[serde(rename = "silence")]
    Silence,
}

/// Explicit silence or black. Modelled explicitly, exactly as OTIO does, so a hold at
/// the end of a film is a stated intention rather than an accident of arithmetic.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Gap {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gap_id: Option<Slug>,

    pub duration: RationalTime,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fill: Option<GapFill>,
}

/// Permitted values: 2, 4.
pub type HighPassFilterOrder = i64;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HighPassFilter {
    /// -3 dB corner of the cascade.
    pub corner_hz: f64,

    /// Pole count. See AmbientPlan.high_pass's $comment for the Q values each order expands
    /// to.
    pub order: HighPassFilterOrder,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MarkerColor {
    #[serde(rename = "RED")]
    RED,

    #[serde(rename = "GREEN")]
    GREEN,

    #[serde(rename = "BLUE")]
    BLUE,

    #[serde(rename = "CYAN")]
    CYAN,

    #[serde(rename = "MAGENTA")]
    MAGENTA,

    #[serde(rename = "YELLOW")]
    YELLOW,

    #[serde(rename = "ORANGE")]
    ORANGE,

    #[serde(rename = "PURPLE")]
    PURPLE,

    #[serde(rename = "WHITE")]
    WHITE,

    #[serde(rename = "BLACK")]
    BLACK,

    #[serde(rename = "PINK")]
    PINK,

    #[serde(rename = "MINT")]
    MINT,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MarkerKind {
    #[serde(rename = "beat")]
    Beat,

    #[serde(rename = "downbeat")]
    Downbeat,

    #[serde(rename = "story_beat")]
    StoryBeat,

    #[serde(rename = "emotional_peak")]
    EmotionalPeak,

    #[serde(rename = "speech")]
    Speech,

    #[serde(rename = "warning")]
    Warning,

    #[serde(rename = "note")]
    Note,
}

/// Maps to otio.schema.Marker. Markers are how our reasoning becomes visible to a human
/// editor who opens the timeline in Resolve.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Marker {
    pub name: String,

    pub marked_range: TimeRange,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub color: Option<MarkerColor>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<MarkerKind>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub comment: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MediaRefMediaKind {
    #[serde(rename = "video")]
    Video,

    #[serde(rename = "image")]
    Image,

    #[serde(rename = "audio")]
    Audio,

    #[serde(rename = "music")]
    Music,

    #[serde(rename = "generated")]
    Generated,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MediaRefContinuity {
    #[serde(rename = "verified_gapless")]
    VerifiedGapless,

    #[serde(rename = "verified_gap")]
    VerifiedGap,

    #[serde(rename = "unverified")]
    Unverified,

    #[serde(rename = "incomplete_set")]
    IncompleteSet,
}

/// One source, addressed by content hash rather than by path. This is what makes an EDL
/// portable: the same plan renders on any machine that has the same footage, wherever
/// it happens to live.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MediaRef {
    /// Local alias used by clips within this EDL.
    pub media_ref_id: Slug,

    /// BLAKE3 of the source file, i.e. a MediaRecord primary key. For chaptered footage
    /// this is the span ASSEMBLY id; the renderer expands it to the ordered member files.
    pub media_id: Blake3Hash,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub media_kind: Option<MediaRefMediaKind>,

    /// The full extent of the source, in source time. Exported as
    /// ExternalReference.available_range. A clip whose source_range escapes this is invalid
    /// and must fail validation before render, not during it.
    pub available_range: TimeRange,

    /// True when media_id names a virtual assembly of chaptered files rather than a single
    /// file on disk.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_span_assembly: Option<bool>,

    /// The assembly's members, in INDEX order -- the order they concatenate into one
    /// recording. Required when is_span_assembly is true and forbidden otherwise. This is
    /// the field that makes an assembly expandable from the EDL alone (contracts#55): the
    /// renderer never sees a MediaRecord, so before this existed the member order arrived
    /// out of band in the render job and the plan could not state what it had planned
    /// against.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub member_media_ids: Option<Vec<Blake3Hash>>,

    /// Whether the chapters were verified gapless, copied from MediaRecord.Span.continuity.
    /// Required when is_span_assembly is true and forbidden otherwise.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub continuity: Option<MediaRefContinuity>,

    /// What this source's code values mean. REQUIRED for a video or image source and null
    /// for an audio or music one. Stated by the planner from the MediaRecord ingest already
    /// probed -- never inferred at render time, which is what
    /// `ColorPipeline.input_transform: "auto"` used to ask for (contracts#58).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub color_encoding: Option<ColorEncoding>,

    /// Peak luminance this source is graded to, in cd/m^2. REQUIRED when `color_encoding`
    /// is an HDR member and null otherwise. For `bt2100_hlg` it MUST be 1000, because that
    /// is the nominal display the HLG decode is defined against here -- any other value
    /// would describe a decode that did not happen. It sits on the source rather than on
    /// ToneMap because a cut can hold a 1000-nit phone clip and a 4000-nit graded one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_peak_nits: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_frame_rate: Option<f64>,

    /// Human-readable name for the NLE bin. Cosmetic only -- never used for resolution.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub label: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MixPlanChannels {
    #[serde(rename = "mono")]
    Mono,

    #[serde(rename = "stereo")]
    Stereo,
}

/// Permitted values: 44100, 48000.
pub type MixPlanSampleRate = i64;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MixPlan {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub master_gain_db: Option<f64>,

    pub loudness_target_lufs: f64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub true_peak_ceiling_db: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limiter: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub channels: Option<MixPlanChannels>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sample_rate: Option<MixPlanSampleRate>,
}

/// Licence and provenance for one piece of music, attached to the clips that place it.
/// A cue is NOT a placement: the bed lives on an audio track like every other sound,
/// and the cue says what it is and what may legally be done with it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MusicCue {
    pub cue_id: Slug,

    /// The source this cue licenses. Must equal the media_ref_id of every clip in clip_ids.
    pub media_ref_id: Slug,

    /// The audio-track clips that place this cue, in timeline order. One entry for a bed
    /// that plays once, one per pass for a bed that repeats. Every clip on a track whose
    /// role is `music` must be claimed by exactly one cue -- that is how an unlicensed bed
    /// becomes impossible rather than merely unlikely.
    pub clip_ids: Vec<Slug>,

    /// Required, not optional. Music licensing is a Phase 0 decision precisely because an
    /// unlicensed track in a shared reel is a legal problem, and the plan is the place it
    /// becomes checkable.
    pub license: MusicLicense,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MusicLicenseProvider {
    #[serde(rename = "catalog_partner")]
    CatalogPartner,

    #[serde(rename = "creative_commons")]
    CreativeCommons,

    #[serde(rename = "public_domain")]
    PublicDomain,

    #[serde(rename = "generated_score")]
    GeneratedScore,

    #[serde(rename = "user_supplied")]
    UserSupplied,

    #[serde(rename = "platform_library")]
    PlatformLibrary,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MusicLicenseLicenseType {
    #[serde(rename = "royalty_free")]
    RoyaltyFree,

    #[serde(rename = "cc_by")]
    CcBy,

    #[serde(rename = "cc_by_sa")]
    CcBySa,

    #[serde(rename = "cc0")]
    Cc0,

    #[serde(rename = "rights_managed")]
    RightsManaged,

    #[serde(rename = "personal_use_only")]
    PersonalUseOnly,

    #[serde(rename = "unknown")]
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MusicLicenseClearedForItem {
    #[serde(rename = "private_playback")]
    PrivatePlayback,

    #[serde(rename = "social_share")]
    SocialShare,

    #[serde(rename = "commercial_use")]
    CommercialUse,

    #[serde(rename = "broadcast")]
    Broadcast,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MusicLicense {
    pub provider: MusicLicenseProvider,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub license_id: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub track_title: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attribution_required: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attribution_text: Option<String>,

    pub license_type: MusicLicenseLicenseType,

    /// Where this cut may legally be published. `user_supplied` music is typically
    /// personal_use only, and a share flow must be able to refuse rather than discover the
    /// problem after upload.
    pub cleared_for: Vec<MusicLicenseClearedForItem>,
}

/// Bookkeeping for the OTIO round trip. `unmapped_fields` must be empty for an export
/// to be called lossless; if it is not, the exporter is telling us exactly which part
/// of the contract has outgrown the mapping.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OtioExportInfo {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub otio_schema_version: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metadata_namespace: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unmapped_fields: Option<Vec<String>>,

    /// Set by the exporter after re-importing its own output and comparing to the source
    /// EDL. The claim of losslessness is tested per export, not assumed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub round_trip_verified: Option<bool>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RateControlMode {
    #[serde(rename = "crf")]
    Crf,

    #[serde(rename = "cqp")]
    Cqp,

    #[serde(rename = "abr")]
    Abr,

    #[serde(rename = "cbr")]
    Cbr,

    #[serde(rename = "lossless")]
    Lossless,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RateControl {
    /// `crf` and `cqp` take a quality value; `abr` and `cbr` take a bit rate; `lossless`
    /// takes neither.
    pub mode: RateControlMode,

    /// CRF or QP value. Required for crf and cqp, null otherwise.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quality: Option<f64>,

    /// Target bit rate. Required for abr and cbr, null otherwise.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bit_rate_kbps: Option<f64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ReframeKeyframeInterpolation {
    #[serde(rename = "linear")]
    Linear,

    #[serde(rename = "smooth")]
    Smooth,

    #[serde(rename = "bezier")]
    Bezier,

    #[serde(rename = "hold")]
    Hold,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ReframeKeyframe {
    /// SOURCE time of the keyframe, so the track stays valid if the clip is later trimmed
    /// or retimed.
    pub time: RationalTime,

    /// Crop window in normalised source coordinates. Its aspect must match the track's
    /// target_aspect_ratio; a mismatch is a validation failure because the renderer must
    /// not be the one deciding how to reconcile it.
    pub crop: NormalizedBox,

    /// How to reach the NEXT keyframe. `hold` produces a snap, which is occasionally what a
    /// hard beat wants. Every mode is a stated formula, not a name -- see the $comment.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interpolation: Option<ReframeKeyframeInterpolation>,

    /// Control points for bezier interpolation, as (x,y) in normalised keyframe-interval
    /// space.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bezier_control: Option<Vec<Point2D>>,

    /// Tracker confidence at this keyframe. Low-confidence stretches are where the fallback
    /// earns its keep.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<Confidence>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ReframeSmoothingMethod {
    #[serde(rename = "none")]
    None,

    #[serde(rename = "moving_average")]
    MovingAverage,

    #[serde(rename = "savitzky_golay")]
    SavitzkyGolay,

    #[serde(rename = "kalman")]
    Kalman,

    #[serde(rename = "spring_damper")]
    SpringDamper,
}

/// Constraints that stop a reframe from looking like a nervous camera operator. Raw
/// per-frame subject centroids are far too jittery to use directly.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ReframeSmoothing {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub method: Option<ReframeSmoothingMethod>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub window_frames: Option<i64>,

    /// Cap on crop travel, in normalised units per second. The difference between a
    /// considered pan and a whip.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_velocity_per_second: Option<f64>,

    /// Subject movement smaller than this does not move the crop at all, which is what
    /// keeps a mostly-still subject from causing constant micro-drift.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deadzone: Option<Unit>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ReframeTrackFallback {
    #[serde(rename = "center_crop")]
    CenterCrop,

    #[serde(rename = "saliency_crop")]
    SaliencyCrop,

    #[serde(rename = "letterbox")]
    Letterbox,

    #[serde(rename = "hold_last_keyframe")]
    HoldLastKeyframe,
}

/// A crop keyframe track that turns landscape footage into a vertical cut with the
/// subject held in frame. This is core IP and has no OTIO equivalent, so it round-trips
/// through metadata.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ReframeTrack {
    pub reframe_track_id: Slug,

    pub target_aspect_ratio: AspectRatio,

    /// Ordered by time, at least one. A single keyframe is a static crop; the interesting
    /// case is a moving crop that tracks a subject across the frame.
    pub keyframes: Vec<ReframeKeyframe>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subject_lock: Option<SubjectLock>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub smoothing: Option<ReframeSmoothing>,

    /// What to do if subject tracking fails at render time. Never leave this implicit: a
    /// reframe that silently falls back to a centre crop can decapitate the subject of the
    /// shot.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fallback: Option<ReframeTrackFallback>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RenderTargetDestination {
    #[serde(rename = "master")]
    Master,

    #[serde(rename = "instagram_reel")]
    InstagramReel,

    #[serde(rename = "instagram_feed")]
    InstagramFeed,

    #[serde(rename = "youtube")]
    Youtube,

    #[serde(rename = "youtube_shorts")]
    YoutubeShorts,

    #[serde(rename = "tiktok")]
    Tiktok,

    #[serde(rename = "whatsapp_status")]
    WhatsappStatus,

    #[serde(rename = "web_preview")]
    WebPreview,
}

/// What this cut is for. Destination is part of the plan because the reframe, the
/// loudness target and the duration are all chosen for it -- an Instagram cut is not a
/// YouTube cut at a different bitrate.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RenderTarget {
    pub destination: RenderTargetDestination,

    pub resolution: PixelSize,

    pub aspect_ratio: AspectRatio,

    /// How the file is written. Required: `destination` says what the cut is FOR and
    /// settles nothing about the bytes, and a renderer that fills the difference in has
    /// made a delivery decision invisibly (contracts#56).
    pub encode: EncodeProfile,

    /// What the planner was asked for. The realised duration is the sum of the timeline and
    /// may differ slightly, because landing a cut on a beat matters more than hitting
    /// exactly 30.000s.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_duration: Option<RationalTime>,

    /// Hard ceiling imposed by the platform. Exceeding it is a validation failure, not a
    /// warning.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_duration: Option<RationalTime>,

    /// Integrated loudness the mix is normalised to. -14 for most social platforms, -23 for
    /// broadcast-style masters.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub loudness_target_lufs: Option<f64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum StoryArcTemplate {
    #[serde(rename = "hook_build_peak_button")]
    HookBuildPeakButton,

    #[serde(rename = "three_act")]
    ThreeAct,

    #[serde(rename = "chronological")]
    Chronological,

    #[serde(rename = "day_in_the_life")]
    DayInTheLife,

    #[serde(rename = "before_after")]
    BeforeAfter,

    #[serde(rename = "montage")]
    Montage,

    #[serde(rename = "custom")]
    Custom,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StoryArcEnergyCurveItem {
    pub time: RationalTime,

    pub energy: Unit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum StoryArcSource {
    #[serde(rename = "template")]
    Template,

    #[serde(rename = "tier2_vlm")]
    Tier2Vlm,

    #[serde(rename = "tier3_model")]
    Tier3Model,

    #[serde(rename = "user")]
    User,
}

/// The narrative intention, kept with the plan so that a revision instruction such as
/// 'more of her' or 'less drone' re-satisfies the SAME arc instead of re-planning from
/// scratch. That persistence is what makes iterative editing feel like direction rather
/// than dice-rolling.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StoryArc {
    pub arc_id: Slug,

    pub template: StoryArcTemplate,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,

    /// One sentence the arc is trying to say. Written by the frontier model and shown to
    /// the user, so a plan can be judged before it is rendered.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub logline: Option<String>,

    pub acts: Vec<Act>,

    /// Target energy over the timeline, as sampled control points. The planner satisfies it
    /// mechanically by choosing moments whose features match; it is a specification, not a
    /// description.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub energy_curve: Option<Vec<StoryArcEnergyCurveItem>>,

    /// Who authored the arc. A tier3_model arc must carry its ConsentRef, because producing
    /// it meant sending a contact sheet off the device.
    pub source: StoryArcSource,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelRef>,

    /// Which prompt-engine template produced it, so a prompt regression is traceable to the
    /// outputs it affected.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prompt_id: Option<Slug>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub consent: Option<ConsentRef>,

    /// The model's stated reasoning, kept for the user-facing 'why this cut' explanation
    /// and for eval review.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rationale: Option<String>,
}

/// A required narrative element and the clips that satisfy it. An unsatisfied required
/// beat is a validation failure -- that is the mechanism by which 'the film has an arc'
/// is checked rather than hoped for.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StoryBeat {
    pub beat_id: Slug,

    pub description: String,

    pub required: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub satisfied_by_clip_ids: Option<Vec<Slug>>,

    /// Moments the planner considered for this beat. Retained so a revision can swap in an
    /// alternative without re-running retrieval.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub candidate_moment_ids: Option<Vec<Blake3Hash>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SubjectLockSource {
    #[serde(rename = "sam2_track")]
    Sam2Track,

    #[serde(rename = "face_track")]
    FaceTrack,

    #[serde(rename = "saliency")]
    Saliency,

    #[serde(rename = "manual")]
    Manual,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SubjectLockKeepInFrame {
    #[serde(rename = "head")]
    Head,

    #[serde(rename = "full_body")]
    FullBody,

    #[serde(rename = "bbox_center")]
    BboxCenter,

    #[serde(rename = "bbox_full")]
    BboxFull,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SubjectLock {
    pub source: SubjectLockSource,

    /// The tracked entity: a SAM 2 object id, a face track uuid, or null for saliency.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subject_ref: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub person_id: Option<Uuid>,

    /// The part of the subject that must never leave the crop. `head` is the one that
    /// matters for people: a technically-centred crop that clips a forehead reads as a
    /// mistake.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub keep_in_frame: Option<SubjectLockKeepInFrame>,

    /// Fraction of the crop height kept above the subject's head. Composition rule, planned
    /// rather than hard-coded in the renderer.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub headroom: Option<Unit>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TimeEffectKind {
    #[serde(rename = "linear_speed")]
    LinearSpeed,

    #[serde(rename = "freeze_frame")]
    FreezeFrame,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TimeEffectAudioHandling {
    #[serde(rename = "mute")]
    Mute,

    #[serde(rename = "resample")]
    Resample,

    #[serde(rename = "preserve_pitch")]
    PreservePitch,
}

/// Speed change. Restricted to what OTIO models natively, because a speed ramp that
/// cannot round-trip is a speed ramp that silently disappears in Resolve.
/// `source_range` stays authoritative under an effect and the timeline extent is
/// derived from it -- see the $comment, which is the rule the renderer implements.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TimeEffect {
    pub kind: TimeEffectKind,

    /// OTIO LinearTimeWarp.time_scalar: the ratio of media time to timeline time. 0.5 is
    /// half speed (twice the timeline extent), 2.0 is double. Required for linear_speed,
    /// and must divide source_range.duration into a whole number of timeline frames.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub time_scalar: Option<f64>,

    /// Source time to hold. Required for freeze_frame, and must equal
    /// source_range.start_time -- the frozen frame is the one frame the clip reads.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub freeze_at: Option<RationalTime>,

    /// How long the frozen frame is held, in TIMELINE time. Required for freeze_frame,
    /// forbidden otherwise: it is the clip's timeline extent, and without it a freeze has a
    /// start and no end.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hold_duration: Option<RationalTime>,

    /// What happens to this clip's audio under a speed change. Almost always `mute` for
    /// slow motion, because pitch-shifted ambient sounds broken. `mute` suppresses this
    /// clip's ambient bed entirely, whatever AmbientPlan says about it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio_handling: Option<TimeEffectAudioHandling>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ToneMapOperator {
    #[serde(rename = "hable")]
    Hable,

    #[serde(rename = "reinhard")]
    Reinhard,

    #[serde(rename = "mobius")]
    Mobius,
}

/// How HDR light is fitted into the SDR output volume. Required exactly when some
/// source carries an HDR encoding; null otherwise, because a tone map over SDR sources
/// is a grade nobody asked for.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ToneMap {
    /// Which curve. Formulas are written out in this def's $comment and are normative.
    pub operator: ToneMapOperator,

    /// The operator's single shape parameter: reinhard's contrast, mobius's linear-section
    /// end. Null for hable, which has none. There is no default -- a curve parameter a
    /// renderer supplies is a curve nobody chose. The range is OPEN AT BOTH ENDS for both
    /// parameterised operators, and 1 is excluded because it is degenerate rather than
    /// merely extreme: see the $comment.
    pub operator_param: Option<f64>,

    /// What 1.0 means in the working space, in cd/m^2. 100 is SDR diffuse white (BT.1886);
    /// 203 is the BT.2100 HLG graphic-white convention. Required because it is the scale
    /// every other number in this object is expressed against.
    pub reference_white_nits: f64,

    /// Threshold, in units of reference white, above which a pixel is pulled towards its
    /// own luminance before mapping. 0 disables it. See the $comment for the exact formula.
    pub desaturation: f64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TrackKind {
    #[serde(rename = "video")]
    Video,

    #[serde(rename = "audio")]
    Audio,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TrackRole {
    #[serde(rename = "primary")]
    Primary,

    #[serde(rename = "overlay")]
    Overlay,

    #[serde(rename = "titles")]
    Titles,

    #[serde(rename = "ambient")]
    Ambient,

    #[serde(rename = "music")]
    Music,

    #[serde(rename = "voiceover")]
    Voiceover,

    #[serde(rename = "sfx")]
    Sfx,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Track {
    pub track_id: Slug,

    /// Maps to OTIO Track.kind, which recognises exactly "Video" and "Audio".
    pub kind: TrackKind,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,

    /// What the track is for. Purely ours -- it rides in metadata -- but it lets the
    /// renderer build the filtergraph without inspecting contents.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub role: Option<TrackRole>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,

    /// Ordered children. Clips and gaps tile the track; transitions sit between two
    /// neighbours and overlap them.
    pub items: Vec<TrackItemsItem>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TransitionTransitionType {
    #[serde(rename = "dissolve")]
    Dissolve,

    #[serde(rename = "dip_to_black")]
    DipToBlack,

    #[serde(rename = "dip_to_white")]
    DipToWhite,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum TransitionEasing {
    #[serde(rename = "linear")]
    Linear,

    #[serde(rename = "ease_in")]
    EaseIn,

    #[serde(rename = "ease_out")]
    EaseOut,

    #[serde(rename = "ease_in_out")]
    EaseInOut,
}

/// An overlap between the two neighbouring items. A hard cut is the absence of one of
/// these, never a zero-length instance -- that is OTIO's convention and departing from
/// it breaks the round trip.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Transition {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transition_id: Option<Slug>,

    /// `dissolve` maps to OTIO's standard SMPTE_Dissolve. The two dips map to OTIO "Custom"
    /// with the specific kind preserved in metadata, which is how OTIO itself handles non-
    /// standard transitions. See this def's $comment for why the enum is only three values.
    pub transition_type: TransitionTransitionType,

    /// How far the transition extends backwards into the outgoing item.
    pub in_offset: RationalTime,

    /// How far it extends forwards into the incoming item.
    pub out_offset: RationalTime,

    /// Shape of the blend weight across the transition. Each value is a polynomial in the
    /// linear progress u, written out in this def's $comment; a name on its own is not a
    /// curve, and a cubic ease and a sine ease are different shots.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub easing: Option<TransitionEasing>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum VariantInfoStrategy {
    #[serde(rename = "moment_subset")]
    MomentSubset,

    #[serde(rename = "pacing_seed")]
    PacingSeed,

    #[serde(rename = "energy_template")]
    EnergyTemplate,

    #[serde(rename = "music_alternate")]
    MusicAlternate,

    #[serde(rename = "reframe_style")]
    ReframeStyle,

    #[serde(rename = "duration_alternate")]
    DurationAlternate,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct VariantInfo {
    pub variant_id: Slug,

    pub variant_index: i64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sibling_edl_ids: Option<Vec<Blake3Hash>>,

    /// What was varied. Variants must differ along a stated axis, so the user's pick is
    /// interpretable as a preference rather than as noise.
    pub strategy: VariantInfoStrategy,

    /// One line the variant picker shows: 'faster, more action'.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EDLKind {
    #[serde(rename = "reel")]
    Reel,

    #[serde(rename = "film")]
    Film,

    #[serde(rename = "highlight")]
    Highlight,

    #[serde(rename = "chapter_preview")]
    ChapterPreview,

    #[serde(rename = "custom")]
    Custom,
}

/// The deterministic edit plan for one video output. Every creative decision in the
/// finished film or reel is expressed here; the renderer executes it and decides
/// nothing.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EDL {
    pub schema_version: SchemaVersion,

    /// BLAKE3 over the canonical JSON of this EDL with the volatile fields removed. Two
    /// EDLs with the same id render identically.
    pub edl_id: Blake3Hash,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,

    pub kind: EDLKind,

    /// Timeline rate in units per second. All video-track RationalTimes are expressed at
    /// this rate. Use exact NTSC rationals where the source demands it (30000/1001), never
    /// the rounded decimal.
    pub rate: f64,

    /// Timeline zero, exported as OTIO Timeline.global_start_time. Normally 0; non-zero
    /// when the output must carry a broadcast start timecode such as 01:00:00:00.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub global_start_time: Option<RationalTime>,

    pub target: RenderTarget,

    /// Every source this EDL touches, addressed by content hash. Declared once at the top
    /// so a renderer can resolve, verify and pre-open all sources before it starts, and so
    /// a missing source is a clean up-front failure rather than a crash at 80%.
    pub media_refs: Vec<MediaRef>,

    /// Ordered tracks. Index 0 is the bottom video layer, matching OTIO Stack ordering.
    pub tracks: Vec<Track>,

    /// Crop/reframe keyframe tracks, referenced by clips. Held at EDL level rather than
    /// inline on the clip so one subject-lock track can drive several clips from the same
    /// source shot.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reframe_tracks: Option<Vec<ReframeTrack>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio_plan: Option<AudioPlan>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub beat_grid: Option<BeatGrid>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub story_arc: Option<StoryArc>,

    /// REQUIRED and non-null (contracts#58). It was nullable, and a null colour pipeline is
    /// a plan that declines to say what colour its own output is -- which leaves the
    /// renderer to decide, which is the whole defect. Every EDL states its colour path,
    /// including the ordinary all-SDR one, where the statement is short and the pipeline is
    /// an identity.
    pub color_pipeline: ColorPipeline,

    /// Present when this EDL is one of several alternatives offered to the user. The reel
    /// planner emits 3-5; whichever the user picks becomes a PrefEvent, and the losers are
    /// training signal too.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub variant: Option<VariantInfo>,

    pub determinism: Determinism,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub validation: Option<EdlValidation>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub otio: Option<OtioExportInfo>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ClusterMembershipMethod {
    #[serde(rename = "hdbscan_cosine")]
    HdbscanCosine,

    #[serde(rename = "agglomerative_cosine")]
    AgglomerativeCosine,

    #[serde(rename = "user_grouped")]
    UserGrouped,

    #[serde(rename = "singleton")]
    Singleton,
}

/// Unsupervised grouping over embedding distance (HDBSCAN over cosine). A cluster is a
/// hypothesis, not an identity: it is allowed to be wrong, which is exactly why it is
/// stored separately from `identity`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ClusterMembership {
    pub cluster_id: Uuid,

    pub method: ClusterMembershipMethod,

    /// HDBSCAN membership probability. Low values sit near the decision boundary and are
    /// precisely the ones the active-learning loop should ask a human about first -- ten
    /// well-chosen taps fix a thousand photos.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub membership_strength: Option<Unit>,

    /// HDBSCAN noise point: too far from any cluster. Never surfaces in automated output.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_noise: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub distance_to_centroid: Option<f64>,

    /// Which clustering pass produced this. Re-clustering a growing library reshuffles
    /// cluster ids; pinning the run makes the reshuffle auditable instead of mysterious.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub clustering_run_id: Option<Slug>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum DetectionDetectedOn {
    #[serde(rename = "thumbnail_512")]
    Thumbnail512,

    #[serde(rename = "preview_2048")]
    Preview2048,

    #[serde(rename = "video_proxy_480p")]
    VideoProxy480p,

    #[serde(rename = "original")]
    Original,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Detection {
    /// Normalised against the ORIENTED frame, so the same box is valid on the 512px
    /// thumbnail the detector saw and on the 6000px original the renderer will crop.
    pub bbox: NormalizedBox,

    pub detection_score: Confidence,

    pub detector: ModelRef,

    /// Which rendition the detector ran against. Small faces missed on a thumbnail are re-
    /// detected at full resolution on demand; recording this makes 'have we already looked
    /// properly' answerable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detected_on: Option<DetectionDetectedOn>,

    /// Fraction of the frame the box covers. The single best predictor of whether an
    /// embedding will be trustworthy, and a hard input to the automated-output threshold.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_area_ratio: Option<Unit>,
}

/// Everything that decides whether this is a GOOD photo of this person, as opposed to
/// whether it is this person. Feeds face-quality scoring, album hero selection, and the
/// 'is anyone blinking' check.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FaceAttributes {
    /// Head pose. Beyond about +/-45 degrees yaw, recognition confidence degrades sharply
    /// and the automated-output threshold should tighten.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub yaw_deg: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pitch_deg: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub roll_deg: Option<f64>,

    /// Probability both eyes are open. The blink check that saves an album spread.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub eyes_open: Option<Confidence>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub smile: Option<Confidence>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mouth_open: Option<Confidence>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gaze_on_camera: Option<Confidence>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sharpness: Option<Unit>,

    /// How much of the face is hidden by a hand, hair, mask or another person.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub occlusion: Option<Unit>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wearing_sunglasses: Option<Confidence>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wearing_mask: Option<Confidence>,

    /// Fused face quality, the value album selection actually sorts on.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quality: Option<Score>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FaceTrack {
    pub track_id: Uuid,

    /// Source-time span the track covers.
    pub track_range: TimeRange,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub position_in_track: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub track_length: Option<i64>,

    /// The single best frame of this track, chosen by face quality. Identity is decided
    /// once per track from the representative, not voted per frame -- 120 correlated votes
    /// are not 120 pieces of evidence.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_track_representative: Option<bool>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IdentityAssignment {
    #[serde(rename = "unassigned")]
    Unassigned,

    #[serde(rename = "user_confirmed")]
    UserConfirmed,

    #[serde(rename = "user_rejected")]
    UserRejected,

    #[serde(rename = "auto_high_confidence")]
    AutoHighConfidence,

    #[serde(rename = "auto_below_threshold")]
    AutoBelowThreshold,

    #[serde(rename = "review_queued")]
    ReviewQueued,

    #[serde(rename = "ambiguous_multiple_candidates")]
    AmbiguousMultipleCandidates,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IdentityThresholdProfile {
    #[serde(rename = "automated_output")]
    AutomatedOutput,

    #[serde(rename = "review_queue")]
    ReviewQueue,

    #[serde(rename = "search_only")]
    SearchOnly,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct IdentityCandidatesItem {
    pub person_id: Uuid,

    pub confidence: Confidence,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IdentityReviewReason {
    #[serde(rename = "below_threshold")]
    BelowThreshold,

    #[serde(rename = "near_boundary")]
    NearBoundary,

    #[serde(rename = "multiple_candidates")]
    MultipleCandidates,

    #[serde(rename = "new_cluster")]
    NewCluster,

    #[serde(rename = "user_reported_error")]
    UserReportedError,

    #[serde(rename = "low_face_quality")]
    LowFaceQuality,

    #[serde(rename = "extreme_pose")]
    ExtremePose,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum IdentityDecidedBy {
    #[serde(rename = "model")]
    Model,

    #[serde(rename = "user")]
    User,

    #[serde(rename = "rule")]
    Rule,
}

/// Who we say this is, how sure we are, and whether that is sure enough to act on
/// unattended.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Identity {
    /// Null until an assignment exists. A person is a user-facing entity created by
    /// labeling, never by clustering alone.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub person_id: Option<Uuid>,

    /// How the person_id was arrived at. `auto_below_threshold` is a real, common state:
    /// the model has a guess it is not allowed to use, which is the entire point of
    /// precision-first.
    pub assignment: IdentityAssignment,

    /// Calibrated similarity-to-person confidence. Null when unassigned.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<Confidence>,

    /// Which operating point was applied. `automated_output` is the strict one tuned for
    /// >=99% precision (build plan section 7); `search_only` is permissive because a wrong
    /// hit in a search result is a shrug, not a catastrophe.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub threshold_profile: Option<IdentityThresholdProfile>,

    /// The actual numeric threshold applied, stored so that retuning the operating point is
    /// a replayable decision rather than a silent behaviour change.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub threshold_used: Option<Confidence>,

    /// THE GATE. Album, film and reel selection may only treat this face as a known person
    /// when this is true. Invariant, enforced in tests: true requires assignment to be
    /// user_confirmed, or auto_high_confidence with confidence >= threshold_used. Every
    /// other state is false.
    pub eligible_for_automated_output: bool,

    /// Runner-up people considered. Populated when assignment is
    /// ambiguous_multiple_candidates, which is the twins-and-siblings case that a single
    /// best-match number hides.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub candidates: Option<Vec<IdentityCandidatesItem>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub review_reason: Option<IdentityReviewReason>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decided_by: Option<IdentityDecidedBy>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decided_at: Option<Timestamp>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum LandmarksScheme {
    #[serde(rename = "insightface_5")]
    Insightface5,

    #[serde(rename = "insightface_106")]
    Insightface106,

    #[serde(rename = "mediapipe_468")]
    Mediapipe468,

    #[serde(rename = "yunet_5")]
    Yunet5,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Landmarks {
    /// Point count and ordering convention. Consumers must switch on this rather than
    /// assuming an index layout. yunet_5 and insightface_5 are both five points and are NOT
    /// interchangeable: feeding one to an alignment template built for the other produces a
    /// plausible warp and a wrong embedding, which is the worst failure mode in this system
    /// because nothing downstream can detect it.
    pub scheme: LandmarksScheme,

    pub points: Vec<Point2D>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub score: Option<Confidence>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SensitiveFlagsMinorStatus {
    #[serde(rename = "unknown")]
    Unknown,

    #[serde(rename = "estimated_minor")]
    EstimatedMinor,

    #[serde(rename = "confirmed_minor")]
    ConfirmedMinor,

    #[serde(rename = "confirmed_adult")]
    ConfirmedAdult,
}

/// Child-face labeling sits behind separate explicit consent (build plan section 8).
/// Modelled here rather than on the person so that the gate is evaluated at the point
/// of use.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SensitiveFlags {
    /// `unknown` is the default and is NOT treated as adult. Estimated age is a signal for
    /// asking the user, never a licence to proceed.
    pub minor_status: SensitiveFlagsMinorStatus,

    /// Required before a confirmed_minor face may be labeled with a person identity. Absent
    /// consent, the face is still detected and counted, but never named. Must be scoped to
    /// minor_face_labeling specifically -- a consent granted for cloud rendering does not
    /// authorise naming a child.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub labeling_consent: Option<ConsentRef>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub excluded_from_sharing: Option<bool>,
}

/// One detected face in one frame: where it is, what it looks like as an embedding,
/// which cluster it fell into, and -- separately and much more cautiously -- which
/// person we are willing to say it is.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FaceRecord {
    pub schema_version: SchemaVersion,

    /// BLAKE3 over (media_id, frame_time, quantised bbox, detector model_id + version).
    /// Content-addressed, so re-running the same detector on the same frame produces the
    /// same id and re-detection is idempotent. Changing detector version deliberately
    /// produces new ids rather than silently mutating old ones.
    ///
    /// CANONICAL ENCODING (issue #34), because 'BLAKE3 over the tuple' does not determine
    /// the bytes and every writer picked its own. The hashed byte string is exactly:
    ///
    /// face_id = BLAKE3( utf8( DOMAIN US media_id US TIME US BBOX US MODEL_ID US VERSION )
    /// )
    ///
    /// where US is U+001F INFORMATION SEPARATOR ONE, written once between adjacent fields
    /// and nowhere else, and the six fields are:
    ///
    /// DOMAIN -- the literal ASCII string 'face:v1'. This versions THIS ENCODING, not the
    /// detector. Changing the encoding means bumping it, so a re-encoding produces new ids
    /// on purpose rather than colliding with old ones by accident.
    ///
    /// media_id -- the 64 lowercase hex characters, verbatim.
    ///
    /// TIME -- the EMPTY STRING when frame_time is null, i.e. for a still. Otherwise
    /// `<value>/<rate>`, each number rendered in RFC 8785 / ECMAScript Number::toString
    /// form, which is the same numeric rule edl_id already uses. So 1001 and 1001.0 both
    /// render as `1001`, and a rate of 30000/1001 renders as `29.97002997002997`. THIS IS
    /// THE FIELD THAT BREAKS FIRST ACROSS LANGUAGES: Python's repr writes `1.0` where
    /// JavaScript writes `1`, and the same frame then gets two ids. A number that cannot be
    /// rendered without an exponent is REJECTED rather than written, because exponent
    /// formatting is where the two languages stop agreeing and no real frame rate needs
    /// one. The still case cannot be confused with the video case: a rendered time always
    /// contains a `/`, and the empty string never does.
    ///
    /// BBOX -- `<qx>,<qy>,<qw>,<qh>` where q(v) = round_half_away_from_zero(v * 10000),
    /// rendered as a base-10 integer with no padding and no sign (every component is non-
    /// negative by schema, so half-away-from-zero and half-up coincide). NOT banker's
    /// rounding: Python's round() sends 3002.5 to 3002 while JavaScript's Math.round and
    /// Rust's f64::round both send it to 3003, and 8855 of the 10000 half-quantum positions
    /// in [0,1] are exactly representable as doubles, so this is reachable rather than
    /// theoretical. The quantum is 1e-4 of the frame -- 0.6px on a 6000px original, finer
    /// than any detector's own precision and coarse enough that the last-bit disagreement
    /// between two execution providers cannot turn one face into two. `rotation_deg` does
    /// NOT participate, which is why Detection pins it to 0.
    ///
    /// MODEL_ID, VERSION -- detection.detector.model_id and .version, verbatim. model_id is
    /// a Slug and cannot contain the separator; ModelRef.version is pattern-constrained to
    /// exclude control characters for exactly this reason, so the join is injective and
    /// needs no length prefix.
    ///
    /// DELIBERATELY NOT IN THE TUPLE: weights_blake3, config_blake3, runtime, precision,
    /// detected_on, detection_score, landmarks and embedding. All of them are still
    /// RECORDED, on the detector ModelRef and in model_runs, so provenance is not lost --
    /// but none of them may move the id. weights_blake3 is nullable in development mode, so
    /// including it would rename every face the moment the same detection is re-recorded
    /// against a pinned registry: duplicated rows where deduplication was the point.
    /// config_blake3 moves when a score threshold moves, which changes WHICH faces are
    /// found rather than the identity of one that was found -- and a config change that
    /// really does move a box further than the quantum already changes BBOX. `version` is
    /// the deliberate, human-controlled switch for 'issue new ids'.
    ///
    /// contracts/tests recomputes this for every face fixture and for
    /// contracts/vectors/face-id.json, in Python and again in TypeScript against the
    /// generated bindings. An identity that is asserted rather than computed is how issue
    /// #26's invented span_id happened, and this is the same failure one schema over.
    pub face_id: Blake3Hash,

    /// The MediaRecord this face was found in. For spanned video this is the assembly
    /// record, so a face track can cross a chapter boundary.
    pub media_id: Blake3Hash,

    /// Position within the video, in SOURCE time (already mapped back through the proxy
    /// frame index). Null for stills.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frame_time: Option<RationalTime>,

    /// Face track membership for video. One person walking through a 4-second shot is ~120
    /// FaceRecords sharing a track_id; the ranking and moment layers work on tracks, not
    /// frames.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub track: Option<FaceTrack>,

    pub detection: Detection,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub landmarks: Option<Landmarks>,

    /// Recognition embedding. Null when the face was detected but was too small, too
    /// blurred or too occluded to embed reliably -- an unembeddable face is still worth
    /// recording, because it counts toward 'how many people are in this photo'.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub embedding: Option<VectorRef>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attributes: Option<FaceAttributes>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cluster: Option<ClusterMembership>,

    pub identity: Identity,

    pub sensitive: SensitiveFlags,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_runs: Option<Vec<ModelRun>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at: Option<Timestamp>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<Timestamp>,
}

/// Durable resumption state. The cursor is opaque on purpose: the contract promises to
/// persist and return it, and declines to speculate about what a directory walker or a
/// video encoder needs to remember.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Checkpoint {
    /// False for jobs that must restart from zero -- a short atomic render, say. Stating it
    /// explicitly stops a scheduler from assuming either way.
    pub resumable: bool,

    /// Worker-owned opaque state. Null means 'resumable but not yet started'.
    pub cursor: Option<String>,

    /// Bumped when a worker changes its cursor format. A cursor from an older version is
    /// discarded and the job restarts, rather than being handed to code that will misparse
    /// it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub checkpoint_version: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<Timestamp>,

    /// Inputs already finished. On resume these are skipped, which is what stops a resumed
    /// 3TB scan from rehashing the first 2TB.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completed_input_ids: Option<Vec<Blake3Hash>>,

    /// Outputs written before the interruption. Recorded so they are neither orphaned nor
    /// recreated.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub partial_output_ids: Option<Vec<Blake3Hash>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EgressDeclarationDestination {
    #[serde(rename = "tier3_inference")]
    Tier3Inference,

    #[serde(rename = "cloud_render")]
    CloudRender,

    #[serde(rename = "billing")]
    Billing,

    #[serde(rename = "sync")]
    Sync,

    #[serde(rename = "share")]
    Share,

    #[serde(rename = "print_vendor")]
    PrintVendor,

    #[serde(rename = "telemetry")]
    Telemetry,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EgressDeclarationPayloadKind {
    #[serde(rename = "contact_sheet")]
    ContactSheet,

    #[serde(rename = "thumbnail")]
    Thumbnail,

    #[serde(rename = "feature_vector")]
    FeatureVector,

    #[serde(rename = "metadata_only")]
    MetadataOnly,

    #[serde(rename = "structured_decision")]
    StructuredDecision,

    #[serde(rename = "original_media")]
    OriginalMedia,

    #[serde(rename = "rendered_output")]
    RenderedOutput,
}

/// Whether this job talks to the network, and on whose authority. Declared on every job
/// including the overwhelming majority that declare `false`, because an absent
/// declaration and a negative one must not look the same.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EgressDeclaration {
    pub requires_egress: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub consent: Option<ConsentRef>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub destination: Option<EgressDeclarationDestination>,

    /// What actually leaves the device. `contact_sheet` and `thumbnail` are the only image-
    /// bearing values permitted for Tier 3, and `original_media` requires its own explicit
    /// consent scope -- originals never leave without one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload_kind: Option<EgressDeclarationPayloadKind>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub estimated_bytes: Option<i64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum JobErrorCode {
    #[serde(rename = "file_not_found")]
    FileNotFound,

    #[serde(rename = "file_unreadable")]
    FileUnreadable,

    #[serde(rename = "file_corrupt")]
    FileCorrupt,

    #[serde(rename = "zero_byte_file")]
    ZeroByteFile,

    #[serde(rename = "unsupported_codec")]
    UnsupportedCodec,

    #[serde(rename = "unsupported_format")]
    UnsupportedFormat,

    #[serde(rename = "symlink_loop")]
    SymlinkLoop,

    #[serde(rename = "permission_denied")]
    PermissionDenied,

    #[serde(rename = "disk_full")]
    DiskFull,

    #[serde(rename = "out_of_memory")]
    OutOfMemory,

    #[serde(rename = "gpu_unavailable")]
    GpuUnavailable,

    #[serde(rename = "model_load_failed")]
    ModelLoadFailed,

    #[serde(rename = "model_inference_failed")]
    ModelInferenceFailed,

    #[serde(rename = "timeout")]
    Timeout,

    #[serde(rename = "cancelled_by_user")]
    CancelledByUser,

    #[serde(rename = "dependency_failed")]
    DependencyFailed,

    #[serde(rename = "consent_missing")]
    ConsentMissing,

    #[serde(rename = "consent_revoked")]
    ConsentRevoked,

    #[serde(rename = "network_unavailable")]
    NetworkUnavailable,

    #[serde(rename = "rate_limited")]
    RateLimited,

    #[serde(rename = "validation_failed")]
    ValidationFailed,

    #[serde(rename = "internal_error")]
    InternalError,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JobError {
    pub code: JobErrorCode,

    /// Already redacted: no paths, no filenames, no EXIF. Crash reporting forwards this
    /// verbatim, so redaction happens at write time rather than at send time.
    pub message: String,

    pub retryable: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attempt: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub occurred_at: Option<Timestamp>,

    /// Which specific input broke, so a 300k-file scan reports one bad file rather than
    /// failing wholesale.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failed_input_id: Option<Blake3Hash>,
}

/// Everything the job reads, addressed by hash. Content addressing is what makes the
/// whole pipeline idempotent: identical inputs cannot produce a different job.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JobInputs {
    /// Sorted before hashing into job_id, so input order never changes job identity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub media_ids: Option<Vec<Blake3Hash>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub moment_ids: Option<Vec<Blake3Hash>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_ids: Option<Vec<Blake3Hash>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub edl_id: Option<Blake3Hash>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub album_id: Option<Blake3Hash>,

    /// Only for scan_source, which by definition starts before anything has a hash. Every
    /// other job type addresses content, never location.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_paths: Option<Vec<String>>,

    /// BLAKE3 over the canonical form of `source_paths`: each path resolved to absolute,
    /// symlinks followed, trailing separators stripped, NFC-normalised, then sorted and
    /// joined with a NUL separator. Required for scan_source and the only thing
    /// distinguishing two scans of different folders, since neither has content hashes yet.
    /// Canonicalisation matters as much as the digest -- '/Volumes/Archive' and
    /// '/Volumes/Archive/' must not be two jobs, or a re-scan re-walks a whole drive.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_locator_digest: Option<Blake3Hash>,

    /// The job that spawned this one. A scan spawns a hash job per file; the tree is what
    /// makes 'cancel this import' a well-defined operation.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent_job_id: Option<Blake3Hash>,

    /// Jobs that must reach completed before this one may start.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub depends_on_job_ids: Option<Vec<Blake3Hash>>,

    /// Model pins. Part of the params digest, so re-running with a swapped model is a
    /// different job and the old result is never silently reused.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub models: Option<Vec<ModelRef>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum JobOutputKind {
    #[serde(rename = "media_record")]
    MediaRecord,

    #[serde(rename = "face_record")]
    FaceRecord,

    #[serde(rename = "moment_record")]
    MomentRecord,

    #[serde(rename = "edl")]
    Edl,

    #[serde(rename = "album_spec")]
    AlbumSpec,

    #[serde(rename = "proxy")]
    Proxy,

    #[serde(rename = "rendered_video")]
    RenderedVideo,

    #[serde(rename = "rendered_pdf")]
    RenderedPdf,

    #[serde(rename = "otio_file")]
    OtioFile,

    #[serde(rename = "vector_index_entry")]
    VectorIndexEntry,

    #[serde(rename = "eval_report")]
    EvalReport,

    #[serde(rename = "pref_event")]
    PrefEvent,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JobOutput {
    pub kind: JobOutputKind,

    /// Content hash of the produced artifact, so an output can be verified rather than
    /// trusted.
    pub id: Blake3Hash,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub byte_size: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub produced_at: Option<Timestamp>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum JobRequirementsCompute {
    #[serde(rename = "cpu")]
    Cpu,

    #[serde(rename = "gpu")]
    Gpu,

    #[serde(rename = "neural_engine")]
    NeuralEngine,

    #[serde(rename = "any")]
    Any,
}

/// What the job needs to run. The scheduler matches these against the machine rather
/// than discovering mid-render that there is no GPU.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JobRequirements {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compute: Option<JobRequirementsCompute>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_vram_mb: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_ram_mb: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_disk_mb: Option<i64>,

    /// Proxy generation must saturate disk I/O rather than CPU, which is only possible with
    /// VideoToolbox/NVDEC/QSV. A proxy job that would fall back to software decode should
    /// queue rather than crawl.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hardware_decode: Option<bool>,

    /// True only for proxy generation and final render. Everything else works on proxies --
    /// sources are opened exactly twice in a file's life.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub requires_source_file: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub estimated_duration_ms: Option<f64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum JobStateStatus {
    #[serde(rename = "pending")]
    Pending,

    #[serde(rename = "blocked")]
    Blocked,

    #[serde(rename = "running")]
    Running,

    #[serde(rename = "paused")]
    Paused,

    #[serde(rename = "completed")]
    Completed,

    #[serde(rename = "failed")]
    Failed,

    #[serde(rename = "cancelled")]
    Cancelled,

    #[serde(rename = "quarantined")]
    Quarantined,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JobState {
    /// `paused` is a deliberate user action; `pending` after a crash is what a killed
    /// `running` job becomes on relaunch. Distinguishing them is what makes resumption safe
    /// -- a paused job must not restart itself.
    pub status: JobStateStatus,

    pub attempts: i64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub worker_id: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub started_at: Option<Timestamp>,

    /// Last sign of life. A running job whose heartbeat has gone stale was killed with the
    /// process and is safe to reclaim -- without it, a crashed job is indistinguishable
    /// from a slow one and blocks its queue forever.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub heartbeat_at: Option<Timestamp>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub finished_at: Option<Timestamp>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub progress: Option<Progress>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum JournalEntriesItemAction {
    #[serde(rename = "delete_file")]
    DeleteFile,

    #[serde(rename = "overwrite_file")]
    OverwriteFile,

    #[serde(rename = "move_file")]
    MoveFile,

    #[serde(rename = "prune_proxy")]
    PruneProxy,

    #[serde(rename = "network_send")]
    NetworkSend,

    #[serde(rename = "network_receive")]
    NetworkReceive,

    #[serde(rename = "consent_recorded")]
    ConsentRecorded,

    #[serde(rename = "consent_revoked")]
    ConsentRevoked,

    #[serde(rename = "model_swapped")]
    ModelSwapped,

    #[serde(rename = "index_rebuilt")]
    IndexRebuilt,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JournalEntriesItem {
    pub action: JournalEntriesItemAction,

    pub at: Timestamp,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_id: Option<Blake3Hash>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reversible: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

/// Record of every destructive or externally-visible action the job took. 'No silent
/// data loss' means anything irreversible is written down before it happens, not after
/// it succeeds.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Journal {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub entries: Option<Vec<JournalEntriesItem>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ProgressUnit {
    #[serde(rename = "files")]
    Files,

    #[serde(rename = "bytes")]
    Bytes,

    #[serde(rename = "frames")]
    Frames,

    #[serde(rename = "seconds")]
    Seconds,

    #[serde(rename = "images")]
    Images,

    #[serde(rename = "moments")]
    Moments,

    #[serde(rename = "pages")]
    Pages,

    #[serde(rename = "items")]
    Items,
}

/// Progress in real units, not a synthetic percentage. '12,400 of 318,000 files'
/// survives a restart honestly; a percentage that jumps backwards does not.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Progress {
    pub units_done: f64,

    /// Null while still being discovered -- a scan does not know how many files exist until
    /// it has walked them, and claiming otherwise produces a progress bar that lies.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub units_total: Option<f64>,

    pub unit: ProgressUnit,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bytes_processed: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RetryPolicyBackoff {
    #[serde(rename = "none")]
    None,

    #[serde(rename = "linear")]
    Linear,

    #[serde(rename = "exponential")]
    Exponential,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RetryPolicy {
    pub max_attempts: i64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub backoff: Option<RetryPolicyBackoff>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub initial_delay_ms: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_delay_ms: Option<f64>,

    /// Move to quarantined rather than failed once attempts are exhausted, so a hostile
    /// file is never retried automatically again but is still visible in the diagnostics
    /// view. Nothing is dropped silently.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quarantine_after_max: Option<bool>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum JobSpecJobType {
    #[serde(rename = "scan_source")]
    ScanSource,

    #[serde(rename = "hash_file")]
    HashFile,

    #[serde(rename = "extract_metadata")]
    ExtractMetadata,

    #[serde(rename = "generate_thumbnail")]
    GenerateThumbnail,

    #[serde(rename = "generate_video_proxy")]
    GenerateVideoProxy,

    #[serde(rename = "perceptual_hash")]
    PerceptualHash,

    #[serde(rename = "analyze_image")]
    AnalyzeImage,

    #[serde(rename = "analyze_video")]
    AnalyzeVideo,

    #[serde(rename = "detect_faces")]
    DetectFaces,

    #[serde(rename = "cluster_faces")]
    ClusterFaces,

    #[serde(rename = "transcribe_audio")]
    TranscribeAudio,

    #[serde(rename = "detect_shots")]
    DetectShots,

    #[serde(rename = "score_moments")]
    ScoreMoments,

    #[serde(rename = "rank_media")]
    RankMedia,

    #[serde(rename = "dedupe_cluster")]
    DedupeCluster,

    #[serde(rename = "cluster_events")]
    ClusterEvents,

    #[serde(rename = "plan_reel")]
    PlanReel,

    #[serde(rename = "plan_film")]
    PlanFilm,

    #[serde(rename = "plan_album")]
    PlanAlbum,

    #[serde(rename = "tier3_request")]
    Tier3Request,

    #[serde(rename = "enhance_image")]
    EnhanceImage,

    #[serde(rename = "render_video")]
    RenderVideo,

    #[serde(rename = "render_print")]
    RenderPrint,

    #[serde(rename = "export_otio")]
    ExportOtio,

    #[serde(rename = "eval_run")]
    EvalRun,

    #[serde(rename = "reindex_vectors")]
    ReindexVectors,

    #[serde(rename = "consent_export")]
    ConsentExport,
}

/// Any unit of work in the system: what to do, to which inputs, with which parameters,
/// how far it got, and how to pick it up again.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JobSpec {
    pub schema_version: SchemaVersion,

    /// BLAKE3 over (job_type, sorted input ids, source_locator_digest or the empty string,
    /// params_digest, scope). Doubles as the idempotency key -- there is deliberately no
    /// second field for that, because two sources of truth for identity is how duplicate
    /// work gets in.
    ///
    /// CANONICAL ENCODING, for the same reason face_id and span_id have one: a named tuple
    /// is not a byte string, and two workers that separate the fields differently compute
    /// different ids for identical work -- which means the second one redoes it, or worse,
    /// a genuinely different job collides with a completed one and is skipped. The hashed
    /// bytes are:
    ///
    /// job_id = BLAKE3( utf8( job_type US IDS US LOCATOR US params_digest US scope ) )
    ///
    /// where US is U+001F INFORMATION SEPARATOR ONE. IDS is the media ids followed by the
    /// moment ids, sorted as one list and joined with a single comma; empty when there are
    /// none. LOCATOR is source_locator_digest, or the EMPTY STRING when it is null --
    /// absent and empty must render the same way, or a job with no locator gets two ids.
    /// scope renders as the empty string when null.
    ///
    /// The fields are all fixed-alphabet (hex digests, an enumerated job_type, a slug-
    /// shaped scope), so the separator is sufficient and no length prefix is needed.
    pub job_id: Blake3Hash,

    /// What kind of work. Enumerated rather than free-form so that a worker cannot be
    /// handed a job type it has never heard of and improvise.
    pub job_type: JobSpecJobType,

    pub inputs: JobInputs,

    /// Type-specific parameters. Free-form because the parameter shape of `plan_reel` and
    /// `hash_file` have nothing in common, but never anonymous: params_digest pins it. A
    /// worker validates its own params against its own local schema.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<BTreeMap<String, serde_json::Value>>,

    /// BLAKE3 over the canonical JSON of `params`. Part of job_id, which is what makes
    /// 'same job with different settings' a genuinely different job rather than a silent
    /// overwrite of the first result.
    ///
    /// Canonical JSON here is the same rule `edl_id` states: keys sorted, no insignificant
    /// whitespace, numbers in RFC 8785 / ECMAScript Number::toString form so that 1.0 and 1
    /// are one value, UTF-8 bytes. One canonicalisation for the whole contract,
    /// deliberately -- a second one is how a digest starts disagreeing with itself across
    /// languages.
    ///
    /// EVERYTHING THAT CHANGES THE RESULT MUST BE IN `params`. In particular the MODEL
    /// PINS: naming a model by id alone meant that editing its config -- a detection
    /// threshold, an NMS IoU -- left job_id unchanged, so the completed job was found and
    /// every already-analysed record skipped. The library kept an analysis produced by
    /// settings it was no longer configured with, and nothing said so. `inputs.models`
    /// carries the same pins for provenance; the copy in `params` is the one that affects
    /// identity, and a writer that fills one and not the other has a bug.
    pub params_digest: Blake3Hash,

    /// Namespace separating otherwise-identical work: a library id, a project id, or a user
    /// id. Without it, two users analysing the same stock photo would collide on job_id.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scope: Option<String>,

    /// Higher runs first. Interactive work (the user is watching a progress bar) outranks
    /// background sweeps.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub priority: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub requirements: Option<JobRequirements>,

    pub egress: EgressDeclaration,

    pub state: JobState,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub checkpoint: Option<Checkpoint>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outputs: Option<Vec<JobOutput>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<JobError>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub retry_policy: Option<RetryPolicy>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub journal: Option<Journal>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at: Option<Timestamp>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deadline: Option<Timestamp>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AudioStream {
    pub stream_index: i64,

    pub channels: i64,

    pub sample_rate: i64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub codec: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,

    /// Detected during proxy generation. A silent track means the ambient mix has nothing
    /// to preserve and music can sit at full level.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_silent: Option<bool>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum CaptureMetadataPresentItem {
    #[serde(rename = "exif")]
    Exif,

    #[serde(rename = "xmp")]
    Xmp,

    #[serde(rename = "iptc")]
    Iptc,

    #[serde(rename = "quicktime")]
    Quicktime,

    #[serde(rename = "gopro_gpmf")]
    GoproGpmf,

    #[serde(rename = "takeout_json")]
    TakeoutJson,

    #[serde(rename = "maker_note")]
    MakerNote,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Capture {
    /// Always a TimeAssertion, never a bare timestamp. A file with no EXIF gets an
    /// assertion with source 'unknown' and precision 'unknown', not a fabricated date.
    pub captured_at: TimeAssertion,

    /// Which metadata blocks were actually found. Empty array is the EXIF-less case and is
    /// completely normal for WhatsApp media and screenshots.
    pub metadata_present: Vec<CaptureMetadataPresentItem>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gps: Option<GeoPoint>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub device: Option<DeviceInfo>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exposure: Option<ExposureInfo>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ContentAnalysisTagsItemSource {
    #[serde(rename = "zero_shot_siglip")]
    ZeroShotSiglip,

    #[serde(rename = "ocr")]
    Ocr,

    #[serde(rename = "exif")]
    Exif,

    #[serde(rename = "user")]
    User,

    #[serde(rename = "tier2_vlm")]
    Tier2Vlm,

    #[serde(rename = "tier3_model")]
    Tier3Model,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ContentAnalysisTagsItem {
    pub label: String,

    pub score: Unit,

    pub source: ContentAnalysisTagsItemSource,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ContentAnalysisSceneType {
    #[serde(rename = "indoor")]
    Indoor,

    #[serde(rename = "outdoor")]
    Outdoor,

    #[serde(rename = "portrait")]
    Portrait,

    #[serde(rename = "landscape")]
    Landscape,

    #[serde(rename = "food")]
    Food,

    #[serde(rename = "document")]
    Document,

    #[serde(rename = "screenshot")]
    Screenshot,

    #[serde(rename = "night")]
    Night,

    #[serde(rename = "underwater")]
    Underwater,

    #[serde(rename = "aerial")]
    Aerial,

    #[serde(rename = "unknown")]
    Unknown,
}

/// Present when text was found. Screenshot and document detection ride on this, and
/// both are auto-excluded from memories.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ContentAnalysisOcr {
    pub has_text: bool,

    pub text_area_ratio: Unit,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub languages: Option<Vec<String>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_screenshot: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_document: Option<bool>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ContentAnalysis {
    /// One SigLIP embedding powers search, dedupe refinement, diversity constraints and
    /// zero-shot tagging. This single field is the highest-leverage thing in the record.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub embedding: Option<VectorRef>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tags: Option<Vec<ContentAnalysisTagsItem>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scene_type: Option<ContentAnalysisSceneType>,

    /// Present when text was found. Screenshot and document detection ride on this, and
    /// both are auto-excluded from memories.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ocr: Option<ContentAnalysisOcr>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub safety: Option<SafetyAssessment>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum DedupeMembershipMethod {
    #[serde(rename = "exact_content_hash")]
    ExactContentHash,

    #[serde(rename = "phash_bucket")]
    PhashBucket,

    #[serde(rename = "phash_bucket_embedding_refined")]
    PhashBucketEmbeddingRefined,

    #[serde(rename = "burst_metadata")]
    BurstMetadata,

    #[serde(rename = "user_grouped")]
    UserGrouped,
}

/// Near-duplicate grouping. Exactly one member of a group is primary; the ranking
/// engine picks it, and only the primary is eligible for automated output so a burst of
/// 12 near-identical frames contributes one photo, not twelve.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DedupeMembership {
    pub group_id: Uuid,

    pub is_primary: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub primary_media_id: Option<Blake3Hash>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub similarity_to_primary: Option<Unit>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_size: Option<i64>,

    pub method: DedupeMembershipMethod,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DeviceInfo {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub make: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lens: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub software: Option<String>,

    /// Hashed, never raw. A camera serial is a personal identifier; we want 'same body'
    /// equality without storing the number.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body_serial_hash: Option<Blake3Hash>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ErrorInfo {
    pub code: Slug,

    /// Human-readable, already redacted: no paths, no filenames, no EXIF. Crash reporting
    /// forwards this verbatim.
    pub message: String,

    pub retryable: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub occurred_at: Option<Timestamp>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExclusionStateReasonsItem {
    #[serde(rename = "screenshot")]
    Screenshot,

    #[serde(rename = "document")]
    Document,

    #[serde(rename = "nsfw")]
    Nsfw,

    #[serde(rename = "sensitive")]
    Sensitive,

    #[serde(rename = "corrupt")]
    Corrupt,

    #[serde(rename = "unreadable")]
    Unreadable,

    #[serde(rename = "duplicate_secondary")]
    DuplicateSecondary,

    #[serde(rename = "below_quality_floor")]
    BelowQualityFloor,

    #[serde(rename = "black_frame")]
    BlackFrame,

    #[serde(rename = "lens_obstructed")]
    LensObstructed,

    #[serde(rename = "too_short")]
    TooShort,

    #[serde(rename = "user_hidden")]
    UserHidden,

    #[serde(rename = "unsupported_codec")]
    UnsupportedCodec,
}

/// Whether this file may appear in unattended output. Separate from user hiding:
/// exclusion is a system judgement with a stated reason, and every reason is
/// individually overridable.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ExclusionState {
    pub excluded_from_automation: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasons: Option<Vec<ExclusionStateReasonsItem>>,

    /// Tri-state on purpose. null = no opinion, true = user forced it in, false = user
    /// forced it out. Distinguishing 'user said include' from 'system did not exclude'
    /// matters when the exclusion rules later change.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub user_override: Option<bool>,
}

/// Shot settings, used as priors by the technical quality pass: a 1/8s handheld
/// exposure predicts motion blur before any model runs.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ExposureInfo {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub iso: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exposure_time_s: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub f_number: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub focal_length_mm: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub focal_length_35mm: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub flash_fired: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metering_mode: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FaceSummary {
    pub face_count: i64,

    pub face_ids: Vec<Blake3Hash>,

    /// Only people whose assignment is eligible for automated output. A person appearing
    /// here is safe to use for 'album of Avika'; anything less certain is deliberately
    /// absent.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confirmed_person_ids: Option<Vec<Uuid>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pending_review_count: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub largest_face_area_ratio: Option<Unit>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum FrameIndexSidecarMapping {
    #[serde(rename = "identity")]
    Identity,

    #[serde(rename = "table")]
    Table,
}

/// Mapping from proxy time to source timecode. The intelligence layer works entirely in
/// proxy time; this is the single point where that is converted to something the
/// renderer can seek to in the original.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FrameIndexSidecar {
    pub path: String,

    pub entry_count: i64,

    /// `identity` when proxy and source share a frame timeline and no lookup is needed --
    /// the common CFR case. `table` when the sidecar must be consulted per frame, which is
    /// the VFR phone-video case.
    pub mapping: FrameIndexSidecarMapping,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_rate: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proxy_rate: Option<f64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ImagePropertiesColorSpace {
    #[serde(rename = "srgb")]
    Srgb,

    #[serde(rename = "display_p3")]
    DisplayP3,

    #[serde(rename = "adobe_rgb")]
    AdobeRgb,

    #[serde(rename = "prophoto_rgb")]
    ProphotoRgb,

    #[serde(rename = "rec2020")]
    Rec2020,

    #[serde(rename = "linear")]
    Linear,

    #[serde(rename = "unknown")]
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ImageProperties {
    /// Pixel dimensions as stored in the file, before EXIF orientation is applied.
    pub stored_size: PixelSize,

    /// Dimensions after orientation. Every NormalizedBox in the system is relative to THIS,
    /// which removes an entire class of rotated-crop bugs.
    pub oriented_size: PixelSize,

    /// EXIF orientation tag, 1-8.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub orientation: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bit_depth: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub color_space: Option<ImagePropertiesColorSpace>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub icc_profile_name: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub has_alpha: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_raw: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_hdr: Option<bool>,

    /// For a Live Photo or Motion Photo, the record holding the motion track.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub paired_motion_media_id: Option<Blake3Hash>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PerceptualFingerprintKeyframeHashesItem {
    pub time: RationalTime,

    pub hash: PerceptualHash,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PerceptualFingerprint {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub image_hash: Option<PerceptualHash>,

    /// Per-keyframe hashes for video, so a clip that appears in two exports of the same
    /// trip is recognised as duplicate footage.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub keyframe_hashes: Option<Vec<PerceptualFingerprintKeyframeHashesItem>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ProcessingStateState {
    #[serde(rename = "discovered")]
    Discovered,

    #[serde(rename = "hashed")]
    Hashed,

    #[serde(rename = "proxied")]
    Proxied,

    #[serde(rename = "analyzing")]
    Analyzing,

    #[serde(rename = "analyzed")]
    Analyzed,

    #[serde(rename = "failed")]
    Failed,

    #[serde(rename = "quarantined")]
    Quarantined,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ProcessingStateStages {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hash: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metadata: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub thumbnail: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub video_proxy: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub perceptual_hash: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub classical_quality: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub image_embedding: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_detection: Option<StageState>,

    /// Aligning each detected face onto the recognition model's template and embedding it.
    /// Separate from `face_detection` because they fail and resume separately: a detector
    /// that ran and an embedder that was missing must leave the library with face BOXES --
    /// which the print validator's trim-zone check needs and which have nothing to do with
    /// identity -- rather than with neither.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_embedding: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub iqa: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub aesthetic: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tagging: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub safety: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ocr: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub shot_detection: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transcription: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio_events: Option<StageState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub moment_scoring: Option<StageState>,
}

/// Per-stage pipeline state. Granular per stage rather than one status field because a
/// 3TB scan is killed and resumed constantly, and 'hashed but not yet proxied' has to
/// be a first-class, restartable position.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ProcessingState {
    /// Rollup for UI and query. `quarantined` means the file is unreadable or hostile
    /// (zero-byte, truncated, symlink loop) and must never be retried automatically.
    pub state: ProcessingStateState,

    pub stages: ProcessingStateStages,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ProxyRefKind {
    #[serde(rename = "thumbnail_512")]
    Thumbnail512,

    #[serde(rename = "preview_2048")]
    Preview2048,

    #[serde(rename = "video_proxy_480p")]
    VideoProxy480p,

    #[serde(rename = "waveform")]
    Waveform,

    #[serde(rename = "contact_sheet_tile")]
    ContactSheetTile,

    #[serde(rename = "audio_wav_16k")]
    AudioWav16k,
}

/// A derived rendition on local disk. Content-addressed like everything else, so a
/// proxy regenerated with the same tool version is recognised as the same artifact.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ProxyRef {
    pub proxy_id: Blake3Hash,

    pub kind: ProxyRefKind,

    pub path: String,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub size: Option<PixelSize>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub byte_size: Option<i64>,

    /// Tool + settings that produced it. A change here invalidates the proxy without
    /// deleting anything.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub generator_version: Option<String>,

    /// Present on video_proxy_480p only. The proxy is single-pass and may not be frame-
    /// exact against the source, so analysis results measured in proxy time must be mapped
    /// back through this sidecar before they can address source timecode.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frame_index: Option<FrameIndexSidecar>,
}

/// Technical quality, cheapest measures first. The classical measures alone reject most
/// of the junk for free (build plan 4.2), so they are required and the learned scores
/// are optional.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct QualityScores {
    /// Laplacian-variance derived, normalised. Low means blurred, whether from focus or
    /// motion.
    pub sharpness: Score,

    /// Histogram-derived. Penalises clipped highlights and crushed shadows; 1.0 is a well-
    /// distributed histogram.
    pub exposure: Score,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub noise: Option<Score>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub contrast: Option<Score>,

    /// Learned no-reference IQA (MUSIQ/TOPIQ class).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub technical_iqa: Option<Score>,

    /// Aesthetic prior. Explicitly a PRIOR: the ranking engine reweights this per user from
    /// PrefEvents, so it must never be treated as ground truth.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub aesthetic: Option<Score>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub composition: Option<Score>,

    /// Best face quality in the frame: eyes open, unblurred, forward-facing. Null when
    /// there are no faces.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_quality: Option<Score>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_black_frame: Option<bool>,

    /// Lens cap, pocket footage, finger over the lens. Cheap to detect and enormously
    /// common on action-camera cards.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_lens_obstructed: Option<bool>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SafetyAssessmentCategoriesItem {
    #[serde(rename = "nudity")]
    Nudity,

    #[serde(rename = "sexual")]
    Sexual,

    #[serde(rename = "violence")]
    Violence,

    #[serde(rename = "gore")]
    Gore,

    #[serde(rename = "medical")]
    Medical,

    #[serde(rename = "document_pii")]
    DocumentPii,

    #[serde(rename = "unknown")]
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SafetyAssessment {
    pub nsfw_score: Confidence,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub categories: Option<Vec<SafetyAssessmentCategoriesItem>>,

    /// Excluded by default from all automated output. The user can override per item; the
    /// override is recorded in UserAnnotations, never by mutating this field.
    pub auto_excluded: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub threshold_used: Option<Confidence>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SourceLocationAdapter {
    #[serde(rename = "filesystem")]
    Filesystem,

    #[serde(rename = "google_takeout")]
    GoogleTakeout,

    #[serde(rename = "icloud_export")]
    IcloudExport,

    #[serde(rename = "whatsapp")]
    Whatsapp,

    #[serde(rename = "gopro_card")]
    GoproCard,

    #[serde(rename = "dslr_card")]
    DslrCard,

    #[serde(rename = "phone_gallery")]
    PhoneGallery,

    #[serde(rename = "insta360")]
    Insta360,

    #[serde(rename = "drone_card")]
    DroneCard,

    #[serde(rename = "manual_import")]
    ManualImport,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SourceLocation {
    /// Absolute path as seen at scan time. Local only -- this string never leaves the
    /// device and is stripped by the crash-reporter privacy filter.
    pub path: String,

    /// Stable identifier for the containing volume, so an unplugged external drive is
    /// reported as 'offline' rather than 'deleted'.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub volume_id: Option<String>,

    /// Which ingest adapter found it. Drives source-specific metadata recovery: Takeout
    /// puts the real date in a sidecar JSON, WhatsApp puts it in the filename, and neither
    /// has usable EXIF.
    pub adapter: SourceLocationAdapter,

    /// Companion files: XMP, Takeout's .json, GoPro's .THM/.LRV, Live Photo's paired .MOV.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sidecar_paths: Option<Vec<String>>,

    /// Filename as it appeared, kept after any rename because WhatsApp and camera naming
    /// conventions are the only date source for a large share of real libraries.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub original_filename: Option<String>,

    pub first_seen_at: Timestamp,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_verified_at: Option<Timestamp>,

    /// False when the path no longer resolves. The record is retained: losing sight of a
    /// file is not permission to forget everything we learned about it (hard rule 7, no
    /// silent data loss).
    pub present: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SpanRole {
    #[serde(rename = "member")]
    Member,

    #[serde(rename = "assembly")]
    Assembly,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SpanSpanKind {
    #[serde(rename = "gopro_chapter")]
    GoproChapter,

    #[serde(rename = "dslr_size_split")]
    DslrSizeSplit,

    #[serde(rename = "insta360_lens_pair")]
    Insta360LensPair,

    #[serde(rename = "manual_group")]
    ManualGroup,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SpanContinuity {
    #[serde(rename = "verified_gapless")]
    VerifiedGapless,

    #[serde(rename = "verified_gap")]
    VerifiedGap,

    #[serde(rename = "unverified")]
    Unverified,

    #[serde(rename = "incomplete_set")]
    IncompleteSet,
}

/// Membership in a multi-file recording. Modelled as a role on each record rather than
/// a nested structure so that a chapter can be discovered, hashed and analysed before
/// its siblings have even been walked -- which is exactly what happens on a 400-file
/// GoPro card.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Span {
    /// BLAKE3 over the ordered member media_ids once the set is closed. Before closure, a
    /// provisional id derived from the camera's own group identifier (GoPro's file number,
    /// e.g. 1234 in GH011234.MP4). On the assembly record this is also the media_id -- the
    /// assembly has no bytes to hash, so its members' identity is its identity.
    ///
    /// CANONICAL ENCODING, because 'BLAKE3 over the ids' does not determine the bytes and
    /// three plausible readings gave three different identities: span_id = BLAKE3(
    /// concat(member_media_ids in index order) ) where each id contributes its 64 lowercase
    /// ASCII hex characters, with NO delimiter, NO length prefix and NO domain separator.
    ///
    /// The absence of a delimiter is safe rather than lucky: every Blake3Hash is exactly 64
    /// hex characters, so the concatenation is fixed-width and therefore prefix-free -- no
    /// two different member lists can produce the same byte string. A variable-length
    /// encoding would need a delimiter to avoid that, which is where this class of bug
    /// usually starts.
    ///
    /// ORDER IS INDEX ORDER, NOT SORTED ORDER. Chapters are a sequence: GH011234, GH021234,
    /// GH031234 concatenate into one recording in that order, and sorting by hash would
    /// scramble a timeline. The assembly's identity therefore changes if the chapters are
    /// reordered, which is correct -- a different order is a different recording.
    ///
    /// Codex raised this (issue #26) after finding that the golden fixture's span_id
    /// matched none of the plausible readings. It matched none of them because it had been
    /// written by hand rather than computed, so the fixture was not testing the identity at
    /// all. contracts/tests recomputes it now.
    pub span_id: Blake3Hash,

    /// `member` is a physical file on disk and always sits on an asset_kind of
    /// physical_file. `assembly` is the virtual record representing the concatenated
    /// recording; it is always asset_kind virtual_assembly, carries byte_size 0, no sources
    /// and no proxies, and is what MomentRecords and EDL clips reference so a cut can cross
    /// a chapter boundary without the planner knowing chapters exist.
    pub role: SpanRole,

    pub span_kind: SpanSpanKind,

    /// 0-based position within the recording. Required on members, null on the assembly.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub index: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub member_count: Option<i64>,

    /// Ordered member ids. Populated on the assembly record only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub member_media_ids: Option<Vec<Blake3Hash>>,

    /// Where this member starts within the assembly's timeline. Lets a MomentRecord on the
    /// assembly be resolved back to (file, timecode) at render without re-probing every
    /// chapter.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub offset_in_span: Option<RationalTime>,

    /// Whether the chapters were verified to be gapless. Cameras occasionally drop a frame
    /// at the split; the renderer must know before it concatenates.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub continuity: Option<SpanContinuity>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum StageStateStatus {
    #[serde(rename = "pending")]
    Pending,

    #[serde(rename = "running")]
    Running,

    #[serde(rename = "done")]
    Done,

    #[serde(rename = "failed")]
    Failed,

    #[serde(rename = "skipped")]
    Skipped,

    #[serde(rename = "not_applicable")]
    NotApplicable,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StageState {
    pub status: StageStateStatus,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attempts: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<Timestamp>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub job_id: Option<Blake3Hash>,

    /// Why a stage was skipped. Required reading for 'no silent anything': a skipped stage
    /// must be explicable without reading logs.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub skip_reason: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<ErrorInfo>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct UserAnnotations {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub favorite: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hidden: Option<bool>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rating: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tags: Option<Vec<String>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub caption: Option<String>,
}

/// Permitted values: 0, 90, 180, 270.
pub type VideoPropertiesRotationDeg = i64;

/// Exact frame rate as a rational. 30000/1001 must survive as such; storing 29.97 as a
/// float and reconstructing it later is how beat-locked cuts drift.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct VideoPropertiesFrameRate {
    pub numerator: i64,

    pub denominator: i64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct VideoProperties {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stored_size: Option<PixelSize>,

    pub oriented_size: PixelSize,

    /// Rotation from the container's display matrix. Phone video is almost always stored
    /// landscape with a rotation flag.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rotation_deg: Option<VideoPropertiesRotationDeg>,

    pub duration: RationalTime,

    /// Exact frame rate as a rational. 30000/1001 must survive as such; storing 29.97 as a
    /// float and reconstructing it later is how beat-locked cuts drift.
    pub frame_rate: VideoPropertiesFrameRate,

    /// True for most phone video. A VFR source must be conformed before frame-accurate
    /// cutting, and the renderer needs to be told, not surprised.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub is_variable_frame_rate: Option<bool>,

    /// Embedded SMPTE timecode track start, when present. Carried through to OTIO so a
    /// professional round-trip lands on the right frame.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub start_timecode: Option<RationalTime>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub video_codec: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bit_rate: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub color_primaries: Option<String>,

    /// HLG or PQ here means HDR footage, which changes both the enhancement plan and the
    /// encode profile.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transfer_characteristics: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio_streams: Option<Vec<AudioStream>>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MediaRecordAssetKind {
    #[serde(rename = "physical_file")]
    PhysicalFile,

    #[serde(rename = "virtual_assembly")]
    VirtualAssembly,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MediaRecordKind {
    #[serde(rename = "image")]
    Image,

    #[serde(rename = "video")]
    Video,

    #[serde(rename = "live_photo")]
    LivePhoto,

    #[serde(rename = "motion_photo")]
    MotionPhoto,

    #[serde(rename = "audio")]
    Audio,

    #[serde(rename = "sidecar")]
    Sidecar,

    #[serde(rename = "unknown")]
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MediaRecordFileFormat {
    #[serde(rename = "jpeg")]
    Jpeg,

    #[serde(rename = "png")]
    Png,

    #[serde(rename = "heic")]
    Heic,

    #[serde(rename = "heif")]
    Heif,

    #[serde(rename = "avif")]
    Avif,

    #[serde(rename = "webp")]
    Webp,

    #[serde(rename = "tiff")]
    Tiff,

    #[serde(rename = "dng")]
    Dng,

    #[serde(rename = "cr2")]
    Cr2,

    #[serde(rename = "cr3")]
    Cr3,

    #[serde(rename = "nef")]
    Nef,

    #[serde(rename = "arw")]
    Arw,

    #[serde(rename = "raf")]
    Raf,

    #[serde(rename = "orf")]
    Orf,

    #[serde(rename = "rw2")]
    Rw2,

    #[serde(rename = "gif")]
    Gif,

    #[serde(rename = "bmp")]
    Bmp,

    #[serde(rename = "mp4")]
    Mp4,

    #[serde(rename = "mov")]
    Mov,

    #[serde(rename = "avi")]
    Avi,

    #[serde(rename = "mkv")]
    Mkv,

    #[serde(rename = "webm")]
    Webm,

    #[serde(rename = "m4v")]
    M4v,

    #[serde(rename = "mts")]
    Mts,

    #[serde(rename = "3gp")]
    V3gp,

    #[serde(rename = "insv")]
    Insv,

    #[serde(rename = "wav")]
    Wav,

    #[serde(rename = "mp3")]
    Mp3,

    #[serde(rename = "m4a")]
    M4a,

    #[serde(rename = "aac")]
    Aac,

    #[serde(rename = "flac")]
    Flac,

    #[serde(rename = "unknown")]
    Unknown,
}

/// The identity of one media file and everything the analysis layer has learned about
/// it. One record per physical file, always -- a GoPro chapter set is N member records
/// plus one assembly record, never a record that pretends four files are one.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MediaRecord {
    pub schema_version: SchemaVersion,

    /// Primary key. For a physical_file this is the BLAKE3 of the file's bytes; for a
    /// virtual_assembly it is the span_id, a BLAKE3 over the ordered member media_ids.
    /// Content-addressed either way, so re-importing is a no-op and every downstream job
    /// keyed on it is idempotent.
    pub media_id: Blake3Hash,

    /// Whether this record describes bytes on disk or a virtual assembly of other records.
    /// Required and explicit: the identity, size and source rules differ between the two,
    /// and a reader must never have to infer which set applies.
    pub asset_kind: MediaRecordAssetKind,

    /// Top-level media class. `live_photo` and `motion_photo` are their own kind rather
    /// than image-with-a-video because the still and the motion track are separately
    /// renderable and separately rankable.
    pub kind: MediaRecordKind,

    pub byte_size: i64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mime_type: Option<String>,

    /// Container as detected from content, not from the extension. A .jpg that is actually
    /// HEIC is common in exports and must not be trusted by extension.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub file_format: Option<MediaRecordFileFormat>,

    /// Every place on disk these exact bytes have been seen. Plural because deduplication
    /// by content is the whole point: one record, many paths. A physical_file always has at
    /// least one; a virtual_assembly always has none, because its members own the paths and
    /// duplicating one of them here would make the assembly look like a file that can be
    /// opened.
    pub sources: Vec<SourceLocation>,

    /// Set membership for footage split across multiple files. Present on GoPro chaptered
    /// MP4s (GH011234.MP4, GH021234.MP4, ...), DSLR 4GB-limit splits, and Insta360 .insv
    /// sets. Null for the overwhelming majority of files.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub span: Option<Span>,

    pub capture: Capture,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub image: Option<ImageProperties>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub video: Option<VideoProperties>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub perceptual: Option<PerceptualFingerprint>,

    /// Derived renditions. Analysis reads only these; the original is opened exactly twice
    /// in the file's life, once to make these and once at final render.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proxies: Option<Vec<ProxyRef>>,

    pub processing: ProcessingState,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quality: Option<QualityScores>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content: Option<ContentAnalysis>,

    /// Denormalised face summary. The authoritative per-face data lives in FaceRecord; this
    /// block exists so the library grid can filter 'photos with 3+ people' without joining.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub faces: Option<FaceSummary>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dedupe: Option<DedupeMembership>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exclusion: Option<ExclusionState>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub user: Option<UserAnnotations>,

    /// Provenance for every score on this record. Scores reference these by run_id.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_runs: Option<Vec<ModelRun>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub first_seen_at: Option<Timestamp>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<Timestamp>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AudioFeaturesEventsItemLabel {
    #[serde(rename = "laughter")]
    Laughter,

    #[serde(rename = "cheering")]
    Cheering,

    #[serde(rename = "applause")]
    Applause,

    #[serde(rename = "crying")]
    Crying,

    #[serde(rename = "singing")]
    Singing,

    #[serde(rename = "shouting")]
    Shouting,

    #[serde(rename = "splash")]
    Splash,

    #[serde(rename = "music")]
    Music,

    #[serde(rename = "speech")]
    Speech,

    #[serde(rename = "wind")]
    Wind,

    #[serde(rename = "silence")]
    Silence,

    #[serde(rename = "engine")]
    Engine,

    #[serde(rename = "animal")]
    Animal,

    #[serde(rename = "fireworks")]
    Fireworks,

    #[serde(rename = "other")]
    Other,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AudioFeaturesEventsItem {
    pub label: AudioFeaturesEventsItemLabel,

    pub confidence: Confidence,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub time: Option<RationalTime>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AudioFeatures {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub loudness_lufs: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub speech_ratio: Option<Unit>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub music_ratio: Option<Unit>,

    /// Wind and handling noise. High values are why a visually perfect action shot may
    /// still need its ambient ducked to nothing.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub noise_ratio: Option<Unit>,

    /// Detected audio events with their own confidences. Laughter and cheering are among
    /// the strongest emotional-peak signals available to a local model.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub events: Option<Vec<AudioFeaturesEventsItem>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub embedding: Option<VectorRef>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EliminationReasonsItem {
    #[serde(rename = "shake")]
    Shake,

    #[serde(rename = "blown_exposure")]
    BlownExposure,

    #[serde(rename = "crushed_exposure")]
    CrushedExposure,

    #[serde(rename = "black_frame")]
    BlackFrame,

    #[serde(rename = "lens_obstructed")]
    LensObstructed,

    #[serde(rename = "no_motion")]
    NoMotion,

    #[serde(rename = "too_short")]
    TooShort,

    #[serde(rename = "no_subject")]
    NoSubject,

    #[serde(rename = "duplicate_footage")]
    DuplicateFootage,

    #[serde(rename = "wind_noise_dominant")]
    WindNoiseDominant,

    #[serde(rename = "below_score_floor")]
    BelowScoreFloor,

    #[serde(rename = "user_rejected")]
    UserRejected,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EliminationStage {
    #[serde(rename = "classical")]
    Classical,

    #[serde(rename = "local_model")]
    LocalModel,

    #[serde(rename = "fusion")]
    Fusion,

    #[serde(rename = "planner")]
    Planner,

    #[serde(rename = "user")]
    User,
}

/// Elimination-first is the biggest cost and quality lever in the system, so its result
/// is a required, structured field rather than the absence of a record.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Elimination {
    pub eliminated: bool,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasons: Option<Vec<EliminationReasonsItem>>,

    /// How far the moment got before being dropped. `classical` eliminations are the free
    /// ones and should account for the overwhelming majority.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stage: Option<EliminationStage>,
}

/// The fused feature stream over the moment's window. These are the inputs to score
/// fusion, and they are exactly what a PrefEvent captures as decision context -- which
/// is why they are named, bounded and stable rather than an opaque vector.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MomentFeatures {
    /// Mean optical-flow magnitude, normalised. High is action; near-zero over a long
    /// window is tripod dead time and gets eliminated.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub motion_energy: Option<Unit>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub motion_peak: Option<Unit>,

    /// Camera instability distinct from subject motion. The single most common reason
    /// handheld footage is unusable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub shake: Option<Unit>,

    /// Low when the camera is hunting exposure, e.g. walking from indoors into sun. Such a
    /// window looks bad no matter how good the content is.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exposure_stability: Option<Unit>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sharpness: Option<Unit>,

    /// Fraction of frames in the window containing at least one face.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_presence: Option<Unit>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_face_area_ratio: Option<Unit>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub smile_intensity: Option<Unit>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub audio: Option<AudioFeatures>,

    /// SigLIP embedding of the moment's representative keyframe. Drives diversity
    /// constraints, so two moments that look alike cannot both make the cut.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub visual_embedding: Option<VectorRef>,

    /// Distance from everything already selected. This is what stops a reel being six near-
    /// identical drone shots.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub novelty: Option<Unit>,

    /// Best single frame in the window, used as the contact-sheet tile shown to the
    /// frontier model.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub representative_frame_time: Option<RationalTime>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum MomentScoresSource {
    #[serde(rename = "local_fusion")]
    LocalFusion,

    #[serde(rename = "local_learned")]
    LocalLearned,

    #[serde(rename = "tier2_vlm")]
    Tier2Vlm,

    #[serde(rename = "tier3_model")]
    Tier3Model,

    #[serde(rename = "user_override")]
    UserOverride,
}

/// Fused judgements. `moment_score` is the only required one; the rest are the
/// decomposition that makes it explainable and per-user reweightable.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MomentScores {
    /// Overall keepworthiness. v1 is a hand-weighted linear fusion because a transparent,
    /// tunable model beats an opaque one until PrefEvents exist to train on.
    pub moment_score: Score,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub technical: Option<Score>,

    /// Fitness for the first second of a reel, where retention is won or lost. Rewards
    /// immediate motion or an immediate face, and punishes slow builds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hook_potential: Option<Score>,

    /// How much this feels like a moment rather than merely looking like one. Local
    /// features approximate it via laughter, smiles and motion onsets; the frontier model
    /// is the one that can actually tell 'child sees the ocean' from 'child near ocean',
    /// and when it has ruled, `source` says so.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub emotional_peak: Option<Score>,

    /// Contribution to a story beat, only ever populated by a Tier 2/3 reasoning pass.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub narrative_value: Option<Score>,

    /// Which tier produced the judgement scores. Never let a frontier-model opinion be
    /// mistaken for a local measurement.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<MomentScoresSource>,

    /// Which weight set produced moment_score. Per-user reweighting means the same features
    /// legitimately yield different scores for different people, and the score is
    /// meaningless without knowing which weights applied.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fusion_weights_version: Option<String>,
}

/// Hard bounds on trimming. `speech_safe_*` are the ones that make the no-mid-word
/// guarantee exact: they are derived from word-level timestamps, not from voice-
/// activity guesses.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SafeTrim {
    pub earliest_in: RationalTime,

    pub latest_out: RationalTime,

    /// Earliest in-point that does not land inside a spoken word. Null when the moment
    /// contains no speech, in which case earliest_in applies.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub speech_safe_in: Option<RationalTime>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub speech_safe_out: Option<RationalTime>,

    /// Below this the moment reads as a flash frame rather than a shot.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_duration: Option<RationalTime>,

    /// True when the audio meaningfully continues past the visual out-point -- a laugh that
    /// lands after the cut. The renderer honours this with an audio-only extension (an
    /// L-cut), which is a decision that must live in the plan.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub preserve_audio_tail: Option<bool>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SnapPointKind {
    #[serde(rename = "shot_boundary")]
    ShotBoundary,

    #[serde(rename = "motion_onset")]
    MotionOnset,

    #[serde(rename = "motion_offset")]
    MotionOffset,

    #[serde(rename = "audio_onset")]
    AudioOnset,

    #[serde(rename = "speech_gap")]
    SpeechGap,

    #[serde(rename = "speech_start")]
    SpeechStart,

    #[serde(rename = "speech_end")]
    SpeechEnd,

    #[serde(rename = "subject_entry")]
    SubjectEntry,

    #[serde(rename = "subject_exit")]
    SubjectExit,

    #[serde(rename = "impact")]
    Impact,

    #[serde(rename = "scene_brightness_change")]
    SceneBrightnessChange,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SnapPointCutDirection {
    #[serde(rename = "in")]
    In,

    #[serde(rename = "out")]
    Out,

    #[serde(rename = "both")]
    Both,
}

/// A time at which cutting is defensible, with a reason. The planner may only place
/// cuts on snap points; that constraint is what makes 'beat-alignment error < 50ms' and
/// 'no mid-word cuts' testable properties of a plan rather than emergent behaviour.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SnapPoint {
    /// Source timecode. Exact rational, never rounded to milliseconds -- the 50ms beat-
    /// alignment gate has no headroom to spare on rounding.
    pub time: RationalTime,

    pub kind: SnapPointKind,

    /// How pronounced the boundary is. Cutting on a weak onset is worse than cutting 40ms
    /// later on a strong one.
    pub strength: Unit,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<Confidence>,

    /// Whether this point is usable as an in-point, an out-point, or both. A motion onset
    /// is a great in-point and a poor out-point; encoding that asymmetry stops the planner
    /// making technically-legal, visually-wrong cuts.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cut_direction: Option<SnapPointCutDirection>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TranscriptSegmentWordsItem {
    pub word: String,

    pub start: RationalTime,

    pub end: RationalTime,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<Confidence>,
}

/// Speech inside the moment, with word timing. Word timestamps are not a nicety: they
/// are the mechanism behind speech-aware trimming and the mid-word-cut quality gate.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TranscriptSegment {
    pub text: String,

    /// BCP-47. Indian-language libraries are a first-class target, so this is required
    /// rather than assumed English.
    pub language: String,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub words: Option<Vec<TranscriptSegmentWordsItem>>,

    /// Diarisation labels, mapped to person ids where a confident face-voice association
    /// exists.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub speaker_ids: Option<Vec<String>>,

    /// Someone says a name. A strong and cheap signal that a window matters to the family
    /// it belongs to.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub contains_name_mention: Option<bool>,
}

/// Who is present, resolved through the automated-output face gate. A person named here
/// has passed the precision bar; uncertain faces contribute to face_presence but not to
/// this list.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MomentRecordPeople {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub person_ids: Option<Vec<Uuid>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_track_ids: Option<Vec<Uuid>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unidentified_face_count: Option<i64>,
}

/// A scored time interval in a video: what happens in it, how good it is, and where
/// inside it a cut is allowed to land.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MomentRecord {
    pub schema_version: SchemaVersion,

    /// BLAKE3 over (media_id, source_range, scorer model_id+version). Rescoring with a new
    /// model yields new ids, so an EDL always points at the exact moment definition it was
    /// planned against.
    pub moment_id: Blake3Hash,

    /// MediaRecord this moment lives in. For chaptered footage this is the span ASSEMBLY
    /// id, so a moment may legally straddle a chapter boundary that the planner never has
    /// to know about.
    pub media_id: Blake3Hash,

    /// The moment's extent in SOURCE timecode -- already mapped back through the proxy
    /// frame index. Everything downstream, including the EDL, addresses source time; proxy
    /// time never escapes the analysis layer.
    pub source_range: TimeRange,

    /// The same interval in proxy time, retained so a re-score can be run against the
    /// cached proxy without redoing the mapping.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proxy_range: Option<TimeRange>,

    /// Shot this moment sits inside, from TransNetV2 boundary detection. A moment never
    /// crosses a shot boundary -- crossing one is a cut, and cuts belong to the planner.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub shot_id: Option<Slug>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub features: Option<MomentFeatures>,

    pub scores: MomentScores,

    /// Certified cut positions inside (and at the edges of) this moment. Ordered by time. A
    /// beat-locked reel cut is the intersection of a beat grid entry and a snap point of
    /// kind motion_onset or audio_onset -- that intersection is what makes a cut feel
    /// deliberate rather than arbitrary.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub snap_points: Option<Vec<SnapPoint>>,

    /// The bounds a planner may trim to without damaging the moment. Absent only when
    /// speech and motion analysis have not run.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub safe_trim: Option<SafeTrim>,

    pub elimination: Elimination,

    /// Who is present, resolved through the automated-output face gate. A person named here
    /// has passed the precision bar; uncertain faces contribute to face_presence but not to
    /// this list.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub people: Option<MomentRecordPeople>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transcript: Option<TranscriptSegment>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_runs: Option<Vec<ModelRun>>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at: Option<Timestamp>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Alternative {
    pub subject_id: String,

    /// True for the option that won. Included in the list rather than only in Subject so a
    /// single array fully describes the comparison.
    pub chosen: bool,

    /// Position as shown, 0-based. Position bias is real and strong; a model trained
    /// without it will learn that the top-left option is beautiful.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub presented_rank: Option<i64>,

    /// The score the system assigned at presentation time. The gap between this and the
    /// human's choice is the error signal.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub presented_score: Option<Unit>,

    /// Features for this alternative, in the same feature_set_id as the subject's. Required
    /// for pairwise training; a comparison where only the winner has features cannot be
    /// learned from.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub feature_vector: Option<DenseFeatures>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum DecisionKind {
    #[serde(rename = "kept")]
    Kept,

    #[serde(rename = "rejected")]
    Rejected,

    #[serde(rename = "reordered")]
    Reordered,

    #[serde(rename = "recropped")]
    Recropped,

    #[serde(rename = "variant_picked")]
    VariantPicked,

    #[serde(rename = "hero_swapped")]
    HeroSwapped,

    #[serde(rename = "replaced")]
    Replaced,

    #[serde(rename = "person_confirmed")]
    PersonConfirmed,

    #[serde(rename = "person_rejected")]
    PersonRejected,

    #[serde(rename = "moment_trimmed")]
    MomentTrimmed,

    #[serde(rename = "music_changed")]
    MusicChanged,

    #[serde(rename = "enhancement_accepted")]
    EnhancementAccepted,

    #[serde(rename = "enhancement_rejected")]
    EnhancementRejected,

    #[serde(rename = "page_reordered")]
    PageReordered,

    #[serde(rename = "exported")]
    Exported,

    #[serde(rename = "printed")]
    Printed,

    #[serde(rename = "shared")]
    Shared,

    #[serde(rename = "deleted")]
    Deleted,

    #[serde(rename = "favorited")]
    Favorited,

    #[serde(rename = "revision_requested")]
    RevisionRequested,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum DecisionSurface {
    #[serde(rename = "culling_ui")]
    CullingUi,

    #[serde(rename = "library_grid")]
    LibraryGrid,

    #[serde(rename = "album_review")]
    AlbumReview,

    #[serde(rename = "spread_editor")]
    SpreadEditor,

    #[serde(rename = "variant_picker")]
    VariantPicker,

    #[serde(rename = "person_labeling")]
    PersonLabeling,

    #[serde(rename = "project_editor")]
    ProjectEditor,

    #[serde(rename = "share_flow")]
    ShareFlow,

    #[serde(rename = "checkout")]
    Checkout,

    #[serde(rename = "concierge_review")]
    ConciergeReview,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Decision {
    /// What the human did. Note that `exported`, `printed` and `shared` are included:
    /// acting on an output is the strongest positive signal available, far stronger than a
    /// thumbs-up, and it costs nothing to capture.
    pub kind: DecisionKind,

    /// Where in the product it happened. The same `kept` means different things in a
    /// culling sweep and in a final album review, and a model that cannot tell them apart
    /// will learn the average of two different tastes.
    pub surface: DecisionSurface,

    /// True when the human deliberately expressed a preference. False for inferred signals
    /// such as dwelling on a frame or scrolling past. Inferred events are far weaker
    /// evidence and must be weighted accordingly rather than mixed in silently.
    pub explicit: bool,

    /// How much to trust an inferred signal. Null for explicit ones, which need no
    /// discount.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<Confidence>,

    /// Set when the human undid this within the session. A reversed decision is training
    /// data about the reversal, not about the original action, and must never be fed in as
    /// a plain positive.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reversed_at: Option<Timestamp>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum DecisionContextTask {
    #[serde(rename = "cull")]
    Cull,

    #[serde(rename = "album")]
    Album,

    #[serde(rename = "reel")]
    Reel,

    #[serde(rename = "film")]
    Film,

    #[serde(rename = "person_labeling")]
    PersonLabeling,

    #[serde(rename = "search")]
    Search,

    #[serde(rename = "share")]
    Share,

    #[serde(rename = "concierge")]
    Concierge,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum DecisionContextDeviceClass {
    #[serde(rename = "desktop")]
    Desktop,

    #[serde(rename = "laptop")]
    Laptop,

    #[serde(rename = "tablet")]
    Tablet,

    #[serde(rename = "phone")]
    Phone,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DecisionContext {
    /// What the human was trying to accomplish. Taste is task-relative: a photo rejected
    /// for a print album may be perfectly good for a reel.
    pub task: DecisionContextTask,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_id: Option<Uuid>,

    /// Groups decisions made in one sitting. Decisions late in a long session are noisier
    /// -- fatigue is measurable and worth modelling rather than ignoring.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_id: Option<Uuid>,

    /// How many options existed in total, which may exceed the number displayed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub candidate_set_size: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub presented_count: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subject_presented_rank: Option<i64>,

    /// Time from presentation to decision. A 400ms rejection and a 30s agonised one are
    /// different strengths of evidence.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deliberation_ms: Option<f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub position_in_session: Option<i64>,

    /// Which ranking model produced the scores the human was reacting to. Without it, an
    /// event cannot be attributed to the model that generated the ordering, and offline
    /// evaluation becomes guesswork.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ranker_version: Option<String>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fusion_weights_version: Option<String>,

    /// Screen size changes what a person can even perceive; a crop judged on a phone is not
    /// a crop judged on a 27-inch display.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub device_class: Option<DecisionContextDeviceClass>,
}

/// The before and after of an edit. A re-crop is the richest signal the system ever
/// receives: the human has not merely judged, they have demonstrated the correct
/// answer.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DecisionDelta {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub crop_before: Option<NormalizedBox>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub crop_after: Option<NormalizedBox>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub position_before: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub position_after: Option<i64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trim_before: Option<TimeRange>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trim_after: Option<TimeRange>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub replaced_with_subject_id: Option<String>,

    /// For revision_requested: the human's own words, such as 'more of her' or 'less
    /// drone'. Free text, local-only, and stripped before any export -- it can contain
    /// names.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instruction_text: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DenseFeatures {
    pub feature_set_id: Slug,

    pub values: Vec<f64>,
}

/// Whether confirmed people were present, and how many. Who is in a photo is often the
/// entire reason it was kept, and this captures that without naming anybody in an
/// exportable record -- ids stay local, counts travel.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FeatureContextPersonContext {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confirmed_person_count: Option<i64>,

    /// True when someone the user has marked as important is present. The single strongest
    /// predictor of a keep decision in family libraries.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub includes_priority_person: Option<bool>,

    /// Local-only. Stripped by the anonymisation pass before any event leaves the device.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub person_ids: Option<Vec<Uuid>>,
}

/// The subject's features as they stood at decision time. Both a named map and an
/// optional dense vector: the named map keeps the data interpretable and debuggable for
/// years, the dense vector keeps training cheap. The named map is the source of truth.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FeatureContext {
    /// Names the exact ordered feature list in use. Bumped whenever a feature is added,
    /// removed or redefined. A dense vector is meaningless without it.
    pub feature_set_id: Slug,

    /// Feature name to value. Values are numeric and normalised. Deliberately an open map
    /// rather than a fixed property list, because the feature set is expected to grow --
    /// but every key must be declared in the named feature_set_id, and the eval harness
    /// checks that.
    pub named: BTreeMap<String, f64>,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dense: Option<DenseFeatures>,

    /// Embedding references, not the floats themselves for local storage, and resolved to
    /// values only when a training export is built under an explicit consent scope. An
    /// embedding is derived from pixels but is not pixels; treating it as sensitive anyway
    /// is the conservative reading of the privacy promise.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub embeddings: Option<Vec<VectorRef>>,

    /// The system's own scores as presented. Frozen: never recomputed against a later
    /// model.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scores_at_decision: Option<BTreeMap<String, f64>>,

    /// Whether confirmed people were present, and how many. Who is in a photo is often the
    /// entire reason it was kept, and this captures that without naming anybody in an
    /// exportable record -- ids stay local, counts travel.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub person_context: Option<FeatureContextPersonContext>,
}

/// Who this event belongs to and how far it is allowed to travel. Present on every
/// event because the decision about sharing is made once, at write time, and never re-
/// litigated by whatever code later reads the record.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PrivacyEnvelope {
    /// A per-install salted hash, not a user id and not an email. Sufficient to group one
    /// person's events for a per-user model, insufficient to identify them.
    pub user_pseudonym: Blake3Hash,

    /// Whether this event may join the anonymised global training pool. Defaults to false
    /// in practice: the user opts in, and the absence of a decision is not consent.
    pub shareable_for_global_model: bool,

    /// Required when shareable_for_global_model is true. Same rule as everywhere else --
    /// nothing leaves without a ledger entry.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub consent: Option<ConsentRef>,

    /// True when the record still holds person ids or free text. Such an event must pass
    /// the anonymisation step before export; this flag is what makes that check cheap and
    /// unambiguous.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub contains_local_identifiers: Option<bool>,

    /// Which redaction pass was applied. Set once the event has been stripped for export.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub anonymization_version: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SubjectSubjectType {
    #[serde(rename = "media")]
    Media,

    #[serde(rename = "moment")]
    Moment,

    #[serde(rename = "face")]
    Face,

    #[serde(rename = "person")]
    Person,

    #[serde(rename = "placement")]
    Placement,

    #[serde(rename = "spread")]
    Spread,

    #[serde(rename = "page")]
    Page,

    #[serde(rename = "edl_variant")]
    EdlVariant,

    #[serde(rename = "album")]
    Album,

    #[serde(rename = "enhancement_op")]
    EnhancementOp,

    #[serde(rename = "music_cue")]
    MusicCue,
}

/// What was decided about, and crucially what it was decided AGAINST.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Subject {
    pub subject_type: SubjectSubjectType,

    /// Content hash for content-addressed subjects, uuid or slug otherwise. Kept as a
    /// string so one field serves every subject type.
    pub subject_id: String,

    /// The other options on screen when the decision was made, each with the score it was
    /// presented with. This is what converts an outcome into a pairwise preference, and it
    /// is the single most valuable field in the record. Empty only for decisions with
    /// genuinely no alternatives, such as favouriting one photo in a grid.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub alternatives: Option<Vec<Alternative>>,

    /// Containing entity: the album a placement is on, the reel a variant belongs to.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent_id: Option<String>,
}

/// One human reaction, captured with the feature context that existed at the moment of
/// the decision.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PrefEvent {
    pub schema_version: SchemaVersion,

    pub event_id: Uuid,

    pub occurred_at: Timestamp,

    pub decision: Decision,

    pub subject: Subject,

    pub context: DecisionContext,

    pub features: FeatureContext,

    /// What actually changed, for decisions that are an edit rather than a choice: the crop
    /// before and after, the position before and after. The edit itself is the label.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delta: Option<DecisionDelta>,

    pub privacy: PrivacyEnvelope,

    /// Structurally false, always. Present as a field rather than as an unwritten rule so
    /// that any pipeline stage, any reviewer, and any test can assert the privacy property
    /// directly on the record.
    pub pixel_data_present: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ClassScores {
    pub explicit: Unit,

    pub suggestive: Unit,

    pub medical_or_artistic: Unit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ClassifierPinLoadMode {
    #[serde(rename = "release")]
    Release,

    #[serde(rename = "development")]
    Development,
}

/// Which model produced these verdicts. A verdict from a model you cannot identify is
/// not evidence, and a verdict produced under a different config is a verdict about a
/// different decision boundary -- score_threshold 0.3 and 0.5 are different classifiers
/// to every consumer.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ClassifierPin {
    pub model: ModelRef,

    pub ran_at: Timestamp,

    /// Which gate the host was running under. A verdict produced by a DEVELOPMENT-mode host
    /// -- unpinned weights, unverified licence -- must never clear a real publication, and
    /// a verifier serving a release sink must refuse it. Recorded rather than assumed,
    /// because 'we were only testing' is how unverified weights reach production.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub load_mode: Option<ClassifierPinLoadMode>,
}

/// The aggregate, derived from `items` and recomputed by every verifier rather than
/// trusted. It is stored so a rejected publication can be explained without re-running
/// anything -- not so a reader can skip checking the items.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ClearanceDecision {
    /// True only when EVERY item is `cleared`, or is `blocked` with a valid override for
    /// this sink. One indeterminate item denies the whole publication -- a book is printed
    /// as a unit and a share is published as a unit, so partial clearance is not a state
    /// either can be in.
    pub cleared_for_publication: bool,

    pub item_count: i64,

    pub cleared_count: i64,

    pub blocked_count: i64,

    pub indeterminate_count: i64,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub denied_reason: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ItemVerdictVerdict {
    #[serde(rename = "cleared")]
    Cleared,

    #[serde(rename = "blocked")]
    Blocked,

    #[serde(rename = "indeterminate")]
    Indeterminate,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ItemVerdictIndeterminateReason {
    #[serde(rename = "no_result")]
    NoResult,

    #[serde(rename = "model_unavailable")]
    ModelUnavailable,

    #[serde(rename = "model_unloadable")]
    ModelUnloadable,

    #[serde(rename = "load_gate_denied")]
    LoadGateDenied,

    #[serde(rename = "config_digest_mismatch")]
    ConfigDigestMismatch,

    #[serde(rename = "inference_error")]
    InferenceError,

    #[serde(rename = "inference_timeout")]
    InferenceTimeout,

    #[serde(rename = "evidence_stale")]
    EvidenceStale,

    #[serde(rename = "verifier_exception")]
    VerifierException,
}

/// One media id's clearance, bound to the exact bytes that were classified.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ItemVerdict {
    pub media_id: Blake3Hash,

    /// The PROXY the classifier actually saw. Not the media id: a proxy can be regenerated
    /// -- a better decoder, a corrected orientation, a different size -- and a verdict
    /// about the old proxy is not evidence about the new one. A verifier must confirm this
    /// matches the proxy the publication is built from, or treat the verdict as stale,
    /// which is indeterminate, which blocks.
    pub evidence_id: Blake3Hash,

    /// `cleared` is the ONLY value that permits automatic publication.
    ///
    /// `blocked` means the classifier scored above a threshold. It may be overridden per
    /// item by a human, because the classifier does not get a veto over a parent's
    /// judgement about their own family.
    ///
    /// `indeterminate` means nobody knows: no result row, model unavailable or unloadable,
    /// load-gate denial, config digest mismatch, inference error or timeout, or evidence
    /// that no longer matches. It may NOT be overridden by anything, because 'nobody
    /// checked' is not a decision somebody made.
    pub verdict: ItemVerdictVerdict,

    /// Per-class probabilities, when the classifier ran. Null on `indeterminate` -- an
    /// indeterminate verdict with scores attached is a contradiction, and the conditional
    /// below rejects it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scores: Option<ClassScores>,

    /// Why nobody knows. Required on `indeterminate`, because 'blocked for an unknown
    /// reason' is unactionable and the remedies differ completely: a missing model needs
    /// installing, a stale evidence id needs re-running, a digest mismatch needs
    /// investigating.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub indeterminate_reason: Option<ItemVerdictIndeterminateReason>,

    /// A human decision to publish despite a `blocked` verdict. Permitted ONLY on
    /// `blocked`; the conditional below refuses it on `indeterminate`, which is the single
    /// most important rule in this file.
    #[serde(default, skip_serializing_if = "Option::is_none", rename = "override")]
    pub r#override: Option<Override>,
}

/// A recorded human decision. Attributable on purpose: an override that nobody owns is
/// a bypass.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Override {
    pub decided_at: Timestamp,

    /// Local user identifier. Never a service account, never a config value -- a machine
    /// cannot consent on a person's behalf about their own photographs.
    pub decided_by: String,

    /// `item_and_sink` is the only value, deliberately. There is no 'always allow this
    /// photo' and no 'always allow this class': a decision to print a photo in a private
    /// family book is not a decision to publish it, and the whole design fails if an
    /// override can outlive the publication it was made for.
    pub scope: String,

    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

/// The decision boundaries actually applied, per class. Recorded rather than referenced
/// because the config can change underneath a stored verdict, and a verdict whose
/// threshold you cannot reconstruct cannot be re-audited.
///
/// The classes are separate on purpose. Collapsing them into one 'nsfw' bit produces
/// the two classic failures: a breastfeeding photo or a post-surgery record treated as
/// pornography, and a bikini holiday photo treated as safe for a public share. A family
/// library contains all three, and the right handling differs for each.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Thresholds {
    pub explicit: Unit,

    pub suggestive: Unit,

    pub medical_or_artistic: Unit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SafetyClearanceSink {
    #[serde(rename = "print")]
    Print,

    #[serde(rename = "share")]
    Share,

    #[serde(rename = "frontier_egress")]
    FrontierEgress,

    #[serde(rename = "local_export")]
    LocalExport,
}

/// The manifest that must exist, verify, and be COMPLETE before anything leaves the
/// device or reaches a printer. Designed by Codex on issue #21; this is the contract
/// form of it.
///
/// WHY A MANIFEST AND NOT A FIELD ON EACH RECORD
///
/// A per-record `is_safe` flag is checked at some point and acted on at another, and
/// the gap between them is where the failure lives: the selection changes, a photo is
/// swapped in, and the check that passed was about a different set. So clearance is
/// bound to an EXACT publication -- this sink, these media ids, in this order, under
/// this classifier and this config digest -- and hashed. The renderer or service
/// verifies the hash against the inputs it is ACTUALLY about to publish, inside the
/// same operation that creates the export. There is no window in which the checked set
/// and the published set can differ.
///
/// THE RULE THAT MATTERS MOST
///
/// Absence is `indeterminate`, and indeterminate BLOCKS. A missing verdict, an
/// unloadable classifier, a config digest mismatch, an inference timeout, a stale
/// verdict for a proxy that has since changed, a row that simply is not there -- all of
/// them are indeterminate. Only `cleared` proceeds.
///
/// This is the opposite of how safety checks usually fail. The common shape is a check
/// that silently no-ops when its model is missing, so everything downstream reads the
/// absence as a pass. This project has already shipped one gate with exactly that
/// defect (a model load gate that permitted weights whose hash had never been
/// computed), and the fix cost more than building it correctly would have.
///
/// WHAT MAY AND MAY NOT BE OVERRIDDEN
///
/// A POSITIVE classifier result may be overridden per item by a human: a parent may
/// decide a breastfeeding photo belongs in the family album, and the classifier does
/// not get a veto over that. The override is recorded in the manifest with who and
/// when, so the decision is attributable.
///
/// A MISSING result may NOT be overridden -- not by a flag, not by a default, not by a
/// global bypass, not by an empty override list. 'Nobody checked' and 'somebody checked
/// and disagreed' are different states, and only the second is a decision.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SafetyClearance {
    pub schema_version: SchemaVersion,

    /// BLAKE3 over the canonical manifest body, computed exactly as models/policy/digest.py
    /// computes a config digest: over the SERIALISED BYTES of this document with
    /// `manifest_id` and `decision` removed. Bytes rather than a re-serialisation, because
    /// Python writes the float 1.0 as `1.0` and JavaScript writes `1`, and a manifest that
    /// verifies in the pipeline and fails in the Rust renderer is a gate that blocks
    /// correct output -- which is how gates get disabled.
    pub manifest_id: Blake3Hash,

    /// Format version of the manifest itself. A verifier that does not recognise this value
    /// MUST DENY rather than attempt a best-effort parse. Deny-by-default on an unknown
    /// version is what stops an old renderer from ignoring a field a newer planner added --
    /// and the field it ignores will be the one that was added because something went
    /// wrong.
    pub manifest_version: i64,

    pub created_at: Timestamp,

    /// Where this publication is going. Clearance is NOT transferable between sinks: a
    /// photo cleared for a private printed book has not thereby been cleared for a public
    /// share link, and the thresholds differ. A verifier must check that the sink it is
    /// serving matches this value exactly.
    pub sink: SafetyClearanceSink,

    /// Free text naming the specific destination (vendor, recipient scope, model provider)
    /// for the audit trail. NEVER parsed, never used to make a decision -- a decision that
    /// depends on a free-text field is a decision an attacker can influence.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sink_detail: Option<String>,

    pub classifier: ClassifierPin,

    pub thresholds: Thresholds,

    /// One entry per media id in the publication, in PUBLICATION ORDER. Order is part of
    /// the identity: a manifest whose items match by set but not by order describes a
    /// different publication, and a verifier comparing sets rather than sequences would
    /// accept a reordered book.
    pub items: Vec<ItemVerdict>,

    pub decision: ClearanceDecision,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "item_type")]
pub enum TrackItemsItem {
    #[serde(rename = "clip")]
    Clip(Clip),

    #[serde(rename = "gap")]
    Gap(Gap),

    #[serde(rename = "transition")]
    Transition(Transition),
}

/// Contract version every generated record declares.
pub const CONTRACT_VERSION: &str = "v0";
