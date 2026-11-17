import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import media_storage as storage


class Missing(Exception):
    response = {"Error": {"Code": "404"}}


class FakeR2:
    def __init__(self):
        self.objects = {}
        self.uploads = 0

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        data, extra = self.objects[Key]
        return {"ContentLength": len(data), "Metadata": extra["Metadata"]}

    def upload_fileobj(self, source, bucket, key, ExtraArgs):
        self.uploads += 1
        self.objects[key] = (source.read(), ExtraArgs)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key][0])}


class MediaStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.media = self.directory / "Episode 1.wav"
        self.media.write_bytes(b"RIFF media sample")
        self.records = storage.inventory([self.media])

    def tearDown(self):
        self.temp.cleanup()

    def test_content_addressed_key_deduplicates_rename(self):
        renamed = self.directory / "Renamed.wav"
        renamed.write_bytes(self.media.read_bytes())
        self.assertEqual(self.records[0]["object_key"], storage.inventory([renamed])[0]["object_key"])
        self.assertEqual(self.records[0]["content_type"], "audio/wav")
        self.assertEqual(len(storage.inventory([self.media, self.media])), 1)

    def test_upload_verifies_bytes_and_rerun_skips_upload(self):
        client = FakeR2()
        first = storage.upload_records(client, "private-media", self.records)
        second = storage.upload_records(client, "private-media", self.records)
        self.assertEqual(first[0]["status"], "uploaded")
        self.assertEqual(second[0]["status"], "already_present")
        self.assertEqual(client.uploads, 1)
        self.assertTrue(first[0]["verified_sha256"])
        self.assertNotIn("ACL", client.objects[self.records[0]["object_key"]][1])

    def test_metadata_conflict_does_not_overwrite(self):
        client = FakeR2()
        client.objects[self.records[0]["object_key"]] = (b"wrong", {"Metadata": {"sha256": "bad"}})
        with self.assertRaisesRegex(ValueError, "metadata conflict"):
            storage.upload_records(client, "private-media", self.records)
        self.assertEqual(client.uploads, 0)

    def test_permission_errors_are_not_treated_as_missing(self):
        class Forbidden(FakeR2):
            def head_object(self, **kw):
                error = Missing()
                error.response = {"Error": {"Code": "403"}}
                raise error
        client = Forbidden()
        with self.assertRaises(Missing):
            storage.upload_records(client, "private-media", self.records)
        self.assertEqual(client.uploads, 0)

    def test_checksum_detects_corrupt_remote_even_with_correct_metadata(self):
        client = FakeR2()
        record = self.records[0]
        client.objects[record["object_key"]] = (b"0" * record["size_bytes"], {"Metadata": {"sha256": record["sha256"]}})
        with self.assertRaisesRegex(ValueError, "checksum"):
            storage.upload_records(client, "private-media", self.records)

    def test_changed_source_is_not_uploaded(self):
        self.media.write_bytes(b"changed")
        client = FakeR2()
        with self.assertRaisesRegex(ValueError, "Source changed"):
            storage.upload_records(client, "private-media", self.records)
        self.assertEqual(client.uploads, 0)

    def test_dry_run_needs_no_credentials_and_leaves_media(self):
        manifest = self.directory / "inventory.json"
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(storage.main([str(self.media), "--dry-run", "--manifest", str(manifest)]), 0)
        payload = json.loads(manifest.read_text())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["files"][0]["sha256"], hashlib.sha256(self.media.read_bytes()).hexdigest())
        self.assertTrue(self.media.exists())

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            storage.inventory([self.media], "../unsafe")
        text = self.directory / "report.md"
        text.write_text("keep me")
        with self.assertRaises(ValueError):
            storage.inventory([text])
        link = self.directory / "link.wav"
        link.symlink_to(self.media)
        with self.assertRaises(ValueError):
            storage.inventory([link])
        with self.assertRaisesRegex(ValueError, "Missing configuration"):
            storage.configured_client({})


if __name__ == "__main__":
    unittest.main()
