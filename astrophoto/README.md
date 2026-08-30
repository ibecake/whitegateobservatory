# Astrophoto Gallery

Gallery metadata for the Astro Photography page (`astro-photography.html`).

## Where the images live

Image binaries are **not** stored in this repo. They live in the public Google
Cloud Storage bucket `whitegate-astrophotography` and are served directly to the
browser from:

`https://storage.googleapis.com/whitegate-astrophotography/<object>`

The bucket grants public read (`allUsers` -> Storage Object Viewer). Only
`manifest.json` (the metadata index) is tracked here.

## Adding a new image

1. Upload the image file to the `whitegate-astrophotography` bucket (a plain
   web-friendly format such as `.jpg`, `.png`, or `.webp` — browsers do not
   render `.heic`).
2. Add an entry to `manifest.json`. The `full` and `thumb` fields are the
   **object path within the bucket** (for an object at the bucket root, that is
   just the file name).

## Manifest format

```json
[
  {
    "title": "Aurora Borealis - East Cork",
    "date": "2025-08-12",
    "target": "Aurora Borealis",
    "camera": "iPhone",
    "telescope": "Unknown",
    "integration": "Unknown",
    "full": "IMG_1959.jpg",
    "thumb": "IMG_1959.jpg",
    "description": "Captured from Whitegate Observatory."
  }
]
```

Notes:

- Paths are resolved against the bucket base URL configured as
  `GALLERY_IMAGE_BASE` in `astro-photography.html`. If a `full`/`thumb` value is
  already an absolute URL (`https://...`), it is used as-is, so you can also mix
  in images hosted elsewhere.
- If you upload thumbnails as separate objects, point `thumb` at them; otherwise
  reuse the full image (as above).
- CORS is not required — the gallery displays images with plain `<img>` tags.

## Gallery behaviour

- Responsive card grid with lazy-loaded thumbnails.
- Click a thumbnail to open a lightbox with the full image and metadata.
- Filters by `target` and `date`.
