# Contributing

Thanks for checking out Datamosher.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5555/`.

## Before submitting changes

Run:

```bash
python -m py_compile app.py
```

Also test at least one small video through the web UI if your change touches rendering, uploads, downloads, or ffmpeg commands.

## Do not commit

- Videos or rendered outputs
- `data/`, `uploads/`, or `outputs/`
- `.env` files
- Credentials, tokens, app-specific passwords, signing certificates, or notarization profiles
- Build artifacts such as `dist/`, `build/`, `.app`, or `.dmg`
