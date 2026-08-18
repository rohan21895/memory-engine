use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

use chrono::Utc;
use memory_engine_contracts::{
    ExclusionState, FrameIndexSidecarMapping, MediaRecord, MediaRecordAssetKind,
    MediaRecordFileFormat, MediaRecordKind, ProcessingState, ProcessingStateStages,
    ProcessingStateState, ProxyRefKind, RationalTime, Span, SpanContinuity, SpanRole, SpanSpanKind,
    StageState, StageStateStatus, VideoProperties,
};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct GroupKey {
    directory: String,
    family: &'static str,
    recording: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct GoproChapter {
    family: &'static str,
    recording: String,
    pub index: i64,
}

#[derive(Debug, Default)]
pub(crate) struct SpanBuild {
    pub members: Vec<MediaRecord>,
    pub assemblies: Vec<MediaRecord>,
    pub issues: Vec<String>,
}

#[derive(Clone)]
struct Sequence {
    span_id: String,
    closed: bool,
    members: Vec<(i64, MediaRecord)>,
}

pub(crate) fn parse_filename(filename: &str) -> Option<GoproChapter> {
    let path = Path::new(filename);
    if !path.extension().is_some_and(|extension| {
        extension.eq_ignore_ascii_case("mp4") || extension.eq_ignore_ascii_case("lrv")
    }) {
        return None;
    }
    let stem = path.file_stem()?.to_str()?.to_ascii_uppercase();
    let bytes = stem.as_bytes();
    if bytes.len() != 8 || !bytes.is_ascii() {
        return None;
    }

    if &bytes[..4] == b"GOPR" && bytes[4..].iter().all(u8::is_ascii_digit) {
        return Some(GoproChapter {
            family: "legacy",
            recording: stem[4..].to_owned(),
            index: 0,
        });
    }
    if matches!(&bytes[..2], b"GH" | b"GX") && bytes[2..].iter().all(u8::is_ascii_digit) {
        let chapter = stem[2..4].parse::<i64>().ok()?;
        if chapter == 0 {
            return None;
        }
        return Some(GoproChapter {
            family: if &bytes[..2] == b"GH" { "gh" } else { "gx" },
            recording: stem[4..].to_owned(),
            index: chapter - 1,
        });
    }
    if &bytes[..2] == b"GP" && bytes[2..].iter().all(u8::is_ascii_digit) {
        let chapter = stem[2..4].parse::<i64>().ok()?;
        if chapter == 0 {
            return None;
        }
        return Some(GoproChapter {
            family: "legacy",
            recording: stem[4..].to_owned(),
            index: chapter,
        });
    }
    None
}

pub(crate) fn build(records: &[MediaRecord]) -> SpanBuild {
    let physical = records
        .iter()
        .filter(|record| {
            record.asset_kind == MediaRecordAssetKind::PhysicalFile
                && record.kind == MediaRecordKind::Video
                && record.file_format == Some(MediaRecordFileFormat::Mp4)
        })
        .map(|record| (record.media_id.clone(), record.clone()))
        .collect::<BTreeMap<_, _>>();
    let existing_assemblies = records
        .iter()
        .filter(|record| record.asset_kind == MediaRecordAssetKind::VirtualAssembly)
        .map(|record| (record.media_id.clone(), record.clone()))
        .collect::<BTreeMap<_, _>>();
    let mut groups: BTreeMap<GroupKey, BTreeMap<i64, BTreeMap<String, MediaRecord>>> =
        BTreeMap::new();

    for record in physical.values() {
        for source in record.sources.iter().filter(|source| source.present) {
            let filename = source
                .original_filename
                .as_deref()
                .or_else(|| Path::new(&source.path).file_name()?.to_str());
            let Some(chapter) = filename.and_then(parse_filename) else {
                continue;
            };
            let directory = Path::new(&source.path)
                .parent()
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or_default();
            groups
                .entry(GroupKey {
                    directory,
                    family: chapter.family,
                    recording: chapter.recording,
                })
                .or_default()
                .entry(chapter.index)
                .or_default()
                .insert(record.media_id.clone(), record.clone());
        }
    }

    let mut result = SpanBuild::default();
    let mut sequences = BTreeMap::<String, Sequence>::new();
    let mut conflicting_sequences = BTreeSet::new();
    for (group, chapters) in groups {
        if chapters.len() < 2 {
            continue;
        }
        if chapters.values().any(|at_index| at_index.len() != 1) {
            result.issues.push(
                "GoPro chapter group has multiple files at one index; assembly skipped".to_owned(),
            );
            continue;
        }
        let members = chapters
            .into_iter()
            .map(|(index, records)| (index, records.into_values().next().expect("one member")))
            .collect::<Vec<_>>();
        let unique_members = members
            .iter()
            .map(|(_, member)| member.media_id.as_str())
            .collect::<BTreeSet<_>>();
        if unique_members.len() != members.len() {
            result.issues.push(
                "GoPro chapter group repeats the same content at multiple indexes; assembly skipped"
                    .to_owned(),
            );
            continue;
        }
        let final_span_id = span_id(members.iter().map(|(_, record)| record.media_id.as_str()));
        let closed = certified_closed_span(&members, &final_span_id);
        let span_id = if closed {
            final_span_id
        } else {
            carried_provisional_span(&members, &final_span_id)
                .unwrap_or_else(|| provisional_span_id(&group, &members))
        };
        if let Some(existing) = sequences.get(&span_id) {
            let existing_identity = existing
                .members
                .iter()
                .map(|(index, member)| (*index, member.media_id.as_str()))
                .collect::<Vec<_>>();
            let candidate_identity = members
                .iter()
                .map(|(index, member)| (*index, member.media_id.as_str()))
                .collect::<Vec<_>>();
            if existing_identity == candidate_identity {
                continue;
            }
            if identity_is_prefix(&existing_identity, &candidate_identity) {
                sequences.insert(
                    span_id.clone(),
                    Sequence {
                        span_id,
                        closed,
                        members,
                    },
                );
            } else if !identity_is_prefix(&candidate_identity, &existing_identity) {
                conflicting_sequences.insert(span_id);
            }
        } else {
            sequences.insert(
                span_id.clone(),
                Sequence {
                    span_id,
                    closed,
                    members,
                },
            );
        }
    }
    if !conflicting_sequences.is_empty() {
        result
            .issues
            .push("GoPro copies disagree about chapter indexes; assemblies skipped".to_owned());
        sequences.retain(|span_id, _| !conflicting_sequences.contains(span_id));
    }

    let mut assignments = BTreeMap::<String, BTreeSet<String>>::new();
    for sequence in sequences.values() {
        for (_, member) in &sequence.members {
            assignments
                .entry(member.media_id.clone())
                .or_default()
                .insert(sequence.span_id.clone());
        }
    }
    let ambiguous = assignments
        .into_iter()
        .filter_map(|(media_id, spans)| (spans.len() > 1).then_some(media_id))
        .collect::<BTreeSet<_>>();
    if !ambiguous.is_empty() {
        result.issues.push(
            "GoPro file belongs to conflicting chapter groups; assemblies skipped".to_owned(),
        );
    }

    for sequence in sequences.into_values().filter(|sequence| {
        !sequence
            .members
            .iter()
            .any(|(_, member)| ambiguous.contains(&member.media_id))
    }) {
        let now = Utc::now().to_rfc3339();
        let missing_index = sequence
            .members
            .iter()
            .enumerate()
            .any(|(expected, (actual, _))| *actual != expected as i64);
        let incomplete = !sequence.closed || missing_index;
        let (assembly_video, offsets, continuity) = timeline(&sequence.members, incomplete);
        let assembly_offset = offsets.first().cloned().flatten();
        let member_ids = sequence
            .members
            .iter()
            .map(|(_, member)| member.media_id.clone())
            .collect::<Vec<_>>();
        let member_count = sequence.closed.then_some(member_ids.len() as i64);

        for (((index, member), offset), media_id) in
            sequence.members.iter().zip(offsets).zip(member_ids.iter())
        {
            let previous_span = member
                .span
                .as_ref()
                .filter(|span| span.role == SpanRole::Member && span.span_id == sequence.span_id);
            let desired_span = Span {
                span_id: sequence.span_id.clone(),
                role: SpanRole::Member,
                span_kind: SpanSpanKind::GoproChapter,
                index: Some(*index),
                member_count,
                member_media_ids: Some(Vec::new()),
                offset_in_span: if continuity == SpanContinuity::IncompleteSet {
                    None
                } else {
                    offset.or_else(|| previous_span.and_then(|span| span.offset_in_span.clone()))
                },
                continuity: Some(prefer_known_continuity(
                    continuity,
                    previous_span.and_then(|span| span.continuity),
                )),
            };
            if member.span.as_ref() != Some(&desired_span) {
                let mut updated = member.clone();
                updated.span = Some(desired_span);
                updated.updated_at = Some(now.clone());
                result.members.push(updated);
            }
            debug_assert_eq!(media_id, &member.media_id);
        }

        let desired = new_assembly(
            &sequence,
            member_ids,
            assembly_video,
            assembly_offset,
            member_count,
            continuity,
            &now,
        );
        let assembly = existing_assemblies
            .get(&sequence.span_id)
            .map_or(desired.clone(), |existing| {
                merge_existing_assembly(existing, &desired, &now)
            });
        result.assemblies.push(assembly);
    }

    result
}

fn span_id<'a>(media_ids: impl Iterator<Item = &'a str>) -> String {
    let mut hasher = blake3::Hasher::new();
    // Blake3Hash is exactly 64 ASCII bytes, so concatenation is unambiguous without a delimiter.
    for media_id in media_ids {
        hasher.update(media_id.as_bytes());
    }
    hasher.finalize().to_hex().to_string()
}

fn certified_closed_span(members: &[(i64, MediaRecord)], final_span_id: &str) -> bool {
    let member_count = members.len() as i64;
    members.iter().all(|(index, member)| {
        member.span.as_ref().is_some_and(|span| {
            span.role == SpanRole::Member
                && span.span_kind == SpanSpanKind::GoproChapter
                && span.span_id == final_span_id
                && span.index == Some(*index)
                && span.member_count == Some(member_count)
        })
    })
}

fn carried_provisional_span(members: &[(i64, MediaRecord)], final_span_id: &str) -> Option<String> {
    let ids = members
        .iter()
        .filter_map(|(_, member)| member.span.as_ref())
        .filter(|span| {
            span.role == SpanRole::Member
                && span.span_kind == SpanSpanKind::GoproChapter
                && span.span_id != final_span_id
        })
        .map(|span| span.span_id.clone())
        .collect::<BTreeSet<_>>();
    (ids.len() == 1).then(|| ids.into_iter().next().expect("one provisional span"))
}

fn provisional_span_id(group: &GroupKey, members: &[(i64, MediaRecord)]) -> String {
    let mut hasher = blake3::Hasher::new();
    hasher.update(b"memory-engine:gopro-provisional:v1\0");
    hasher.update(group.family.as_bytes());
    hasher.update(&[0]);
    hasher.update(group.recording.as_bytes());
    hasher.update(&[0]);
    // The earliest known chapter anchors different cameras that reused the same
    // four-digit recording number, while the group id keeps a trailing arrival
    // from changing provisional identity.
    hasher.update(members[0].1.media_id.as_bytes());
    hasher.finalize().to_hex().to_string()
}

fn identity_is_prefix(left: &[(i64, &str)], right: &[(i64, &str)]) -> bool {
    left.len() < right.len() && left.iter().zip(right).all(|(left, right)| left == right)
}

fn timeline(
    members: &[(i64, MediaRecord)],
    incomplete: bool,
) -> (
    Option<VideoProperties>,
    Vec<Option<RationalTime>>,
    SpanContinuity,
) {
    if incomplete {
        return (
            None,
            vec![None; members.len()],
            SpanContinuity::IncompleteSet,
        );
    }
    let videos = members
        .iter()
        .map(|(_, member)| member.video.as_ref())
        .collect::<Option<Vec<_>>>();
    let Some(videos) = videos else {
        let offsets = proxy_offsets(members).unwrap_or_else(|| vec![None; members.len()]);
        return (None, offsets, SpanContinuity::Unverified);
    };
    let base_rate = videos[0].duration.rate;
    if !base_rate.is_finite()
        || base_rate <= 0.0
        || videos.iter().any(|video| {
            !video.duration.value.is_finite()
                || video.duration.value < 0.0
                || !video.duration.rate.is_finite()
                || video.duration.rate <= 0.0
        })
    {
        return (None, vec![None; members.len()], SpanContinuity::Unverified);
    }

    let mut cumulative = 0.0;
    let mut offsets = Vec::with_capacity(videos.len());
    for video in &videos {
        offsets.push(Some(RationalTime {
            value: cumulative,
            rate: base_rate,
        }));
        cumulative += video.duration.value * base_rate / video.duration.rate;
    }
    let compatible = videos.iter().all(|video| {
        video.oriented_size == videos[0].oriented_size
            && video.frame_rate == videos[0].frame_rate
            && video.rotation_deg == videos[0].rotation_deg
            && video.video_codec == videos[0].video_codec
            && video.color_primaries == videos[0].color_primaries
            && video.transfer_characteristics == videos[0].transfer_characteristics
    });
    let assembly_video = compatible.then(|| {
        let mut video = videos[0].clone();
        video.duration = RationalTime {
            value: cumulative,
            rate: base_rate,
        };
        video
    });
    let continuity = if timecodes_are_gapless(&videos, &offsets, base_rate) == Some(true) {
        SpanContinuity::VerifiedGapless
    } else if timecodes_are_gapless(&videos, &offsets, base_rate) == Some(false) {
        SpanContinuity::VerifiedGap
    } else {
        SpanContinuity::Unverified
    };
    (assembly_video, offsets, continuity)
}

fn proxy_offsets(members: &[(i64, MediaRecord)]) -> Option<Vec<Option<RationalTime>>> {
    let indexes = members
        .iter()
        .map(|(_, member)| {
            member
                .proxies
                .as_deref()?
                .iter()
                .find(|proxy| proxy.kind == ProxyRefKind::VideoProxy480p)?
                .frame_index
                .as_ref()
        })
        .collect::<Option<Vec<_>>>()?;
    let base_rate = indexes.first()?.source_rate?;
    if !base_rate.is_finite()
        || base_rate <= 0.0
        || indexes.iter().any(|index| {
            index.entry_count <= 0
                || index.mapping != FrameIndexSidecarMapping::Identity
                || index
                    .source_rate
                    .is_none_or(|rate| !rate.is_finite() || rate <= 0.0)
        })
    {
        return None;
    }
    let mut cumulative = 0.0;
    Some(
        indexes
            .into_iter()
            .map(|index| {
                let offset = RationalTime {
                    value: cumulative,
                    rate: base_rate,
                };
                cumulative += index.entry_count as f64 * base_rate / index.source_rate.unwrap();
                Some(offset)
            })
            .collect(),
    )
}

fn timecodes_are_gapless(
    videos: &[&VideoProperties],
    offsets: &[Option<RationalTime>],
    base_rate: f64,
) -> Option<bool> {
    let first = videos.first()?.start_timecode.as_ref()?;
    if first.rate <= 0.0 || !first.rate.is_finite() || !first.value.is_finite() {
        return None;
    }
    let start = first.value * base_rate / first.rate;
    for (video, offset) in videos.iter().zip(offsets).skip(1) {
        let timecode = video.start_timecode.as_ref()?;
        if timecode.rate <= 0.0 || !timecode.rate.is_finite() || !timecode.value.is_finite() {
            return None;
        }
        let actual = timecode.value * base_rate / timecode.rate;
        let expected = start + offset.as_ref()?.value;
        if (actual - expected).abs() > 0.5 {
            return Some(false);
        }
    }
    Some(true)
}

fn new_assembly(
    sequence: &Sequence,
    member_ids: Vec<String>,
    video: Option<VideoProperties>,
    offset: Option<RationalTime>,
    member_count: Option<i64>,
    continuity: SpanContinuity,
    now: &str,
) -> MediaRecord {
    let first = &sequence.members[0].1;
    MediaRecord {
        schema_version: first.schema_version.clone(),
        media_id: sequence.span_id.clone(),
        asset_kind: MediaRecordAssetKind::VirtualAssembly,
        kind: MediaRecordKind::Video,
        byte_size: 0,
        mime_type: None,
        file_format: None,
        sources: Vec::new(),
        span: Some(Span {
            span_id: sequence.span_id.clone(),
            role: SpanRole::Assembly,
            span_kind: SpanSpanKind::GoproChapter,
            index: None,
            member_count,
            member_media_ids: Some(member_ids),
            offset_in_span: offset,
            continuity: Some(continuity),
        }),
        capture: first.capture.clone(),
        image: None,
        video,
        perceptual: None,
        proxies: Some(Vec::new()),
        processing: assembly_processing(now),
        quality: None,
        content: None,
        faces: None,
        dedupe: None,
        exclusion: Some(ExclusionState {
            excluded_from_automation: false,
            reasons: Some(Vec::new()),
            user_override: None,
        }),
        user: None,
        model_runs: Some(Vec::new()),
        first_seen_at: first.first_seen_at.clone().or_else(|| Some(now.to_owned())),
        updated_at: Some(now.to_owned()),
    }
}

pub(crate) fn merge_existing_assembly(
    existing: &MediaRecord,
    desired: &MediaRecord,
    now: &str,
) -> MediaRecord {
    let mut merged = existing.clone();
    merged.schema_version = desired.schema_version.clone();
    merged.asset_kind = MediaRecordAssetKind::VirtualAssembly;
    merged.kind = MediaRecordKind::Video;
    merged.byte_size = 0;
    merged.mime_type = None;
    merged.file_format = None;
    merged.sources.clear();
    let mut desired_span = desired.span.clone();
    if let (Some(previous), Some(next)) = (existing.span.as_ref(), desired_span.as_mut()) {
        if previous.role == SpanRole::Assembly
            && previous.span_id == next.span_id
            && previous.member_media_ids == next.member_media_ids
        {
            if next.continuity != Some(SpanContinuity::IncompleteSet)
                && next.offset_in_span.is_none()
            {
                next.offset_in_span = previous.offset_in_span.clone();
            }
            next.continuity = Some(prefer_known_continuity(
                next.continuity.unwrap_or(SpanContinuity::Unverified),
                previous.continuity,
            ));
        }
    }
    merged.span = desired_span;
    if desired.video.is_some()
        || desired
            .span
            .as_ref()
            .is_some_and(|span| span.continuity == Some(SpanContinuity::IncompleteSet))
    {
        merged.video = desired.video.clone();
    }
    merged.proxies = Some(Vec::new());
    if &merged != existing {
        merged.updated_at = Some(now.to_owned());
    }
    merged
}

fn prefer_known_continuity(
    current: SpanContinuity,
    previous: Option<SpanContinuity>,
) -> SpanContinuity {
    if current == SpanContinuity::Unverified {
        previous.unwrap_or(current)
    } else {
        current
    }
}

fn assembly_processing(now: &str) -> ProcessingState {
    ProcessingState {
        state: ProcessingStateState::Discovered,
        stages: ProcessingStateStages {
            hash: Some(not_applicable("virtual assembly has no bytes of its own")),
            metadata: Some(done_stage(now)),
            thumbnail: None,
            video_proxy: Some(not_applicable(
                "assembly reads the members' proxies in order",
            )),
            perceptual_hash: None,
            classical_quality: None,
            image_embedding: None,
            face_detection: None,
            // Added by contracts: the analysis stage records detection and
            // embedding separately, so a detector that ran and an embedder
            // that was missing leaves the library with face boxes rather
            // than with neither. Ingest runs neither.
            face_embedding: None,
            iqa: None,
            aesthetic: None,
            tagging: None,
            safety: None,
            ocr: None,
            shot_detection: None,
            transcription: None,
            audio_events: None,
            moment_scoring: None,
        },
    }
}

fn not_applicable(reason: &str) -> StageState {
    StageState {
        status: StageStateStatus::NotApplicable,
        attempts: Some(0),
        completed_at: None,
        job_id: None,
        skip_reason: Some(reason.to_owned()),
        last_error: None,
    }
}

fn done_stage(now: &str) -> StageState {
    StageState {
        status: StageStateStatus::Done,
        attempts: Some(1),
        completed_at: Some(now.to_owned()),
        job_id: None,
        skip_reason: None,
        last_error: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn member(fixture: &str) -> MediaRecord {
        serde_json::from_str(fixture).expect("golden MediaRecord")
    }

    #[test]
    fn parses_modern_and_legacy_chapter_names() {
        let gh = parse_filename("GH011234.MP4").expect("modern HEVC");
        assert_eq!(
            (gh.family, gh.recording.as_str(), gh.index),
            ("gh", "1234", 0)
        );
        let gx = parse_filename("gx021234.mp4").expect("modern chapter two");
        assert_eq!(
            (gx.family, gx.recording.as_str(), gx.index),
            ("gx", "1234", 1)
        );
        let first = parse_filename("GOPR0123.MP4").expect("legacy first chapter");
        assert_eq!(
            (first.family, first.recording.as_str(), first.index),
            ("legacy", "0123", 0)
        );
        let next = parse_filename("GP010123.MP4").expect("legacy continuation");
        assert_eq!(
            (next.family, next.recording.as_str(), next.index),
            ("legacy", "0123", 1)
        );
        assert!(parse_filename("G0012345.JPG").is_none());
        assert!(parse_filename("GH001234.MP4").is_none());
        assert!(parse_filename("GH011234.LRV").is_some());
    }

    #[test]
    fn golden_chapters_produce_one_content_addressed_gapless_assembly() {
        let first = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ));
        let second = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-02.json"
        ));
        let expected = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-span-assembly.json"
        ));
        let built = build(&[second.clone(), first.clone()]);
        assert!(built.issues.is_empty());
        assert!(built.members.is_empty());
        assert_eq!(built.assemblies.len(), 1);
        let assembly = &built.assemblies[0];
        assert_eq!(assembly.media_id, expected.media_id);
        assert_eq!(assembly.asset_kind, MediaRecordAssetKind::VirtualAssembly);
        assert_eq!(assembly.byte_size, 0);
        assert!(assembly.sources.is_empty());
        assert_eq!(assembly.proxies.as_ref().map(Vec::len), Some(0));
        let span = assembly.span.as_ref().expect("assembly span");
        assert_eq!(span.role, SpanRole::Assembly);
        assert_eq!(span.span_id, assembly.media_id);
        assert_eq!(
            span.member_media_ids.as_deref(),
            expected
                .span
                .as_ref()
                .and_then(|span| span.member_media_ids.as_deref())
        );
        assert_eq!(span.continuity, Some(SpanContinuity::VerifiedGapless));
        let video = assembly.video.as_ref().expect("aggregate video properties");
        assert_eq!(video.duration.value, 61_593.0);
        assert_eq!(first.span.as_ref().unwrap().index, Some(0));
        assert_eq!(
            second
                .span
                .as_ref()
                .unwrap()
                .offset_in_span
                .as_ref()
                .unwrap()
                .value,
            42_405.0
        );
    }

    #[test]
    fn missing_chapter_marks_the_assembly_incomplete() {
        let first = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ));
        let mut third = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-02.json"
        ));
        third.sources[0].path = "/Volumes/GOPRO/DCIM/100GOPRO/GH031234.MP4".to_owned();
        third.sources[0].original_filename = Some("GH031234.MP4".to_owned());
        let built = build(&[first, third]);
        assert_eq!(built.assemblies.len(), 1);
        assert_eq!(
            built.assemblies[0].span.as_ref().unwrap().continuity,
            Some(SpanContinuity::IncompleteSet)
        );
        assert!(built.assemblies[0].video.is_none());
        assert_eq!(
            built.assemblies[0].span.as_ref().unwrap().member_count,
            None
        );
        assert!(built.assemblies[0]
            .span
            .as_ref()
            .unwrap()
            .offset_in_span
            .is_none());
        assert!(built.members.iter().all(|member| member
            .span
            .as_ref()
            .is_some_and(|span| span.offset_in_span.is_none())));
    }

    #[test]
    fn trailing_chapter_arrival_keeps_one_provisional_identity() {
        let mut first = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ));
        let mut second = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-02.json"
        ));
        first.span = None;
        second.span = None;
        let partial = build(&[first.clone(), second.clone()]);
        let partial_id = partial.assemblies[0].media_id.clone();
        assert_eq!(
            partial.assemblies[0].span.as_ref().unwrap().continuity,
            Some(SpanContinuity::IncompleteSet)
        );

        let mut third = second.clone();
        third.media_id = "c".repeat(64);
        third.sources[0].path = "/Volumes/GOPRO/DCIM/100GOPRO/GH031234.MP4".to_owned();
        third.sources[0].original_filename = Some("GH031234.MP4".to_owned());
        let complete_copy = build(&[first, second, third]);

        assert_eq!(complete_copy.assemblies.len(), 1);
        assert_eq!(complete_copy.assemblies[0].media_id, partial_id);
        assert_eq!(
            complete_copy.assemblies[0]
                .span
                .as_ref()
                .unwrap()
                .member_media_ids
                .as_ref()
                .map(Vec::len),
            Some(3)
        );
        assert_eq!(
            complete_copy.assemblies[0]
                .span
                .as_ref()
                .unwrap()
                .continuity,
            Some(SpanContinuity::IncompleteSet)
        );
    }

    #[test]
    fn previously_published_prefix_identity_is_reused_when_the_tail_arrives() {
        let first = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ));
        let mut second = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-02.json"
        ));
        let previous_id = first.span.as_ref().unwrap().span_id.clone();
        let mut third = second.clone();
        third.media_id = "c".repeat(64);
        third.span = None;
        third.sources[0].path = "/Volumes/GOPRO/DCIM/100GOPRO/GH031234.MP4".to_owned();
        third.sources[0].original_filename = Some("GH031234.MP4".to_owned());
        // Keep chapter two's previously published span to reproduce an upgrade
        // from the old premature-closure behavior.
        second.span.as_mut().unwrap().continuity = Some(SpanContinuity::VerifiedGapless);

        let rebuilt = build(&[first, second, third]);

        assert_eq!(rebuilt.assemblies.len(), 1);
        assert_eq!(rebuilt.assemblies[0].media_id, previous_id);
        assert_eq!(
            rebuilt.assemblies[0].span.as_ref().unwrap().continuity,
            Some(SpanContinuity::IncompleteSet)
        );
    }

    #[test]
    fn proxy_frame_indexes_supply_offsets_before_video_metadata_exists() {
        let mut first = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ));
        let mut second = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-02.json"
        ));
        first.video = None;
        first.span.as_mut().unwrap().offset_in_span = None;
        first.span.as_mut().unwrap().continuity = Some(SpanContinuity::Unverified);
        second.video = None;
        second.span.as_mut().unwrap().offset_in_span = None;
        second.span.as_mut().unwrap().continuity = Some(SpanContinuity::Unverified);
        let built = build(&[second, first]);
        assert_eq!(built.assemblies.len(), 1);
        assert!(built.assemblies[0].video.is_none());
        assert_eq!(
            built.assemblies[0]
                .span
                .as_ref()
                .unwrap()
                .offset_in_span
                .as_ref()
                .unwrap()
                .value,
            0.0
        );
        assert_eq!(
            built.members[1]
                .span
                .as_ref()
                .unwrap()
                .offset_in_span
                .as_ref()
                .unwrap()
                .value,
            42_405.0
        );
        assert_eq!(
            built.assemblies[0].span.as_ref().unwrap().continuity,
            Some(SpanContinuity::Unverified)
        );
    }

    #[test]
    fn variable_rate_proxy_indexes_do_not_invent_constant_rate_offsets() {
        let mut first = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ));
        let mut second = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-02.json"
        ));
        first.video = None;
        first.span = None;
        first.proxies.as_mut().unwrap()[0]
            .frame_index
            .as_mut()
            .unwrap()
            .mapping = FrameIndexSidecarMapping::Table;
        second.video = None;
        second.span = None;
        let built = build(&[first, second]);
        assert!(built.members.iter().all(|member| member
            .span
            .as_ref()
            .unwrap()
            .offset_in_span
            .is_none()));
        assert!(built.assemblies[0]
            .span
            .as_ref()
            .unwrap()
            .offset_in_span
            .is_none());
    }

    #[test]
    fn duplicate_chapter_index_is_ambiguous_and_never_assembled() {
        let first = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-01.json"
        ));
        let mut duplicate = first.clone();
        duplicate.media_id = "f".repeat(64);
        let second = member(include_str!(
            "../../../contracts/fixtures/media-record/valid/video-gopro-chapter-02.json"
        ));
        let built = build(&[first, duplicate, second]);
        assert!(built.assemblies.is_empty());
        assert_eq!(built.issues.len(), 1);
    }
}
