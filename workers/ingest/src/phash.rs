//! `phash-dct-64-v2`, the perceptual hash written for every still and every
//! sampled keyframe.
//!
//! The encoding is frozen in `contracts/schemas/common.schema.json` under
//! `PerceptualHash`; this file implements it and nothing else. Two parts of it
//! are worth restating here because they are the parts that were wrong before
//! (issue #14, finding 2):
//!
//! * **DC does not participate.** `C(0,0)` is the sum of every sample, so it is
//!   above the threshold for every input that is not exactly black. It used to
//!   occupy the top bit, which made that bit a constant and left the first of
//!   the four 16-bit bands the dedupe index uses with fifteen live bits.
//!   `(0, 8)` is appended in its place so the digest is 64 informative bits
//!   rather than 63 and a pad.
//! * **The step from a file to the 32x32 luma matrix is ours, not the
//!   contract's.** No two imaging libraries resample or grey identically --
//!   `image` uses Rec. 709 weights and integer division, Pillow Rec. 601 -- so
//!   digests are comparable only between records written by this crate. That is
//!   why the algorithm name travels with every digest and why the golden
//!   vectors start at the luma matrix rather than at an image file.

use image::{imageops::FilterType, DynamicImage, GrayImage};
use memory_engine_contracts::{PerceptualHash, PerceptualHashAlgorithm};

/// The luma matrix is this many samples on a side.
const SIDE: usize = 32;
/// The low-frequency block is this many coefficients on a side.
const BLOCK: usize = 8;
/// The appended coefficient's vertical frequency: the next pure vertical
/// frequency after the block's first row, taking the place DC vacated.
const APPENDED: (usize, usize) = (0, BLOCK);
const BITS: usize = BLOCK * BLOCK;

/// `(u, v)` in bit order, most significant bit first: row-major over the 8x8
/// block with `(0, 0)` omitted, then `(0, 8)`.
fn coefficient_order() -> [(usize, usize); BITS] {
    let mut order = [(0_usize, 0_usize); BITS];
    let mut index = 0;
    for u in 0..BLOCK {
        for v in 0..BLOCK {
            if (u, v) == (0, 0) {
                continue;
            }
            order[index] = (u, v);
            index += 1;
        }
    }
    order[index] = APPENDED;
    order
}

/// Unnormalised DCT-II coefficient. `u` pairs with `x`, the COLUMN index.
///
/// No normalisation constant appears because the digest only ever compares
/// coefficients against each other, and any positive scale cancels. Summation
/// order is fixed (x outer, y inner) so the result is reproducible; Rust does
/// not reassociate floating-point arithmetic.
fn coefficient(gray: &GrayImage, u: usize, v: usize) -> f64 {
    let mut sum = 0.0;
    for x in 0..SIDE {
        let cos_x =
            ((2 * x + 1) as f64 * u as f64 * std::f64::consts::PI / (2 * SIDE) as f64).cos();
        for y in 0..SIDE {
            let cos_y =
                ((2 * y + 1) as f64 * v as f64 * std::f64::consts::PI / (2 * SIDE) as f64).cos();
            sum += f64::from(gray.get_pixel(x as u32, y as u32)[0]) * cos_x * cos_y;
        }
    }
    sum
}

/// The 64 hashed coefficients, in bit order.
fn hashed_coefficients(gray: &GrayImage) -> [f64; BITS] {
    let mut values = [0.0_f64; BITS];
    for (slot, (u, v)) in coefficient_order().into_iter().enumerate() {
        values[slot] = coefficient(gray, u, v);
    }
    values
}

/// The 64-bit word, given the coefficients already in bit order.
fn digest_of(values: &[f64; BITS]) -> u64 {
    let mut sorted = *values;
    sorted.sort_by(f64::total_cmp);
    let threshold = sorted[BITS / 2];
    let mut bits = 0_u64;
    for (index, value) in values.iter().enumerate() {
        // STRICTLY greater. With the threshold drawn from the tuple itself, at
        // most 31 bits can be set, which is an invariant a fabricated digest
        // fails.
        if *value > threshold {
            bits |= 1_u64 << (BITS - 1 - index);
        }
    }
    bits
}

pub(crate) fn dct_64(image: &DynamicImage) -> PerceptualHash {
    let gray = image
        .resize_exact(SIDE as u32, SIDE as u32, FilterType::Lanczos3)
        .to_luma8();
    let bits = digest_of(&hashed_coefficients(&gray));

    PerceptualHash {
        algorithm: PerceptualHashAlgorithm::PhashDct64V2,
        bits: 64,
        hex: format!("{bits:016x}"),
    }
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use image::{DynamicImage, GrayImage, RgbImage};
    use serde::Deserialize;

    use super::*;

    #[derive(Deserialize)]
    struct VectorFile {
        algorithm: String,
        vectors: Vec<Vector>,
    }

    #[derive(Deserialize)]
    struct Vector {
        name: String,
        luma_rows_hex: Vec<String>,
        phash_dct_64_v2_hex: String,
        popcount: u32,
    }

    fn load_vectors() -> VectorFile {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../contracts/vectors/phash-dct-64-v2.json");
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()));
        serde_json::from_str(&text).expect("vector file is not the shape this test expects")
    }

    fn gray_from_rows(rows: &[String]) -> GrayImage {
        assert_eq!(rows.len(), SIDE, "a vector must carry {SIDE} rows");
        let mut image = GrayImage::new(SIDE as u32, SIDE as u32);
        for (y, row) in rows.iter().enumerate() {
            assert_eq!(row.len(), SIDE * 2, "a row must carry {SIDE} bytes");
            for x in 0..SIDE {
                let byte = u8::from_str_radix(&row[x * 2..x * 2 + 2], 16).expect("hex byte");
                image.put_pixel(x as u32, y as u32, image::Luma([byte]));
            }
        }
        image
    }

    fn lcg(state: &mut u64) -> u8 {
        *state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (*state >> 33) as u8
    }

    /// The cross-language check. The vectors were produced by an independent
    /// Python implementation written from the schema text, and
    /// `contracts/tests` recomputes them there; this recomputes them here. One
    /// implementation can only ever show that it agrees with itself.
    #[test]
    fn every_golden_vector_reproduces() {
        let file = load_vectors();
        assert_eq!(file.algorithm, "phash-dct-64-v2");
        assert!(file.vectors.len() >= 6, "the vector table lost entries");
        for vector in &file.vectors {
            let gray = gray_from_rows(&vector.luma_rows_hex);
            let bits = digest_of(&hashed_coefficients(&gray));
            assert_eq!(
                format!("{bits:016x}"),
                vector.phash_dct_64_v2_hex,
                "vector {} disagrees",
                vector.name
            );
            assert_eq!(
                bits.count_ones(),
                vector.popcount,
                "vector {} has the wrong popcount",
                vector.name
            );
        }
    }

    /// Not more than 31, because the threshold comes from the tuple and the
    /// comparison is strict. Asserted over the corpus rather than argued.
    #[test]
    fn no_digest_can_set_more_than_thirty_one_bits() {
        let mut state = 99_u64;
        for _ in 0..64 {
            let image = DynamicImage::ImageRgb8(RgbImage::from_fn(64, 64, |_, _| {
                image::Rgb([lcg(&mut state), lcg(&mut state), lcg(&mut state)])
            }));
            let hex = dct_64(&image).hex;
            let bits = u64::from_str_radix(&hex, 16).unwrap();
            assert!(
                bits.count_ones() <= 31,
                "{hex} sets {} bits",
                bits.count_ones()
            );
        }
    }

    /// The regression for issue #14, finding 2. Before the fix the top bit was
    /// 1 for 27 of these 28 inputs -- everything except the exactly black frame
    /// -- while every other position varied. This asserts that no position is
    /// now stuck, which is the property that was violated rather than the
    /// single bit that violated it.
    #[test]
    fn no_bit_position_is_stuck_across_a_varied_corpus() {
        let mut images: Vec<DynamicImage> = Vec::new();
        for seed in 0..24_u64 {
            let mut state = seed.wrapping_mul(2654435761).wrapping_add(1);
            images.push(DynamicImage::ImageRgb8(RgbImage::from_fn(
                96,
                96,
                |_, _| image::Rgb([lcg(&mut state), lcg(&mut state), lcg(&mut state)]),
            )));
        }
        images.push(DynamicImage::ImageLuma8(GrayImage::from_fn(
            96,
            96,
            |x, y| image::Luma([((x * 3 + y * 7) % 251) as u8]),
        )));
        images.push(DynamicImage::ImageLuma8(GrayImage::from_fn(
            96,
            96,
            |x, y| image::Luma([((x * x + y) % 199) as u8]),
        )));
        images.push(DynamicImage::ImageLuma8(GrayImage::from_fn(
            96,
            96,
            |x, y| image::Luma([(((x % 17) * (y % 13)) % 240) as u8]),
        )));
        images.push(DynamicImage::ImageLuma8(GrayImage::from_fn(
            96,
            96,
            |x, y| image::Luma([((x.pow(2) + y.pow(3)) % 233) as u8]),
        )));

        let mut ever_one = 0_u64;
        let mut ever_zero = 0_u64;
        for image in &images {
            let bits = u64::from_str_radix(&dct_64(image).hex, 16).unwrap();
            ever_one |= bits;
            ever_zero |= !bits;
        }
        let stuck = !(ever_one & ever_zero);
        assert_eq!(
            stuck,
            0,
            "bit positions {stuck:016x} never vary over {} images",
            images.len()
        );
    }

    /// A constant added to every sample moves only `C(0,0)`, which is not in
    /// the tuple. This is the property the old encoding could not have: with DC
    /// in the digest it still held, but only because DC was above the threshold
    /// in both cases -- i.e. because the bit was dead.
    #[test]
    fn brightness_offset_does_not_change_the_digest() {
        let mut state = 4242_u64;
        let base = GrayImage::from_fn(SIDE as u32, SIDE as u32, |_, _| {
            image::Luma([lcg(&mut state) % 128])
        });
        let lifted = GrayImage::from_fn(SIDE as u32, SIDE as u32, |x, y| {
            image::Luma([base.get_pixel(x, y)[0] + 64])
        });
        assert_eq!(
            digest_of(&hashed_coefficients(&base)),
            digest_of(&hashed_coefficients(&lifted))
        );
    }

    /// KNOWN DEFECT, pinned rather than hidden: a structureless frame gets a
    /// digest made of floating-point residue.
    ///
    /// Every hashed coefficient of a flat field is mathematically zero, so the
    /// threshold is drawn from the rounding cloud and all 64 bits are decided
    /// by summation order rather than by the picture. Measured here: a flat
    /// field's largest hashed coefficient is around 9e-11 while the smallest
    /// coefficient in any committed golden vector is 0.77, and flat fields at
    /// luma 1, 17, 64, 128, 200 and 255 produce four distinct digests sitting
    /// 27 to 36 bits apart -- while 1, 64 and 128 collide exactly. The
    /// superseded `phash-dct-64` had the same property; dropping DC neither
    /// caused it nor cured it.
    ///
    /// Not fixed here because the fix is a behaviour change with its own
    /// decision to make -- most likely writing no `image_hash` at all, which
    /// dedupe already handles, rather than inventing a structure threshold --
    /// and issue #14 is about the constant bit. Tracked separately.
    ///
    /// What is asserted is the diagnosis, not the digests: the digests are not
    /// portable and must never be pinned. The 1e-6 bound is nine orders of
    /// magnitude above the residue actually measured and six below the smallest
    /// coefficient any real input has produced here, so it separates the two
    /// populations without being tuned to either edge.
    #[test]
    fn a_flat_field_hashes_only_rounding_residue() {
        const RESIDUE_BOUND: f64 = 1e-6;
        for value in [1_u8, 17, 64, 128, 200, 255] {
            let gray = GrayImage::from_fn(SIDE as u32, SIDE as u32, |_, _| image::Luma([value]));
            let largest = hashed_coefficients(&gray)
                .into_iter()
                .fold(0.0_f64, |accumulator, coefficient| {
                    accumulator.max(coefficient.abs())
                });
            assert!(
                largest < RESIDUE_BOUND,
                "flat field at {value} produced a coefficient of {largest:e}, which is \
                 real signal rather than residue -- the diagnosis has changed"
            );
        }
        let black = GrayImage::from_fn(SIDE as u32, SIDE as u32, |_, _| image::Luma([0]));
        assert_eq!(
            digest_of(&hashed_coefficients(&black)),
            0,
            "an exactly black frame multiplies to exact zeros, so nothing is \
             strictly greater than the threshold"
        );
    }

    #[test]
    fn hash_has_contract_length_and_is_deterministic() {
        let image = DynamicImage::ImageLuma8(GrayImage::from_fn(64, 64, |x, y| {
            image::Luma([((x + y) % 255) as u8])
        }));
        let one = dct_64(&image);
        let two = dct_64(&image);
        assert_eq!(one, two);
        assert_eq!(one.algorithm, PerceptualHashAlgorithm::PhashDct64V2);
        assert_eq!(one.hex.len(), 16);
        assert!(one
            .hex
            .chars()
            .all(|character| character.is_ascii_hexdigit()));
    }

    #[test]
    fn the_coefficient_order_omits_dc_and_appends_the_next_vertical_frequency() {
        let order = coefficient_order();
        assert_eq!(order.len(), 64);
        assert!(!order.contains(&(0, 0)), "DC must not be hashed");
        assert_eq!(order[0], (0, 1), "bit 63 is the block's first non-DC entry");
        assert_eq!(order[62], (7, 7));
        assert_eq!(order[63], APPENDED, "bit 0 is the appended coefficient");
        let mut seen = order.to_vec();
        seen.sort_unstable();
        seen.dedup();
        assert_eq!(seen.len(), 64, "a coefficient is hashed twice");
    }
}
