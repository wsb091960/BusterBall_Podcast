#!/usr/bin/env python3
"""Convert a Buster Ball Podcast Markdown report into WAV narration only."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from buster_ball_report_to_mp4 import (
    AI_DISCLOSURE,
    DEFAULT_VOICE_INSTRUCTIONS,
    clean_report,
    create_speech,
    fail,
    join_audio,
    split_for_speech,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a Buster Ball scouting report into WAV narration."
    )
    parser.add_argument("report", type=Path, help="Scouting report (.txt or .md)")
    parser.add_argument("-o", "--output", type=Path, help="Output WAV path")
    parser.add_argument("--voice", default="cedar", help="OpenAI narration voice")
    parser.add_argument("--model", default="gpt-4o-mini-tts", help="Speech model")
    parser.add_argument(
        "--instructions",
        default=DEFAULT_VOICE_INSTRUCTIONS,
        help="Narration style instructions",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace existing WAV")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if shutil.which("ffmpeg") is None:
        fail("FFmpeg must be installed and available on PATH.")
    if not args.report.is_file():
        fail(f"Report not found: {args.report}")
    if args.report.suffix.lower() not in {".txt", ".md", ".markdown"}:
        fail("The scouting report must be a .txt, .md, or .markdown file.")

    output = (args.output or args.report.with_suffix(".wav")).resolve()
    if output.suffix.lower() != ".wav":
        output = output.with_suffix(".wav")
    if output.exists() and not args.overwrite:
        fail(f"Output already exists: {output} (use --overwrite to replace it)")
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        raw = args.report.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        fail("The report must be saved as a UTF-8 text file.")
    cleaned = clean_report(raw)
    if not cleaned:
        fail("The scouting report is empty after formatting was removed.")

    chunks = split_for_speech(f"{AI_DISCLOSURE}\n\n{cleaned}")
    with tempfile.TemporaryDirectory(prefix="buster_ball_") as temp_name:
        temp_dir = Path(temp_name)
        speech_files = create_speech(
            chunks, temp_dir, args.model, args.voice, args.instructions
        )
        join_audio(speech_files, output, temp_dir)

    print(f"Created: {output}")


if __name__ == "__main__":
    main()
