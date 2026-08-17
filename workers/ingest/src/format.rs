use memory_engine_contracts::{MediaRecordFileFormat, MediaRecordKind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct DetectedFormat {
    pub file_format: MediaRecordFileFormat,
    pub kind: MediaRecordKind,
    pub mime_type: &'static str,
}

pub(crate) fn detect(bytes: &[u8]) -> Option<DetectedFormat> {
    if bytes.starts_with(&[0xff, 0xd8, 0xff]) {
        return Some(image(MediaRecordFileFormat::Jpeg, "image/jpeg"));
    }
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Some(image(MediaRecordFileFormat::Png, "image/png"));
    }
    if bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a") {
        return Some(image(MediaRecordFileFormat::Gif, "image/gif"));
    }
    if bytes.starts_with(b"BM") {
        return Some(image(MediaRecordFileFormat::Bmp, "image/bmp"));
    }
    if bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP" {
        return Some(image(MediaRecordFileFormat::Webp, "image/webp"));
    }
    if bytes.starts_with(b"II*\0") || bytes.starts_with(b"MM\0*") {
        return Some(image(MediaRecordFileFormat::Tiff, "image/tiff"));
    }
    if bytes.len() >= 12 && &bytes[4..8] == b"ftyp" {
        return detect_iso_bmff(&bytes[8..12]);
    }
    if bytes.starts_with(b"RIFF") && bytes.len() >= 12 && &bytes[8..12] == b"AVI " {
        return Some(video(MediaRecordFileFormat::Avi, "video/x-msvideo"));
    }
    if bytes.starts_with(b"\x1a\x45\xdf\xa3") {
        return Some(video(MediaRecordFileFormat::Mkv, "video/x-matroska"));
    }
    if bytes.starts_with(b"RIFF") && bytes.len() >= 12 && &bytes[8..12] == b"WAVE" {
        return Some(audio(MediaRecordFileFormat::Wav, "audio/wav"));
    }
    if bytes.starts_with(b"fLaC") {
        return Some(audio(MediaRecordFileFormat::Flac, "audio/flac"));
    }
    if bytes.starts_with(b"ID3") || bytes.starts_with(&[0xff, 0xfb]) {
        return Some(audio(MediaRecordFileFormat::Mp3, "audio/mpeg"));
    }
    None
}

fn detect_iso_bmff(brand: &[u8]) -> Option<DetectedFormat> {
    match brand {
        b"heic" | b"heix" | b"hevc" | b"hevx" => {
            Some(image(MediaRecordFileFormat::Heic, "image/heic"))
        }
        b"mif1" | b"msf1" => Some(image(MediaRecordFileFormat::Heif, "image/heif")),
        b"avif" | b"avis" => Some(image(MediaRecordFileFormat::Avif, "image/avif")),
        b"qt  " => Some(video(MediaRecordFileFormat::Mov, "video/quicktime")),
        b"M4V " | b"M4VH" | b"M4VP" => Some(video(MediaRecordFileFormat::M4v, "video/x-m4v")),
        b"M4A " => Some(audio(MediaRecordFileFormat::M4a, "audio/mp4")),
        _ => Some(video(MediaRecordFileFormat::Mp4, "video/mp4")),
    }
}

fn image(file_format: MediaRecordFileFormat, mime_type: &'static str) -> DetectedFormat {
    DetectedFormat {
        file_format,
        kind: MediaRecordKind::Image,
        mime_type,
    }
}

fn video(file_format: MediaRecordFileFormat, mime_type: &'static str) -> DetectedFormat {
    DetectedFormat {
        file_format,
        kind: MediaRecordKind::Video,
        mime_type,
    }
}

fn audio(file_format: MediaRecordFileFormat, mime_type: &'static str) -> DetectedFormat {
    DetectedFormat {
        file_format,
        kind: MediaRecordKind::Audio,
        mime_type,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_heic_by_content_even_with_wrong_extension() {
        let mut bytes = vec![0, 0, 0, 24];
        bytes.extend_from_slice(b"ftypheic");
        let detected = detect(&bytes).expect("HEIC signature");
        assert_eq!(detected.file_format, MediaRecordFileFormat::Heic);
    }
}
