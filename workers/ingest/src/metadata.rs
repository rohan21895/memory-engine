use std::{ffi::OsStr, io::Cursor, path::Path};

use chrono::{FixedOffset, NaiveDateTime, Utc};
use exif::{In, Reader, Tag};
use memory_engine_contracts::{
    Capture, CaptureMetadataPresentItem, DeviceInfo, TimeAssertion, TimeAssertionPrecision,
    TimeSource,
};

pub(crate) struct ExtractedMetadata {
    pub capture: Capture,
    pub orientation: u32,
}

pub(crate) fn extract(bytes: &[u8], path: &Path) -> ExtractedMetadata {
    let exif = Reader::new()
        .read_from_container(&mut Cursor::new(bytes))
        .ok();
    let has_exif = exif
        .as_ref()
        .is_some_and(|metadata| metadata.fields().next().is_some());
    let orientation = exif
        .as_ref()
        .and_then(|metadata| metadata.get_field(Tag::Orientation, In::PRIMARY))
        .and_then(|field| field.value.get_uint(0))
        .filter(|value| (1..=8).contains(value))
        .unwrap_or(1);

    let exif_time = exif.as_ref().and_then(|metadata| {
        [Tag::DateTimeOriginal, Tag::DateTimeDigitized]
            .into_iter()
            .find_map(|tag| {
                metadata
                    .get_field(tag, In::PRIMARY)
                    .and_then(|field| parse_exif_datetime(&field.display_value().to_string()))
                    .map(|local| (local, tag))
            })
    });

    let captured_at = if let Some((local, tag)) = exif_time {
        // An offset that IS in the file is the one case the contract says `utc`
        // may be populated: "present only when the zone is actually known
        // (explicit offset in metadata, or GPS-derived zone). Never fabricate
        // this by assuming the machine's local zone." EXIF 2.31 records that
        // offset in OffsetTimeOriginal / OffsetTimeDigitized, alongside the
        // datetime tag it belongs to.
        //
        // Leaving it unread made every record undated as far as anything
        // downstream is concerned: `captured_utc` is what media-db's
        // chronological listing filters on and what the album engine needs to
        // build an event, so a library of perfectly timestamped photos
        // clustered as "undated" and no album could be planned from it.
        let offset_tag = if tag == Tag::DateTimeOriginal {
            Tag::OffsetTimeOriginal
        } else {
            Tag::OffsetTimeDigitized
        };
        let offset_minutes = exif
            .as_ref()
            .and_then(|metadata| {
                metadata
                    .get_field(offset_tag, In::PRIMARY)
                    .or_else(|| metadata.get_field(Tag::OffsetTime, In::PRIMARY))
            })
            .and_then(|field| parse_utc_offset(&field.display_value().to_string()));
        TimeAssertion {
            utc: offset_minutes.and_then(|minutes| to_utc(&local, minutes)),
            local: Some(local),
            timezone: None,
            utc_offset_minutes: offset_minutes,
            source: if tag == Tag::DateTimeOriginal {
                TimeSource::ExifDatetimeOriginal
            } else {
                TimeSource::ExifDatetimeDigitized
            },
            precision: TimeAssertionPrecision::Second,
            confidence: 0.95,
            inferred_from_media_ids: None,
        }
    } else if let Some((local, precision)) = filename_time(path.file_name().unwrap_or_default()) {
        TimeAssertion {
            local: Some(local),
            utc: None,
            timezone: None,
            utc_offset_minutes: None,
            source: TimeSource::FilenamePattern,
            precision,
            confidence: 0.55,
            inferred_from_media_ids: None,
        }
    } else {
        TimeAssertion {
            local: None,
            utc: None,
            timezone: None,
            utc_offset_minutes: None,
            source: TimeSource::Unknown,
            precision: TimeAssertionPrecision::Unknown,
            confidence: 0.0,
            inferred_from_media_ids: None,
        }
    };

    let device = exif.as_ref().and_then(|metadata| {
        let value = |tag| {
            metadata
                .get_field(tag, In::PRIMARY)
                .map(|field| clean_display(field.display_value().to_string()))
                .filter(|text| !text.is_empty())
        };
        let device = DeviceInfo {
            make: value(Tag::Make),
            model: value(Tag::Model),
            lens: value(Tag::LensModel),
            software: value(Tag::Software),
            body_serial_hash: None,
        };
        if device.make.is_some()
            || device.model.is_some()
            || device.lens.is_some()
            || device.software.is_some()
        {
            Some(device)
        } else {
            None
        }
    });

    ExtractedMetadata {
        capture: Capture {
            captured_at,
            metadata_present: if has_exif {
                vec![CaptureMetadataPresentItem::Exif]
            } else {
                Vec::new()
            },
            gps: None,
            device,
            exposure: None,
        },
        orientation,
    }
}

fn parse_exif_datetime(value: &str) -> Option<String> {
    let cleaned = clean_display(value.to_owned());
    cleaned
        .as_bytes()
        .windows(19)
        .filter_map(|candidate| std::str::from_utf8(candidate).ok())
        .find_map(|candidate| {
            ["%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"]
                .into_iter()
                .find_map(|format| NaiveDateTime::parse_from_str(candidate, format).ok())
                .map(|date| date.format("%Y-%m-%dT%H:%M:%S").to_string())
        })
}

/// EXIF offset strings are "+HH:MM" / "-HH:MM". Returns minutes east of UTC.
///
/// EXIF 2.31 also permits the tag to be present and blank ("   :  ") on a
/// camera that has the field but no configured zone, which is why an
/// unparseable value yields None rather than zero. Zero is a real offset
/// (UTC), and treating "unknown" as "UTC" would shift an entire holiday.
fn parse_utc_offset(value: &str) -> Option<i64> {
    let cleaned = clean_display(value.to_owned());
    let bytes = cleaned.as_bytes();
    if bytes.len() < 6 {
        return None;
    }
    let sign = match bytes[0] {
        b'+' => 1,
        b'-' => -1,
        _ => return None,
    };
    if bytes[3] != b':' {
        return None;
    }
    let hours: i64 = cleaned.get(1..3)?.parse().ok()?;
    let minutes: i64 = cleaned.get(4..6)?.parse().ok()?;
    if hours > 18 || minutes > 59 {
        return None;
    }
    let total = sign * (hours * 60 + minutes);
    // Matches TimeAssertion.utc_offset_minutes, which the schema bounds to
    // +/- 18 hours.
    (-1080..=1080).contains(&total).then_some(total)
}

/// Resolve a naive local reading plus a known offset into an RFC 3339 instant.
fn to_utc(local: &str, offset_minutes: i64) -> Option<String> {
    let naive = NaiveDateTime::parse_from_str(local, "%Y-%m-%dT%H:%M:%S").ok()?;
    let offset = FixedOffset::east_opt(i32::try_from(offset_minutes).ok()? * 60)?;
    let resolved = naive.and_local_timezone(offset).single()?;
    Some(resolved.with_timezone(&Utc).to_rfc3339())
}

fn clean_display(value: String) -> String {
    value.trim().trim_matches('"').trim_matches('\'').to_owned()
}

fn filename_time(filename: &OsStr) -> Option<(String, TimeAssertionPrecision)> {
    let filename = filename.to_string_lossy();
    for prefix in ["IMG-", "VID-", "AUD-"] {
        if let Some(rest) = filename.strip_prefix(prefix) {
            if rest.len() >= 8 && rest.as_bytes()[..8].iter().all(u8::is_ascii_digit) {
                return parse_compact_date(&rest[..8]);
            }
        }
    }

    for marker in ["WhatsApp Image ", "WhatsApp Video "] {
        if let Some(rest) = filename.strip_prefix(marker) {
            let candidate = rest.get(..19)?;
            let normalized = candidate.replace(" at ", "T").replace('.', ":");
            if NaiveDateTime::parse_from_str(&normalized, "%Y-%m-%dT%H:%M:%S").is_ok() {
                return Some((normalized, TimeAssertionPrecision::Second));
            }
        }
    }
    None
}

fn parse_compact_date(value: &str) -> Option<(String, TimeAssertionPrecision)> {
    let normalized = format!("{}-{}-{}T00:00:00", &value[..4], &value[4..6], &value[6..8]);
    NaiveDateTime::parse_from_str(&normalized, "%Y-%m-%dT%H:%M:%S")
        .ok()
        .map(|_| (normalized, TimeAssertionPrecision::Day))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recovers_whatsapp_compact_date_without_exif() {
        let metadata = extract(&[], Path::new("IMG-20240203-WA0001.jpg"));
        assert_eq!(
            metadata.capture.captured_at.local.as_deref(),
            Some("2024-02-03T00:00:00")
        );
        assert_eq!(
            metadata.capture.captured_at.source,
            TimeSource::FilenamePattern
        );
        assert!(metadata.capture.metadata_present.is_empty());
    }

    #[test]
    fn parses_signed_exif_offsets_and_refuses_junk() {
        assert_eq!(parse_utc_offset("+05:30"), Some(330));
        assert_eq!(parse_utc_offset("\"-08:00\""), Some(-480));
        assert_eq!(parse_utc_offset("+00:00"), Some(0));
        // Present but unset is not UTC.
        assert_eq!(parse_utc_offset("   :  "), None);
        assert_eq!(parse_utc_offset("05:30"), None);
        assert_eq!(parse_utc_offset("+19:00"), None);
    }

    #[test]
    fn resolves_utc_only_from_a_real_offset() {
        assert_eq!(
            to_utc("2026-03-14T09:10:00", 330).as_deref(),
            Some("2026-03-14T03:40:00+00:00")
        );
        assert_eq!(
            to_utc("2026-03-14T09:10:00", -480).as_deref(),
            Some("2026-03-14T17:10:00+00:00")
        );
        assert_eq!(to_utc("not-a-time", 0), None);
    }

    #[test]
    fn exif_less_nonconforming_name_stays_unknown() {
        let metadata = extract(&[], Path::new("scan_0043.jpg"));
        assert_eq!(metadata.capture.captured_at.source, TimeSource::Unknown);
        assert!(metadata.capture.captured_at.local.is_none());
    }
}
