# Click & Chill Studio

A local Windows studio for turning scripted computer tutorials into illustrated short videos, with narration, captions and thumbnails.

## Run locally

Install the Python dependencies with `python -m pip install -r requirements.txt`, then run `python studio.py` and open http://127.0.0.1:8765.

Rendering requires compatible local FFmpeg, speech model and voice files under `work/runtime` in the checkout. See `pipeline.py` and `speech.py` for the expected paths. Optional local text generation uses the endpoint configured in `pipeline.py`. This repository contains the application source and topic catalog. Runtime binaries, models, account credentials and existing episodes are excluded.
