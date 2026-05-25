# Astrophoto Upload Folder

Use this folder for astrophotography images for the site gallery.

## Recommended structure

- `astrophoto/full/` - Full resolution display images (WebP preferred)
- `astrophoto/thumbs/` - Thumbnails for fast gallery loading
- `astrophoto/manifest.json` - Metadata used by the gallery page

## Suggested naming

Use sortable filenames:

`YYYY-MM-DD_target_filter_exposure.ext`

Example:

`2026-05-20_m51_lrgb_180s.webp`

## Suggested manifest format

```json
[
  {
    "title": "M51 Whirlpool Galaxy",
    "date": "2026-05-20",
    "target": "M51",
    "camera": "ASI533MC",
    "telescope": "72ED",
    "integration": "2h 15m",
    "full": "astrophoto/full/2026-05-20_m51_lrgb_180s.webp",
    "thumb": "astrophoto/thumbs/2026-05-20_m51_lrgb_180s.webp",
    "description": "Clear sky from Whitegate. LRGB stack."
  }
]
```

## Display pattern recommendation

For your volume (<200 files), use a static JSON-driven gallery:

1. Responsive card grid (3 columns desktop, 2 tablet, 1 mobile)
2. Lazy-loaded thumbnails for speed
3. Click thumbnail to open a lightbox with full image and metadata
4. Optional filters by `target` and `date`

This stays simple, fast, and easy to maintain on GitHub Pages.
