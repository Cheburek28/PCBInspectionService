# The `classic-diff` engine

Source: [`src/pcb_inspection/engine/classic/`](../src/pcb_inspection/engine/classic/). Version is stored with
every inspection (`algorithm.version`).

## Pipeline

1. **Decode** with EXIF orientation applied; resize to the *working width* (`PCBIS_ENGINE_WORK_WIDTH`,
   3000 px; never upscaled beyond the reference's own width). The test photo is resized to the reference's
   working width.
2. **Mask** (once, when the reference is created): which part of the frame is inspected.
   - `full_frame` — everything except a 6 px margin.
   - `blue_fixture` — the board island surrounded by blue slots of the holding fixture. Slot pixels are a
     tight blue range (hue 110–135, saturated) kept only as large blobs, so blue-green solder mask is not
     mistaken for a slot. Bridges between slots are closed with growing kernels until an island separates;
     the island is then grown back to the inner slot edges (bounded by the convex hull of the surrounding
     slots), so corners are not cut off and the mask is the same from photo to photo.
   - `polygon` — client-supplied polygon in uploaded-reference pixels.
3. **Board-anchored alignment**: SIFT keypoints of the reference are taken only on the board (mask dilated
   by 60 px), so the fixture seen through the slots and the panel — which may shift relative to the board —
   do not drive the fit. Lowe ratio 0.75, RANSAC homography (3 px), plausibility check (scale 0.8–1.25,
   no mirroring, weak perspective), otherwise `ALIGNMENT_FAILED`.
4. **Refinement**: ECC affine at half resolution (sub-pixel), then dense Farneback optical flow smoothed
   with σ = 12 px — removes residual parallax without "explaining away" real defects.
5. **Quality**: Lab shift of the aligned photo vs. reference inside the mask; sharpness ratio (mean |Laplacian|)
   on the *unwarped* photo, only where photo and reference agree.
6. **Colour normalisation**: per-channel Lab mean/std matched to the reference inside the mask.
7. **Board-to-board variation filter** (both images, lightness only):
   - `highlight_clip` (100): lightness above this counts as equal — shiny vs. matte solder and lead glare;
   - `open_radius` (4 px): morphological opening removes thin bright strokes — part markings and lot codes;
   - `background_sigma` (20 px): the local mean lightness is subtracted — a slightly lighter component body
     or uneven light does not count.
   Shape and colour changes — a missing, shifted, rotated or wrong part, a bridge on green solder mask — survive.
8. **Difference map**: per-pixel Lab distance between value ranges in a (2·tol_px+1) neighbourhood of
   both images (two-sided), chroma weighted ×1.5, smoothed with σ = 4 px (a patch must differ as a whole).
9. **Regions with hysteresis**: a region's outline and area are where the map exceeds `extent_threshold` (12);
   it is reported if its peak reaches `threshold` (20) and its area is at least `min_area` working pixels.
10. **Coordinates**: regions are mapped to uploaded-reference pixels (`bbox_ref`) and through the inverse of
   homography ∘ ECC into uploaded-test pixels (`bbox_test`). Optical flow is not inverted (smooth and small).

If the photo *is* the reference (mean absolute difference < 1 after alignment) no regions are reported.

## Parameters

| Parameter | Default | Per request | Effect |
|---|---|---|---|
| `threshold` | 20.0 | yes | peak difference needed to report a region. Higher → fewer, stronger regions |
| `extent_threshold` | 12.0 | yes | outline/area of a region; lower → larger regions |
| `min_area` | 40 | yes | minimum region size in working pixels |
| `tol_px` | 2 | yes | tolerated misregistration; higher hides small shifts (and small defects) |
| `highlight_clip` | 100 | yes | glare suppression (8-bit Lab L); 255 = off |
| `open_radius` | 4 | yes | marking suppression, working px; 0 = off |
| `background_sigma` | 20 | yes | local tone suppression, working px; 0 = off |
| `work_width` | 3000 | no | resolution of the analysis; pixel parameters are in these pixels |

Quality gates: see [errors.md](errors.md#rejection-codes-inspectionrejectioncode-status-rejected).

### Effect of the variation filter (version 0.2.0)

Measured on a private set of production photos (one product, both sides, 6000×4000, `blue_fixture`, a single
reference, 10 known-good boards and boards with known defects; two different references):

| | engine 0.1.0 (threshold 12) | engine 0.2.0 defaults |
|---|---|---|
| regions on a good board (mean) | 76–135 | 2.8–3.4 |
| a rotated 0402 resistor | rank 26–35 among ~110 regions | rank 1–2 among 1–2 regions |

Trade-off: glare suppression lowers the score of small *bright* defects (a solder blob) by ~20 %, and markings
with strokes thicker than the opening diameter (~9 px) are only partly suppressed.

## Performance

≈ 2 s per side at 3000×2000 on a 20-core workstation, 5–10 s expected on a 4 vCPU VPS. Peak memory
≈ 1 GB per job. The prepared reference (resized image, mask, SIFT keypoints) is cached per worker process.

## Calibrating for your boards

With a single reference, normal board-to-board variation (solder sheen, via colour, printing) shows up
as differences. Before going to production:

1. Collect ≥ 20 photos of good boards and all known defective boards of the product.
2. `pcbis bench reference.jpg good/*.jpg bad/*.jpg` — check differences on good boards and whether all
   real defects are found.
3. Raise `threshold` / `min_area` until good boards are (almost) clean while defects remain; set
   `gate_max_differences` comfortably above what good boards produce.
4. In production, use the exported feedback (`pcbis export`, `metrics.json`) to track precision and
   recall and re-tune.

## Limitations

- Single reference per side: normal variation is handled by the filter in step 7, not learned.
- Assumes a rigid, roughly planar board photographed from a similar viewpoint.
- Defects smaller than `tol_px` shifts or below `min_area` are not reported.
- Colour normalisation compensates exposure and white balance, not strong local reflections.
