use image::DynamicImage;
use memory_engine_contracts::MediaRecordFileFormat;

#[derive(Debug)]
pub(crate) struct DecodedStill {
    pub image: DynamicImage,
    pub has_alpha: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum StillDecodeError {
    MissingCapability,
    DecodeFailed,
}

pub(crate) fn supports(file_format: MediaRecordFileFormat) -> bool {
    match file_format {
        MediaRecordFileFormat::Jpeg
        | MediaRecordFileFormat::Png
        | MediaRecordFileFormat::Gif
        | MediaRecordFileFormat::Bmp
        | MediaRecordFileFormat::Webp
        | MediaRecordFileFormat::Tiff => true,
        MediaRecordFileFormat::Heic | MediaRecordFileFormat::Heif | MediaRecordFileFormat::Avif => {
            platform::supports(file_format)
        }
        _ => false,
    }
}

pub(crate) fn decode(
    bytes: &[u8],
    file_format: MediaRecordFileFormat,
) -> Result<DecodedStill, StillDecodeError> {
    if !supports(file_format) {
        return Err(StillDecodeError::MissingCapability);
    }

    match file_format {
        MediaRecordFileFormat::Heic | MediaRecordFileFormat::Heif | MediaRecordFileFormat::Avif => {
            platform::decode(bytes)
        }
        _ => {
            let image =
                image::load_from_memory(bytes).map_err(|_| StillDecodeError::DecodeFailed)?;
            let has_alpha = image.color().has_alpha();
            Ok(DecodedStill { image, has_alpha })
        }
    }
}

pub(crate) fn missing_capability_code(file_format: MediaRecordFileFormat) -> &'static str {
    match file_format {
        MediaRecordFileFormat::Heic => "missing_capability_heic_decoder",
        MediaRecordFileFormat::Heif => "missing_capability_heif_decoder",
        MediaRecordFileFormat::Avif => "missing_capability_avif_decoder",
        _ => "missing_capability_still_decoder",
    }
}

pub(crate) fn is_heif_family(file_format: MediaRecordFileFormat) -> bool {
    matches!(
        file_format,
        MediaRecordFileFormat::Heic | MediaRecordFileFormat::Heif | MediaRecordFileFormat::Avif
    )
}

#[cfg(target_os = "macos")]
mod platform {
    use std::sync::OnceLock;

    use image::{DynamicImage, RgbaImage};
    use memory_engine_contracts::MediaRecordFileFormat;
    use memory_engine_ingest_imageio as image_io;

    use super::{DecodedStill, StillDecodeError};

    pub(super) fn supports(file_format: MediaRecordFileFormat) -> bool {
        static SOURCE_TYPES: OnceLock<Vec<String>> = OnceLock::new();
        let source_types = SOURCE_TYPES.get_or_init(image_io::supported_source_identifiers);
        source_types.iter().any(|identifier| {
            let identifier = identifier.to_ascii_lowercase();
            match file_format {
                MediaRecordFileFormat::Heic | MediaRecordFileFormat::Heif => {
                    identifier.contains("heic") || identifier.contains("heif")
                }
                MediaRecordFileFormat::Avif => identifier.contains("avif"),
                _ => false,
            }
        })
    }

    pub(super) fn decode(bytes: &[u8]) -> Result<DecodedStill, StillDecodeError> {
        let decoded =
            image_io::decode_first_image(bytes).map_err(|_| StillDecodeError::DecodeFailed)?;
        let width = u32::try_from(decoded.width).map_err(|_| StillDecodeError::DecodeFailed)?;
        let height = u32::try_from(decoded.height).map_err(|_| StillDecodeError::DecodeFailed)?;
        let expected_len = decoded
            .width
            .checked_mul(decoded.height)
            .and_then(|pixels| pixels.checked_mul(4))
            .ok_or(StillDecodeError::DecodeFailed)?;
        if decoded.rgba.len() != expected_len {
            return Err(StillDecodeError::DecodeFailed);
        }

        let image = RgbaImage::from_raw(width, height, decoded.rgba)
            .map(DynamicImage::ImageRgba8)
            .ok_or(StillDecodeError::DecodeFailed)?;
        Ok(DecodedStill {
            image,
            has_alpha: decoded.has_alpha,
        })
    }
}

#[cfg(not(target_os = "macos"))]
mod platform {
    use memory_engine_contracts::MediaRecordFileFormat;

    use super::{DecodedStill, StillDecodeError};

    pub(super) fn supports(_file_format: MediaRecordFileFormat) -> bool {
        false
    }

    pub(super) fn decode(_bytes: &[u8]) -> Result<DecodedStill, StillDecodeError> {
        Err(StillDecodeError::MissingCapability)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn built_in_formats_are_always_available() {
        assert!(supports(MediaRecordFileFormat::Jpeg));
        assert!(supports(MediaRecordFileFormat::Png));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn image_io_advertises_all_supported_iso_bmff_stills() {
        assert!(supports(MediaRecordFileFormat::Heic));
        assert!(supports(MediaRecordFileFormat::Heif));
        assert!(supports(MediaRecordFileFormat::Avif));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn image_io_decodes_heic_from_the_hashed_bytes() {
        let directory = tempfile::tempdir().expect("tempdir");
        let jpeg_path = directory.path().join("source.jpg");
        let heic_path = directory.path().join("source.heic");
        image::RgbImage::from_fn(2, 4, |_x, y| {
            if y < 2 {
                image::Rgb([220, 30, 20])
            } else {
                image::Rgb([20, 30, 220])
            }
        })
        .save(&jpeg_path)
        .expect("JPEG fixture");
        let output = std::process::Command::new("sips")
            .arg("-s")
            .arg("format")
            .arg("heic")
            .arg(&jpeg_path)
            .arg("--out")
            .arg(&heic_path)
            .output()
            .expect("run sips");
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let encoded = std::fs::read(heic_path).expect("HEIC fixture");
        let decoded = decode(&encoded, MediaRecordFileFormat::Heic).expect("ImageIO HEIC decode");
        assert_eq!(decoded.image.width(), 2);
        assert_eq!(decoded.image.height(), 4);
        let rgba = decoded.image.to_rgba8();
        let top = rgba.get_pixel(0, 0);
        let bottom = rgba.get_pixel(0, 3);
        assert!(top[0] > top[2], "top row should remain red: {top:?}");
        assert!(
            bottom[2] > bottom[0],
            "bottom row should remain blue: {bottom:?}"
        );
    }
}
