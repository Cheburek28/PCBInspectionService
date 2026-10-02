# The `classic-diff` engine

Source: [`src/pcb_inspection/engine/classic/`](../src/pcb_inspection/engine/classic/). Version is stored with
every inspection (`algorithm.version`).

## Pipeline

1. **Decode** with EXIF orientation applied; resize to the *working width* (`PCBIS_ENGINE_WORK_WIDTH`,
   3000 px; never upscaled beyond the reference's own width). The test photo is resized to the reference's
   working width.
2. **Mask** (once, when the reference is created): which part of the frame is inspected.
   - `full_frame` — everything except a 6 px margin.
   - `blue_fixture` — the board island surrounded by blue slots of the holding fixture: blue pixels are
     closed with growing kernels until an island that does not touch the frame edge separates.
   - `polygon` — client-supplied polygon in uploaded-reference pixels.
3. **Global alignment**: SIFT (6000 features), Lowe ratio 0.75, RANSAC homography (3 px). The homography
   must be plausible (scale 0.8–1.25, no mirroring, weak perspective), otherwise `ALIGNMENT_FAILED`.
4. **Refinement**: ECC affine at half resolution (sub-pixel), then dense Farneback optical flow smoothed
   with σ = 12 px — removes residual parallax without "explaining away" real defects.
5. **Quality**: Lab shift of the aligned photo vs. reference inside the mask; sharpness ratio (mean |Laplacian|)
   measured on the *unwarped* photo, only where photo and reference agree (differing regions excluded so
   that a board with many missing parts is not called blurry).
6. **Colour normalisation**: per-channel Lab mean/std matched to the reference inside the mask.
7. **Difference map**: per-pixel Lab distance between value ranges in a (2·tol_px+1) neighbourhood of
   both images (two-sided), chroma weighted ×1.5, smoothed with σ = 4 px (a patch must differ as a whole).
8. **Regions**: threshold → morphological closing (9 px) → connected components ≥ `min_area` working
   pixels → `score` = peak of the map in the region. Sorted by score.
9. **Coordinates**: regions are mapped to uploaded-reference pixels (`bbox_ref`) and through the inverse of
   homography ∘ ECC into uploaded-test pixels (`bbox_test`, bounding box of the projected corners).
   Optical flow is not inverted (it is smooth and small).

If the photo *is* the reference (mean absolute difference < 1 after alignment) no regions are reported.

## Parameters

| Parameter | Default | Per request | Effect |
|---|---|---|---|
| `threshold` | 12.0 | yes | difference score needed to start a region. Higher → fewer, stronger regions |
| `min_area` | 40 | yes | minimum region size in working pixels |
| `tol_px` | 2 | yes | tolerated misregistration; higher hides small shifts (and small defects) |
| `work_width` | 3000 | no | resolution of the analysis; `min_area` and `tol_px` are in these pixels |

Quality gates: see [errors.md](errors.md#rejection-codes-inspectionrejectioncode-status-rejected).

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

- Single reference per side; multi-reference pools (minimum over several good boards) are planned.
- Assumes a rigid, roughly planar board photographed from a similar viewpoint.
- Defects smaller than `tol_px` shifts or below `min_area` are not reported.
- Colour normalisation compensates exposure and white balance, not strong local reflections.
