use image::{imageops::FilterType, DynamicImage};
use memory_engine_contracts::{PerceptualHash, PerceptualHashAlgorithm};

pub(crate) fn dct_64(image: &DynamicImage) -> PerceptualHash {
    const SIDE: usize = 32;
    const LOW: usize = 8;
    let gray = image
        .resize_exact(SIDE as u32, SIDE as u32, FilterType::Lanczos3)
        .to_luma8();
    let mut low = [[0.0_f64; LOW]; LOW];

    for (u, row) in low.iter_mut().enumerate() {
        for (v, coefficient) in row.iter_mut().enumerate() {
            let mut sum = 0.0;
            for x in 0..SIDE {
                for y in 0..SIDE {
                    let pixel = f64::from(gray.get_pixel(x as u32, y as u32)[0]);
                    let cos_x = ((2 * x + 1) as f64 * u as f64 * std::f64::consts::PI
                        / (2 * SIDE) as f64)
                        .cos();
                    let cos_y = ((2 * y + 1) as f64 * v as f64 * std::f64::consts::PI
                        / (2 * SIDE) as f64)
                        .cos();
                    sum += pixel * cos_x * cos_y;
                }
            }
            *coefficient = sum;
        }
    }

    let mut values: Vec<f64> = low.iter().flatten().copied().skip(1).collect();
    values.sort_by(f64::total_cmp);
    let median = values[values.len() / 2];
    let mut bits = 0_u64;
    for (index, value) in low.iter().flatten().enumerate() {
        if *value > median {
            bits |= 1_u64 << (63 - index);
        }
    }

    PerceptualHash {
        algorithm: PerceptualHashAlgorithm::PhashDct64,
        bits: 64,
        hex: format!("{bits:016x}"),
    }
}

#[cfg(test)]
mod tests {
    use image::{DynamicImage, GrayImage};

    use super::*;

    #[test]
    fn hash_has_contract_length_and_is_deterministic() {
        let image = DynamicImage::ImageLuma8(GrayImage::from_fn(64, 64, |x, y| {
            image::Luma([((x + y) % 255) as u8])
        }));
        let one = dct_64(&image);
        let two = dct_64(&image);
        assert_eq!(one, two);
        assert_eq!(one.hex.len(), 16);
        assert!(one
            .hex
            .chars()
            .all(|character| character.is_ascii_hexdigit()));
    }
}
