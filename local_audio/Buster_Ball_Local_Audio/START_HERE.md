# Buster Ball audio on your computer

Extract this folder onto your computer, such as into Documents. Run the converter there, outside GitHub and Codespaces. Download the episode Markdown narration into this folder too.

WAVs and transcripts now default to your user folder's `Music/BusterBall` directory. The directory is created when audio is generated. The converter rejects audio output inside a Git repository. It never commits, pushes, or uploads recordings.

The saved production model, Cedar voice, narration instructions, and section joining are preserved. This package changes where output is saved. It has been checked with simulated audio; a live voice request was not made during preparation.

## Setup once

Use Python 3.10 or newer. Open a terminal in the extracted folder and install:

```bash
python -m pip install -r requirements-voice.txt
```

If your computer uses `python3` or Windows `py`, use that command in place of `python` throughout.

Your `OPENAI_API_KEY` must be configured on this computer. A key stored only in Codespaces or GitHub Actions will not automatically be available here. Keep it in your local environment; never put it into the narration, a committed file, or chat. The voice request uses your API account.

## Generate an episode

Replace `Your_Episode.md` with your downloaded narration filename:

```bash
python buster_ball_report_to_wav.py "Your_Episode.md"
```

Find `Your_Episode.wav` and `Your_Episode.transcript.txt` in `Music/BusterBall`. Listen and review the transcript before publishing. Existing recordings are protected unless you deliberately pass `--overwrite`.

To preview narration and the output location without a voice request:

```bash
python buster_ball_report_to_wav.py "Your_Episode.md" --dry-run
```

To choose another folder, pass a full WAV filename with `-o`, or set `BUSTER_BALL_AUDIO_DIR` in your local environment. Choose a folder outside any Git repository. Running in Codespaces would save audio on that remote machine, so run on your own computer for this workflow.

## Existing GitHub automation

This package does not modify your GitHub repository or delete any recordings. If the old automatic WAV workflow is enabled, disable `Build Buster Ball WAV` under the repository's Actions tab so Markdown uploads do not keep generating audio there. Disabling it preserves the files and can be reversed.

Use clean narration prose. Content following `<!-- SOURCES:` and Markdown heading lines is omitted from narration, matching the saved converter behavior.
