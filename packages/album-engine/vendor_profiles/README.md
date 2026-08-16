# Vendor profiles

Physical spec sheets, transcribed. `AlbumSpec.vendor_profile` embeds one of these, and `workers/render-print` validates every export against it as a hard gate.

Two ship by default. **Neither is a real vendor.** They are defensible industry defaults chosen so the layout solver and the print validator can be built and tested now; the build plan is explicit that vendor #1 must be a real spec sheet, and these are what stands in until one is signed.

| | `layflat-300-square` | `perfectbound-210-square` |
|---|---|---|
| Trim | 300 × 300 mm | 210 × 210 mm |
| Binding | Layflat | Perfect bound |
| Gutter | **5 mm** | **14 mm** |
| Safe margin | 8 mm | 10 mm |
| Page count | 20–100, step 2 | 24–120, step 4 |

Two rather than one on purpose: the vendor-profile abstraction is only proven if two profiles genuinely disagree. The gutter is where they disagree most, and it is the difference that actually reaches a user — a face 8 mm from the spine is fine layflat and swallowed by a perfect-bound book.

## Which numbers are conventions, and which are guesses

Honesty about this matters, because the print validator will happily enforce a wrong number with total confidence.

**Real industry conventions — safe to build against:**

- **3 mm bleed.** Near-universal outside the US (where 0.125″ ≈ 3.175 mm is the equivalent).
- **300 DPI floor, 350 preferred.** The standard commercial-print floor. This is the number the whole `effective_dpi` mechanism exists to police.
- **PDF/X-4.** Current standard for transparency and ICC-aware print submission.
- **Page counts in increments.** Books are bound in physical sheets, so counts are always constrained to a step. 2 for layflat, 4 for perfect bound is typical.
- **Layflat has a much smaller gutter loss than perfect bound.** The direction is real and structural, since a layflat spread opens completely.

**Plausible but invented — will change with a real vendor:**

- Every **exact millimetre**: 5 vs 14 mm gutter, 8 vs 10 mm safe margin, the 14 mm and 10 mm spines. Correct in shape, not to the millimetre.
- **Trim sizes.** 300 mm and 210 mm square are common, but vendors have their own catalogues and rarely offer exactly these.
- **Page-count limits.** 20–100 and 24–120 are ordinary, not derived from anything.
- **FOGRA39.** A reasonable European coated-stock default. A US vendor is likelier to want GRACoL 2006; an Indian printer may specify something else again, or nothing, in which case we choose.
- **Paper stock strings.** Descriptive only, no effect on validation.
- **`icc_hash: null`** — we do not yet ship the actual ICC profile. `color_profile_match` cannot be genuinely enforced until we do; today it checks the name, which is weaker than it looks.

## What must happen when a real vendor is signed

1. Replace the numbers from the vendor's spec sheet, and set `vendor_id` / `product_id` to theirs.
2. Obtain the real ICC profile, ship it, and fill `icc_hash`. Until then `color_profile_match` is a name comparison, not a colour guarantee.
3. Bump `profile_version` — a spec validated against `2026.1-default` is not automatically valid against theirs, and the version pin is what makes that detectable.
4. Send a physical test print through the whole pipeline. The build plan's Phase 2 exit gate is a real print run, and no amount of validator passing substitutes for it.

## Adding a profile

Drop a JSON file here matching `VendorProfile` in `contracts/schemas/album-spec.schema.json`. `tests/test_vendor_profiles.py` validates every file in this directory against the schema and checks the internal geometry, so a malformed profile fails before it can reach a print job.
