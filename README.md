# Flow Affiliate AI

Fashion-affiliate automation built around three reusable cores:

1. Google Flow through `gflow-cli`
2. Vietnamese TTS through Gemini or Edge TTS
3. Final vertical-video rendering through FFmpeg

The novel/story engine from `Movie-AI-Flow` is intentionally excluded.

## V1 contract

Input:

- fixed prompt templates inside the application
- one character reference image
- one product reference image

Output:

- one completed 1080x1920 affiliate video

## End-to-end pipeline

```text
character image + product image
        ↓
Flow image i2i: isolate product
        ↓
Flow image i2i: character wears product
        ↓
Flow video r2v: character showcase
        ↓
Flow video r2v: product detail
        ↓
Gemini / Edge TTS
        ↓
FFmpeg + voice + optional music/captions
        ↓
final_video.mp4
```

No `rembg` dependency is required in V1. Product isolation is performed by the fixed semantic extraction prompt inside Flow.

## Local web dashboard

V0.3 adds a lightweight local FastAPI dashboard. It is intentionally bound to `127.0.0.1` and exposes only generated assets located under the configured local `data/` directory.

Install web + TTS dependencies:

```powershell
pip install -e ".[tts,web,dev]"
```

Start the dashboard:

```powershell
flow-affiliate-web
```

Open:

```text
http://127.0.0.1:8000
```

The dashboard provides:

- character-image upload + preview
- product-image upload + preview
- Gemini/Edge TTS selector
- voice and product-shot controls
- explicit Flow-credit approval
- live checkpoint polling
- intermediate image/video previews
- approved fallback retry after a failed character-video attempt
- final video preview + MP4 download
- core health check for Flow, TTS, FFmpeg and FFprobe

Web jobs are serialized through one local worker so a single authenticated Flow browser session is not driven concurrently.

## Checkpoints and retries

Each run has a durable state file under `data/jobs/<job-id>.json`. Completed image/video/audio/render outputs are reused when the same job resumes, so a later failure does not force the entire workflow to start again.

Character-video prompts use three tested complexity levels:

```text
Level 3 → fail → prepare Level 2
Level 2 → fail → prepare Level 1
```

A paid fallback video attempt is never submitted silently. After a failure, rerun the same job with explicit paid-retry approval, or use the dashboard retry button.

## Requirements

- Python 3.11+
- `gflow-cli`
- Google Flow access and an authenticated desktop session
- FFmpeg + FFprobe in PATH
- Gemini API key or Edge TTS

Install/authenticate `gflow-cli` separately, then verify:

```powershell
gflow auth login --browser chrome
gflow auth status
```

For Gemini TTS set:

```powershell
$env:GEMINI_API_KEY = "YOUR_KEY"
```

Optional Flow configuration:

```powershell
$env:GFLOW_PROFILE = "default"
$env:GFLOW_BIN = "gflow"
```

## CLI run

```powershell
flow-affiliate `
  --job-id dress-001 `
  --character "D:\inputs\character.png" `
  --product "D:\inputs\dress.png" `
  --approve-video-credits
```

If the character video fails and the job records a lower fallback level, inspect the failure and explicitly retry:

```powershell
flow-affiliate `
  --job-id dress-001 `
  --character "D:\inputs\character.png" `
  --product "D:\inputs\dress.png" `
  --approve-video-credits `
  --approve-paid-retry
```

Optional arguments include `--tts edge`, `--voice`, `--product-video-style pan`, `--music`, and `--captions-ass`.

## Runtime workspace

```text
data/
├── uploads/              # web-uploaded character/product inputs
├── web_jobs/             # persistent web job options
├── jobs/                 # durable pipeline checkpoints
├── gflow_jobs/           # durable provider submission state
└── runs/
    └── <job-id>/
        ├── images/
        │   ├── product_isolated.png
        │   └── character_wearing_product.png
        ├── clips/
        ├── audio/
        └── renders/
            └── final_video.mp4
```

## Package layout

```text
src/flow_affiliate_ai/
├── providers/
│   ├── flow/             # gflow-cli image/video adapter
│   ├── tts/              # Gemini + Edge TTS
│   └── render/           # FFmpeg renderer
├── prompts/              # fixed fashion prompts + L3/L2/L1 fallback
├── services/             # thin reusable service layer
├── web/                  # FastAPI + static local dashboard
├── jobs.py               # durable job checkpoints
├── pipeline.py           # end-to-end orchestration
└── cli.py                # flow-affiliate command
```

## Tests

```powershell
pytest -q
```

CI installs the web dependencies and tests the dashboard routes, upload validation, and the rule that asset delivery cannot escape the local `data/` directory. Pipeline tests use fake providers and do not consume Flow credits.

## gflow-cli note

`gflow-cli` is an unofficial third-party CLI that automates the user's own Google Flow session. Keep it isolated behind the provider interface because its browser/UI integration can change independently of this project.
