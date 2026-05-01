# Hermes Agent Hackathon Notes

Datamosher is a local video-glitch web app built for the Hermes Agent hackathon. It turns ordinary videos into motion-vector datamosh art with a Flask UI and an ffmpeg-based processing pipeline.

## What it does

- Runs locally at `http://127.0.0.1:5555/`.
- Accepts MP4, MOV, AVI, MKV, and WebM uploads.
- Generates browser-playable MP4 outputs.
- Includes multiple glitch effects, including a true motion-vector datamosh mode.

## Why it is interesting

Most simple glitch apps stack visual filters on top of a video. Datamosher's main effect edits MPEG4 frame structure instead:

1. Detect scene cuts.
2. Re-encode segments as MPEG4 with P-frames and no B-frames.
3. Duplicate predictive frames around cuts.
4. Strip I-VOP frames so motion vectors from the next shot smear the previous image.
5. Re-encode the result to H.264 MP4 for easy browser playback.

That creates the liquid, broken-motion look associated with classic datamoshing rather than just applying a color or blur filter.

## How Hermes Agent helped

Hermes Agent was used as an AI development partner throughout the project:

- Iterated on the Flask app structure and local browser UI.
- Reworked the video pipeline toward real motion-vector datamoshing.
- Debugged ffmpeg commands and MPEG4 elementary stream handling.
- Tried macOS standalone packaging/signing experiments.
- Cleaned the project back down into a safe open-source repository.
- Added README, license, security notes, CI checks, and GitHub readiness cleanup.

## Suggested demo script

1. Start the app:

   ```bash
   python app.py
   ```

2. Open `http://127.0.0.1:5555/`.
3. Upload a short video with obvious hard cuts and motion.
4. Choose `Motion Datamosh`.
5. Set intensity around 70-90%.
6. Render and download the output.
7. Show the before/after in the submission video.

## Best demo footage

Use a short 5-15 second clip with:

- two or more hard cuts,
- visible camera or subject movement after each cut,
- high contrast subjects,
- not too much compression already applied.

The app creates artificial split points when no cuts are detected, but the strongest results come from real cuts.

## Known limitations

- Large videos can take a while to process because ffmpeg re-encodes the media.
- The most dramatic motion datamosh results need hard cuts plus motion.
- The Flask app is intended for local use, not public internet deployment as-is.
- ffmpeg and ffprobe must be installed separately and available on PATH.
- The current repository is the open-source local web app version, not a packaged macOS `.app` release.
