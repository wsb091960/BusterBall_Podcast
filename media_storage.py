#!/usr/bin/env python3
"""Inventory and upload podcast media to a private Cloudflare R2 bucket.

No files are deleted and no bucket permissions are changed. Dry runs need no SDK
or credentials. Object references in the manifest remain valid after URL expiry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MEDIA_TYPES = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".heic": "image/heic",
}
CHUNK_SIZE = 1024 * 1024


def digest_stream(stream):
    digest = hashlib.sha256()
    size = 0
    while block := stream.read(CHUNK_SIZE):
        digest.update(block)
        size += len(block)
    return digest.hexdigest(), size


def validate_prefix(prefix):
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", prefix):
        raise ValueError("Prefix must contain only letters, digits, /, _ and -; no empty segments.")
    return prefix


def inventory(paths, prefix="podcast"):
    prefix = validate_prefix(prefix)
    records = []
    seen = set()
    for supplied in paths:
        supplied = Path(supplied).expanduser()
        if supplied.is_symlink():
            raise ValueError(f"Symlinks are not accepted: {supplied}")
        path = supplied.resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"Not a file: {path}")
        if path in seen:
            continue
        seen.add(path)
        suffix = path.suffix.lower()
        if suffix not in MEDIA_TYPES:
            raise ValueError(f"Unsupported media extension: {path.name}")
        with path.open("rb") as source:
            checksum, size = digest_stream(source)
        records.append({
            "source_path": str(path), "filename": path.name,
            "sha256": checksum, "size_bytes": size,
            "content_type": MEDIA_TYPES[suffix],
            "object_key": f"{prefix}/{checksum[:2]}/{checksum}{suffix}",
        })
    return records


def configured_client(environ=None):
    env = os.environ if environ is None else environ
    names = ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    if not re.fullmatch(r"[0-9a-fA-F]{32}", env["R2_ACCOUNT_ID"]):
        raise ValueError("R2_ACCOUNT_ID must be the 32-character Cloudflare account ID.")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise ValueError("Install storage dependencies: python -m pip install -r requirements-storage.txt") from error
    client = boto3.client(
        "s3", endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=env["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"], region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"},
                      request_checksum_calculation="when_required", response_checksum_validation="when_required"),
    )
    return client, env["R2_BUCKET"]


def is_missing(error):
    response = getattr(error, "response", {})
    return str(response.get("Error", {}).get("Code")) in {"404", "NoSuchKey", "NotFound"}


def verify_remote(client, bucket, record):
    remote = client.get_object(Bucket=bucket, Key=record["object_key"])
    body = remote["Body"]
    try:
        checksum, size = digest_stream(body)
    finally:
        body.close()
    if (checksum, size) != (record["sha256"], record["size_bytes"]):
        raise ValueError(f"Remote checksum verification failed: {record['filename']}")


def upload_records(client, bucket, records):
    results = []
    for record in records:
        try:
            remote = client.head_object(Bucket=bucket, Key=record["object_key"])
        except Exception as error:
            if not is_missing(error):
                raise
            remote = None
        if remote is not None:
            if (remote.get("ContentLength") != record["size_bytes"] or
                    remote.get("Metadata", {}).get("sha256") != record["sha256"]):
                raise ValueError(f"Existing object metadata conflict for {record['filename']}; nothing overwritten.")
            status = "already_present"
        else:
            with Path(record["source_path"]).open("rb") as source:
                # Detect edits between inventory and upload; use the same handle.
                checksum, size = digest_stream(source)
                if (checksum, size) != (record["sha256"], record["size_bytes"]):
                    raise ValueError(f"Source changed since inventory: {record['filename']}")
                source.seek(0)
                client.upload_fileobj(source, bucket, record["object_key"], ExtraArgs={
                    "ContentType": record["content_type"],
                    "Metadata": {"sha256": record["sha256"]},
                })
            status = "uploaded"
        # Full byte checksum, not ETag (multipart ETags are not file checksums).
        verify_remote(client, bucket, record)
        results.append({**record, "provider": "r2", "bucket": bucket,
                        "status": status, "verified_sha256": True})
    return results


def write_manifest(path, records, dry_run=False):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError("Manifest must not be a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
               "dry_run": dry_run, "files": records}
    # Atomic replacement; small manifests can be retained separately from media.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".media-manifest-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="Explicit image/audio/video files to inventory/upload")
    parser.add_argument("--prefix", default="podcast", help="Object key prefix (default: podcast)")
    parser.add_argument("--manifest", type=Path, default=Path("media-manifest.json"))
    parser.add_argument("--dry-run", action="store_true", help="Inventory only; no network or credentials required")
    args = parser.parse_args(argv)
    try:
        records = inventory(args.files, args.prefix)
        if args.manifest.resolve() in {Path(record["source_path"]) for record in records}:
            raise ValueError("Manifest must not overwrite a source media file.")
        if not args.dry_run:
            client, bucket = configured_client()
            records = upload_records(client, bucket, records)
        write_manifest(args.manifest, records, args.dry_run)
    except Exception as error:
        # Never print credentials or signed URLs.
        print(f"Media storage failed: {error}", file=sys.stderr)
        return 1
    print(f"{'Inventoried' if args.dry_run else 'Uploaded and verified'} {len(records)} media file(s). Manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
