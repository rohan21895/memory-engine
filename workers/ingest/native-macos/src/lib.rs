//! Narrow, audited safe wrapper around macOS ImageIO/CoreGraphics.
//!
//! The public API owns every buffer it exposes. Unsafe code is confined to the framework calls
//! whose generic dictionary arguments are `None`, and to a bitmap context whose lifetime is
//! strictly shorter than its backing `Vec`.

use core::ffi::c_void;

use objc2_core_foundation::{CFArray, CFData, CFRetained, CFString, CGPoint, CGRect, CGSize};
use objc2_core_graphics::{
    CGBitmapContextCreate, CGColorSpace, CGContext, CGImage, CGImageAlphaInfo, CGImageByteOrderInfo,
};
use objc2_image_io::CGImageSource;
use thiserror::Error;

#[derive(Debug)]
pub struct DecodedImage {
    pub width: usize,
    pub height: usize,
    pub rgba: Vec<u8>,
    pub has_alpha: bool,
}

#[derive(Debug, Error)]
pub enum DecodeError {
    #[error("ImageIO could not create an image source")]
    Source,
    #[error("ImageIO source has no decodable primary image")]
    Image,
    #[error("decoded dimensions exceed addressable memory")]
    Dimensions,
    #[error("CoreGraphics could not create an RGBA bitmap context")]
    BitmapContext,
}

pub fn supported_source_identifiers() -> Vec<String> {
    // SAFETY: ImageIO documents this as a retained CFArray containing only CFString values.
    let untyped = unsafe { CGImageSource::type_identifiers() };
    // SAFETY: The element type is guaranteed by CGImageSourceCopyTypeIdentifiers.
    let identifiers = unsafe { CFRetained::cast_unchecked::<CFArray<CFString>>(untyped) };
    identifiers
        .to_vec()
        .into_iter()
        .map(|identifier| identifier.to_string())
        .collect()
}

pub fn decode_first_image(bytes: &[u8]) -> Result<DecodedImage, DecodeError> {
    let data = CFData::from_bytes(bytes);
    // SAFETY: `data` is a valid immutable CFData and no untyped options dictionary is supplied.
    let source = unsafe { CGImageSource::with_data(&data, None) }.ok_or(DecodeError::Source)?;
    // SAFETY: Index zero is the primary still image and no untyped options dictionary is supplied.
    let image = unsafe { source.image_at_index(0, None) }.ok_or(DecodeError::Image)?;
    let width = CGImage::width(Some(&image));
    let height = CGImage::height(Some(&image));
    if width == 0 || height == 0 {
        return Err(DecodeError::Dimensions);
    }
    let byte_len = width
        .checked_mul(height)
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or(DecodeError::Dimensions)?;
    let bytes_per_row = width.checked_mul(4).ok_or(DecodeError::Dimensions)?;
    let mut rgba = vec![0_u8; byte_len];
    let color_space = CGColorSpace::new_device_rgb().ok_or(DecodeError::BitmapContext)?;
    let bitmap_info = CGImageAlphaInfo::PremultipliedLast.0 | CGImageByteOrderInfo::Order32Big.0;

    // SAFETY: `rgba` is initialized, correctly sized for the declared stride and dimensions, and
    // remains pinned by its Vec allocation until the context is explicitly dropped below.
    let context = unsafe {
        CGBitmapContextCreate(
            rgba.as_mut_ptr().cast::<c_void>(),
            width,
            height,
            8,
            bytes_per_row,
            Some(&color_space),
            bitmap_info,
        )
    }
    .ok_or(DecodeError::BitmapContext)?;
    let rect = CGRect::new(CGPoint::ZERO, CGSize::new(width as f64, height as f64));
    CGContext::draw_image(Some(&context), rect, Some(&image));
    drop(context);

    let alpha_info = CGImage::alpha_info(Some(&image));
    let has_alpha = !matches!(
        alpha_info,
        CGImageAlphaInfo::None | CGImageAlphaInfo::NoneSkipFirst | CGImageAlphaInfo::NoneSkipLast
    );
    if has_alpha {
        unpremultiply_rgba(&mut rgba);
    }
    Ok(DecodedImage {
        width,
        height,
        rgba,
        has_alpha,
    })
}

fn unpremultiply_rgba(rgba: &mut [u8]) {
    for pixel in rgba.chunks_exact_mut(4) {
        let alpha = u32::from(pixel[3]);
        if alpha == 0 {
            pixel[..3].fill(0);
            continue;
        }
        for channel in &mut pixel[..3] {
            let straight = (u32::from(*channel) * 255 + alpha / 2) / alpha;
            *channel = straight.min(255) as u8;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::unpremultiply_rgba;

    #[test]
    fn converts_core_graphics_premultiplied_pixels_to_straight_rgba() {
        let mut rgba = [50, 25, 0, 128, 99, 88, 77, 0];
        unpremultiply_rgba(&mut rgba);
        assert_eq!(rgba, [100, 50, 0, 128, 0, 0, 0, 0]);
    }
}
