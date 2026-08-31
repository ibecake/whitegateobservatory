# Astrophoto Gallery

Gallery metadata for the Astro Photography page (`astro-photography.html`).

## Where the images live

Image binaries are **not** stored in this repo. They live in the public Google
Cloud Storage bucket `whitegate-astrophotography` and are served directly to the
browser from:

`https://storage.googleapis.com/whitegate-astrophotography/<object>`

The bucket grants public read (`allUsers` -> Storage Object Viewer). Only
`manifest.json` (the metadata index) is tracked here.

Use [admin.html](../admin.html) to upload images, apply tags, read EXIF metadata,
and update this file. You can still edit `manifest.json` by hand if you prefer.

## Adding a new image

1. Open `admin.html`, choose **Astrophotography**, and drop the image (or upload
   it to the `whitegate-astrophotography` bucket with `gcloud storage cp`).
2. Review the auto-filled date/camera/tags from file metadata, then download or
   commit the updated `manifest.json`.

The `full` and `thumb` fields are the **object path within the bucket** (for an
object at the bucket root, that is just the file name). The admin also writes
`file`, `type`, and `tags`.

## Manifest format

```json
{
  "version": 1,
  "collection": "astrophotography",
  "tags": ["aurora"],
  "items": [
    {
      "type": "image",
      "title": "Aurora Borealis - East Cork",
      "date": "2025-08-12",
      "target": "Aurora Borealis",
      "tags": ["aurora"],
      "camera": "iPhone",
      "telescope": "Unknown",
      "integration": "Unknown",
      "file": "IMG_1959.jpg",
      "full": "IMG_1959.jpg",
      "thumb": "IMG_1959.jpg",
      "description": "Captured from Whitegate Observatory."
    }
  ]
}
```

A legacy JSON array of items is still accepted by the gallery.

Browser uploads to GCS from `admin.html` need CORS on the bucket. An example
policy is in [`assets/data/gcs-cors.example.json`](../assets/data/gcs-cors.example.json).

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
- Filters by `target`, year, and `tags`.
