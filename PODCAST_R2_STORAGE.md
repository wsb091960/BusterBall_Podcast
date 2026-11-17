# Podcast media storage

This change prepares Buster Ball audio, video, and artwork for Cloudflare R2.
The code does not create a bucket, change public access, delete local media, or
rewrite repository history. Real uploads require the configuration below.

## One-time setup

1. Create a dedicated **private** R2 bucket for podcast masters, for example
   `busterball-media`. Keep public bucket access and the public development URL
   disabled. Keep Golf Coach student media in a separate private bucket.
2. Create R2 S3 credentials scoped to object read/write in the podcast bucket.
3. In this repository's Actions settings, add **variables** `R2_ACCOUNT_ID` and
   `R2_BUCKET`, and **secrets** `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`.
   Enter secrets in the provider's settings, never in source files or chat.
   The existing `OPENAI_API_KEY` remains the narration credential.
4. Install the optional upload dependency locally when needed:

   ```sh
   python -m pip install -r requirements-storage.txt
   ```

## Current workflow change

`build_buster_ball_wav.yml` validates storage configuration before generating
paid narration, writes WAV into the ignored `media-output` directory, uploads
the WAV to R2, and downloads the object to verify its SHA-256 checksum. It retains
only a small JSON media manifest as a GitHub Actions artifact for 30 days. The
manifest holds durable bucket/object references, never credentials or expiring
URLs. It is not an audio download link. It can be downloaded with the run results
and used to locate the media in R2.

Until R2 is configured, the modified workflow stops with a configuration error
before narration. This intentionally avoids silently continuing to consume
GitHub audio-artifact storage. Existing GitHub artifacts are unchanged.

## Upload locally generated audio, video, or artwork

The standalone local-audio script continues to save outside the repository. The
existing top-level generation commands remain usable; use an external output
folder or `media-output` for their generated files. Upload selected finished
files separately:

```sh
python media_storage.py /path/to/episode.wav /path/to/cover.png --dry-run --manifest media-manifest-preview.json
python media_storage.py /path/to/episode.wav /path/to/cover.png --manifest media-manifest.json
```

Export the four R2 settings from a secure local credential mechanism before the
real upload. `--dry-run` uses no network, SDK, or credentials. `--prefix podcast`
is the default. Keys include the SHA-256 digest and media extension; renamed
copies of identical bytes with the same extension use the same object key.
Re-running verifies the existing object instead of uploading another copy.
The uploader rejects existing metadata conflicts, propagates authentication
errors, and verifies actual downloaded bytes rather than trusting multipart
ETags. It never deletes originals, including when an upload fails.

Each manifest contains source path, filename, size, MIME type, checksum, object
key and (after successful upload) bucket and verification state. Retain the
manifest with your episode records. Manifests can contain local paths and should
be shared deliberately. Do not discard local masters until a real restore and
playback test has passed.

## Public podcast episodes

Masters stay private. If public streaming or podcast RSS hosting is needed,
publish approved episodes to a **separate public delivery bucket/domain** and
use stable public episode URLs there. A time-limited signed URL is unsuitable
as a permanent podcast feed enclosure. This change does not enable public
access or modify a podcast feed. Public delivery and browser CORS/range playback
require an explicit configuration and playback test.

## Capacity and migration limits

New generated audio/video and episode artwork are ignored by Git. Existing
tracked artwork is retained for compatibility. Ignoring files does not untrack
existing files, remove old Git blobs, or delete old Actions artifacts. Inventory
those separately; migrate and verify them before any removal. Identify whether
the actual limit is repository/LFS, Actions artifacts, or Codespaces storage.
For this repository, the current WAV workflow specifically uses Actions
artifacts with 30-day retention; replacing that path prevents new large audio
artifacts but does not reclaim old quota automatically.

## Verification

```sh
python -m unittest discover -s tests -v
```

Tests use an in-memory object-store fake and verify content addressing,
duplicate handling, corruption detection, changed-source protection, permission
failures, private-by-default request parameters, and credential-free dry runs.
No live R2 upload, signed download, playback, or recovery has been verified yet.

References: [R2 boto3 example](https://developers.cloudflare.com/r2/examples/aws/boto3/),
[R2 pricing](https://developers.cloudflare.com/r2/pricing/).
