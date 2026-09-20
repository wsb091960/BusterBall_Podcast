#!/usr/bin/env python3
"""Turn a Buster Ball Podcast scouting report into a narrated MP4.

The script accepts a UTF-8 .txt or .md report, creates narration with the
OpenAI Speech API, joins long reports automatically, and builds a 1080p MP4
with either supplied cover art or a generated Buster Ball title card.

Requirements:
    python -m pip install openai pillow
    FFmpeg and ffprobe must be installed and available on PATH.
    Set the OPENAI_API_KEY environment variable before running.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


DEFAULT_VOICE_INSTRUCTIONS = (
    "Deliver this as Billy B, host of the Buster Ball Podcast: confident, "
    "conversational, analytical, and energetic without shouting. Sound like "
    "an experienced baseball broadcaster explaining advanced sabermetrics. "
    "Use natural pauses between sections. Clearly pronounce statistics and "
    "team abbreviations."
)
AI_DISCLOSURE = "This episode of the Buster Ball Podcast uses an AI-generated voice."
VIDEO_SIZE = (1920, 1080)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a Buster Ball scouting-report text file into a narrated MP4."
    )
    parser.add_argument("report", type=Path, help="Scouting report (.txt or .md)")
    parser.add_argument("-o", "--output", type=Path, help="Output MP4 path")
    parser.add_argument("--cover", type=Path, help="Optional PNG/JPG cover image")
    parser.add_argument("--title", help="Episode title shown on the generated title card")
    parser.add_argument(
        "--voice",
        default="cedar",
        choices=[
            "alloy", "ash", "ballad", "coral", "echo", "fable", "nova",
            "onyx", "sage", "shimmer", "verse", "marin", "cedar",
        ],
        help="OpenAI narration voice (default: cedar)",
    )
    parser.add_argument(
        "--model", default="gpt-4o-mini-tts", help="Speech model (default: gpt-4o-mini-tts)"
    )
    parser.add_argument(
        "--instructions", default=DEFAULT_VOICE_INSTRUCTIONS,
        help="Narration style instructions",
    )
    parser.add_argument(
        "--keep-audio", action="store_true",
        help="Also save the joined narration as a WAV file",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing MP4")
    return parser.parse_args()


def fail(message: str) -> "NoReturn":
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        fail(f"FFmpeg failed with exit code {exc.returncode}.")


def clean_report(raw: str) -> str:
    """Make Markdown scouting reports sound natural when narrated."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
    # Remove citation-only links completely. Without this pass, a Markdown
    # citation such as "([MLB](...))" is narrated as the stray word "MLB."
    text = re.sub(
        r"\s*\(\s*\[[^\]]+\]\(https?://[^)]+\)\s*\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*\[[^\]]+\]\(https?://[^)]+\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*https?://\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", "", text, flags=re.MULTILINE)
    text = text.replace("|", ". ")
    text = re.sub(r"[*_~`]", "", text)
    text = re.sub(r"\bSFG\b", "San Francisco Giants", text)
    text = re.sub(r"\bSDP\b", "San Diego Padres", text)
    text = re.sub(r"\bwRC\+\b", "weighted runs created plus", text, flags=re.IGNORECASE)
    text = re.sub(r"\bxFIP\b", "expected FIP", text, flags=re.IGNORECASE)
    text = re.sub(r"\bFIP\b", "F I P", text)
    text = re.sub(r"\bWPA\b", "win probability added", text)
    text = re.sub(r"\bWAR\b", "wins above replacement", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def infer_title(cleaned: str, report_path: Path) -> str:
    first_line = next((line.strip(" .:-") for line in cleaned.splitlines() if line.strip()), "")
    if 4 <= len(first_line) <= 90:
        return first_line
    return report_path.stem.replace("_", " ").replace("-", " ").title()


def split_for_speech(text: str, limit: int = 3500) -> list[str]:
    """Split on paragraphs/sentences while staying well below request limits."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    def add_piece(piece: str) -> None:
        nonlocal current
        candidate = f"{current}\n\n{piece}".strip() if current else piece
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = piece

    for paragraph in paragraphs:
        if len(paragraph) <= limit:
            add_piece(paragraph)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        for sentence in sentences:
            if len(sentence) <= limit:
                add_piece(sentence)
            else:
                for start in range(0, len(sentence), limit):
                    add_piece(sentence[start : start + limit])
    if current:
        chunks.append(current)
    return chunks


def find_font(bold: bool, size: int):
    try:
        from PIL import ImageFont
    except ImportError:
        fail("Pillow is required. Install it with: python -m pip install pillow")

    names = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/Library/Fonts/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/Library/Fonts/Arial.ttf", "C:/Windows/Fonts/arial.ttf"]
    )
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def make_title_card(path: Path, title: str) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        fail("Pillow is required. Install it with: python -m pip install pillow")

    width, height = VIDEO_SIZE
    image = Image.new("RGB", VIDEO_SIZE)
    pixels = image.load()
    for y in range(height):
        blend = y / max(height - 1, 1)
        color = (int(5 + 8 * blend), int(20 + 18 * blend), int(39 + 25 * blend))
        for x in range(width):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)
    gold = "#F5B335"
    white = "#F7F4EA"
    muted = "#9FB0C3"
    draw.rectangle((0, 0, 34, height), fill=gold)
    draw.ellipse((1340, -320, 2040, 380), outline="#173C61", width=28)
    draw.ellipse((-250, 760, 430, 1440), outline="#173C61", width=20)
    draw.rounded_rectangle((135, 135, 1785, 945), radius=38, outline="#315474", width=3)

    brand_font = find_font(True, 84)
    title_font = find_font(True, 66)
    sub_font = find_font(False, 34)
    draw.text((190, 205), "BUSTER BALL", font=brand_font, fill=gold)
    draw.text((195, 305), "PODCAST", font=brand_font, fill=white)
    draw.rectangle((195, 430, 720, 438), fill=gold)

    wrapped = textwrap.wrap(title, width=38)[:4] or ["Game-Day Scouting Report"]
    title_y = 505
    for line in wrapped:
        draw.text((195, title_y), line, font=title_font, fill=white)
        title_y += 82

    draw.text((195, 865), "ADVANCED SABERMETRICS  •  HOSTED BY BILLY B",
              font=sub_font, fill=muted)
    image.save(path, "PNG")


def create_speech(chunks: list[str], temp_dir: Path, model: str, voice: str,
                  instructions: str) -> list[Path]:
    if not os.environ.get("OPENAI_API_KEY"):
        fail("OPENAI_API_KEY is not set.")
    try:
        from openai import OpenAI
    except ImportError:
        fail("The OpenAI package is required. Install it with: python -m pip install openai")

    client = OpenAI()
    files: list[Path] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        destination = temp_dir / f"narration_{index:03d}.wav"
        print(f"Generating narration section {index}/{total}...")
        with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=chunk,
            instructions=instructions,
            response_format="wav",
        ) as response:
            response.stream_to_file(destination)
        files.append(destination)
    return files


def join_audio(files: list[Path], destination: Path, temp_dir: Path) -> None:
    concat_file = temp_dir / "audio_files.txt"
    concat_file.write_text(
        "".join(f"file '{path.as_posix()}'\n" for path in files), encoding="utf-8"
    )
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c:a", "pcm_s16le", str(destination),
    ])


def make_video(cover: Path, audio: Path, output: Path, title: str, overwrite: bool) -> None:
    overwrite_flag = "-y" if overwrite else "-n"
    video_filter = (
        "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,setsar=1[base];"
        "[1:a]asplit=2[audio][viz];"
        "[viz]showwaves=s=1500x170:mode=line:colors=0xF5B335:r=30,format=rgba[wave];"
        "[base][wave]overlay=(W-w)/2:H-h-65:shortest=1,format=yuv420p[video]"
    )
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", overwrite_flag,
        "-loop", "1", "-framerate", "30", "-i", str(cover), "-i", str(audio),
        "-filter_complex", video_filter,
        "-map", "[video]", "-map", "[audio]",
        "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest",
        "-metadata", f"title={title}",
        "-metadata", "artist=Buster Ball Podcast — Billy B",
        "-metadata", "comment=Contains an AI-generated narration voice",
        str(output),
    ])


def main() -> None:
    args = parse_args()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        fail("FFmpeg and ffprobe must be installed and available on PATH.")
    if not args.report.is_file():
        fail(f"Report not found: {args.report}")
    if args.report.suffix.lower() not in {".txt", ".md", ".markdown"}:
        fail("The scouting report must be a .txt, .md, or .markdown file.")
    if args.cover and not args.cover.is_file():
        fail(f"Cover image not found: {args.cover}")

    output = (args.output or args.report.with_suffix(".mp4")).resolve()
    if output.suffix.lower() != ".mp4":
        output = output.with_suffix(".mp4")
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
    title = args.title or infer_title(cleaned, args.report)
    narration = f"{AI_DISCLOSURE}\n\n{cleaned}"
    chunks = split_for_speech(narration)

    with tempfile.TemporaryDirectory(prefix="buster_ball_") as temp_name:
        temp_dir = Path(temp_name)
        speech_files = create_speech(
            chunks, temp_dir, args.model, args.voice, args.instructions
        )
        joined_audio = temp_dir / "buster_ball_narration.wav"
        join_audio(speech_files, joined_audio, temp_dir)

        if args.cover:
            cover = args.cover.resolve()
        else:
            cover = temp_dir / "buster_ball_title_card.png"
            make_title_card(cover, title)

        print("Building MP4...")
        make_video(cover, joined_audio, output, title, args.overwrite)

        if args.keep_audio:
            audio_copy = output.with_suffix(".wav")
            shutil.copy2(joined_audio, audio_copy)
            print(f"Saved narration: {audio_copy}")

    print(f"Created: {output}")


if __name__ == "__main__":
    main()
