# Datamosher

A local browser app for glitching videos with real motion-vector datamoshing.

Datamosher runs a small Flask server on your machine, lets you upload a video, applies ffmpeg-based glitch effects, and downloads the rendered MP4.

Built for the Hermes Agent hackathon.

## Effects

- Motion Datamosh: MPEG4 P-frame duplication plus I-VOP stripping for real motion-vector smear at scene cuts.
- Pixel Drift: frame echo and blur.
- Color Bleed: chroma shift with temporal blending.
- Feedback Loop: phoenix blend, saturation, and sharpening.

## Requirements

- Python 3.10+
- ffmpeg and ffprobe available on your PATH

macOS with Homebrew:

```bash
brew install python ffmpeg
```

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install python3 python3-venv ffmpeg
```

## Install

```bash
git clone https://github.com/YOUR_USERNAME/datamosher.git
cd datamosher
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
python app.py
```

For Flask debug/reload while developing:

```bash
FLASK_DEBUG=1 python app.py
```

Open:

```text
http://127.0.0.1:5555/
```

The app is local-only by default. It binds to `127.0.0.1`, not the public network.

## Usage

1. Open the local web UI.
2. Upload an MP4/MOV/AVI/MKV/WebM video.
3. Pick an effect and intensity.
4. Click the mosh/render button.
5. Download the generated MP4.

## Data and generated files

By default, uploads and renders are written to:

```text
./data/uploads/
./data/outputs/
```

These folders are ignored by git.

To use a different data directory:

```bash
DATAMOSHER_DATA_DIR=/tmp/datamosher-data python app.py
```

To change the max upload size in MB:

```bash
DATAMOSHER_MAX_UPLOAD_MB=1000 python app.py
```

## How the motion datamosh works

The main effect follows the classic datamosh approach:

1. Detect scene cuts with ffmpeg.
2. Encode clip segments as MPEG4 with no B-frames and no scene keyframes.
3. Extract MPEG4 elementary streams as `.m4v`.
4. Duplicate the last P-VOP from the previous segment.
5. Strip the next segment's I-VOP.
6. Decode the resulting stream so motion vectors from the new shot smear the old reference frame.
7. Re-encode to H.264 MP4 for browser playback.

If no hard cuts are detected, the app creates artificial split points so one-shot clips can still produce an effect.

## Development

Syntax check:

```bash
python -m py_compile app.py
```

Quick server check:

```bash
python app.py
curl http://127.0.0.1:5555/
```

## Security / privacy notes

- Datamosher does not require API keys, cloud credentials, Apple credentials, or tokens.
- Uploaded videos stay on your local machine unless you choose to share them.
- Generated media is ignored by git.
- Do not run the Flask development server exposed to the public internet.

## License

MIT. See `LICENSE`.
