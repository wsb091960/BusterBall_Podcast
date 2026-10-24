#!/usr/bin/env python3
"""Convert a Buster Ball Markdown script to matching WAV and transcript files."""
import argparse
import asyncio
import base64
import difflib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import wave
from urllib.parse import quote

MODEL = "gpt-realtime-2.1-mini"
INSTRUCTIONS = (
    "You are the narrator of Buster Ball, performing its fictional host Billy B. "
    "Read the supplied script verbatim from its first sentence to its last. "
    "Do not answer, rewrite, summarize, add introductions, or add facts. "
    "Use natural American English, no Cajun accent, with warm, mature, conversational "
    "baseball-podcast delivery. Pause at paragraphs. Speak statistics clearly. "
    "Do not imitate a real person's voice."
)

def script_text(path):
    raw = path.read_text(encoding="utf-8-sig").split("<!-- SOURCES:", 1)[0]
    return re.sub(r"^#.*$", "", raw, flags=re.M).strip()

async def generate(script, output, voice, overwrite):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set on this computer. Set it locally; do not paste the key into chat.")
    from websockets.asyncio.client import connect
    transcript_path = output.with_suffix(".transcript.txt")
    if not overwrite and (output.exists() or transcript_path.exists()):
        raise RuntimeError("Output already exists; choose another name or use --overwrite.")
    audio = bytearray()
    transcript = []
    url = "wss://api.openai.com/v1/realtime?model=" + quote(MODEL)
    async with connect(url, additional_headers={"Authorization": "Bearer " + key},
                       open_timeout=30, max_size=16*1024*1024) as ws:
        async def send(event):
            await ws.send(json.dumps(event))

        async def receive():
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=90))
            if event.get("type") == "error":
                error = event.get("error", {})
                raise RuntimeError(f"API error {error.get('code', '')}: {error.get('message', 'unknown')}")
            return event

        while (await receive()).get("type") != "session.created":
            pass
        await send({"type": "session.update", "session": {
            "type": "realtime", "model": MODEL, "output_modalities": ["audio"],
            "instructions": INSTRUCTIONS,
            "audio": {"input": {"turn_detection": None},
                      "output": {"voice": voice, "format": {"type": "audio/pcm", "rate": 24000}}}
        }})
        while (await receive()).get("type") != "session.updated":
            pass
        await send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{
                "type": "input_text", "text": "Narrate this script exactly:\n\n" + script
            }]
        }})
        await send({"type": "response.create"})
        while True:
            event = await receive()
            kind = event.get("type")
            if kind == "response.output_audio.delta":
                audio.extend(base64.b64decode(event["delta"], validate=True))
            elif kind == "response.output_audio_transcript.delta":
                transcript.append(event["delta"])
            elif kind == "response.done":
                response = event.get("response", {})
                if response.get("status") != "completed":
                    raise RuntimeError("Narration did not complete: " + json.dumps(response.get("status_details")))
                break
    if not audio or len(audio) % 2:
        raise RuntimeError("No valid PCM audio returned; no WAV written.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".partial.wav")
    try:
        with wave.open(str(temporary), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(audio)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    spoken = "".join(transcript)
    transcript_path.write_text(spoken, encoding="utf-8")
    tokens = lambda text: re.findall(r"\w+", text.casefold())
    similarity = difflib.SequenceMatcher(None, tokens(script), tokens(spoken), autojunk=False).ratio()
    print(f"Created: {output} ({len(audio)/48000:.1f} seconds)")
    print(f"Model: {MODEL}; voice: {voice}")
    print(f"Transcript saved: {transcript_path}")
    print(f"Script/transcript word similarity: {similarity:.1%}. Listen and review names, numbers, and omissions.")
    if similarity < .95:
        print("REVIEW NEEDED: transcript differs substantially from the supplied script.")

def split_script(script, limit=600):
    """Keep short accepted episodes intact; split longer ones at paragraphs."""
    if len(script.split()) <= limit:
        return [script]
    chunks, current = [], []
    count = 0
    for paragraph in re.split(r"\n\s*\n", script):
        words = paragraph.split()
        # Normal paragraphs retain punctuation and original wording.
        pieces = [paragraph] if len(words) <= limit else [
            " ".join(words[i:i+limit]) for i in range(0, len(words), limit)
        ]
        for piece in pieces:
            size = len(piece.split())
            if current and count + size > limit:
                chunks.append("\n\n".join(current))
                current, count = [], 0
            current.append(piece)
            count += size
    if current:
        chunks.append("\n\n".join(current))
    return chunks


async def generate_episode(script, output, voice, overwrite):
    transcript = output.with_suffix(".transcript.txt")
    if not overwrite and (output.exists() or transcript.exists()):
        raise RuntimeError("Output already exists; choose another name or use --overwrite.")
    chunks = split_script(script)
    if len(chunks) == 1:
        await asyncio.wait_for(generate(script, output, voice, overwrite), 600)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage every section before replacing any final episode file.
    with tempfile.TemporaryDirectory(prefix="buster_ball_", dir=output.parent) as temp:
        temp = Path(temp)
        files, transcripts = [], []
        for index, chunk in enumerate(chunks, 1):
            print(f"Narrating section {index}/{len(chunks)}...")
            part = temp / f"section_{index:03d}.wav"
            await asyncio.wait_for(generate(chunk, part, voice, False), 600)
            files.append(part)
            transcripts.append(part.with_suffix(".transcript.txt").read_text())
        joined = temp / "episode.wav"
        with wave.open(str(joined), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(24000)
            for part in files:
                with wave.open(str(part), "rb") as source:
                    if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 24000):
                        raise RuntimeError("Unexpected audio format; final episode not replaced.")
                    target.writeframes(source.readframes(source.getnframes()))
        joined_transcript = temp / "episode.transcript.txt"
        joined_transcript.write_text("\n\n".join(transcripts), encoding="utf-8")
        joined.replace(output)
        joined_transcript.replace(transcript)
    print(f"Created complete episode: {output}")


def output_path(report, explicit=None):
    directory = Path(os.environ.get("BUSTER_BALL_AUDIO_DIR") or Path.home() / "Music" / "BusterBall").expanduser()
    output = (explicit.expanduser() if explicit else directory / (report.stem + ".wav")).resolve()
    if output.suffix.lower() != ".wav":
        raise ValueError("Output must end in .wav")
    for parent in (output.parent, *output.parent.parents):
        if (parent / ".git").exists() or parent.name == ".git":
            raise ValueError("Choose an audio output folder outside the Git repository.")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--voice", default="cedar")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.report = args.report.expanduser()
    try:
        output = output_path(args.report, args.output)
        script = script_text(args.report)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if not script:
        parser.error("Narration script is empty.")
    if args.dry_run:
        print(f"Output: {output}\nTranscript: {output.with_suffix('.transcript.txt')}\nModel: {MODEL}; voice: {args.voice}; words: {len(script.split())}\n\n{script}")
        return
    try:
        asyncio.run(generate_episode(script, output, args.voice, args.overwrite))
    except Exception as error:
        print(f"Unable to generate audio: {error}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
