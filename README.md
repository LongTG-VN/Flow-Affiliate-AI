# Flow Affiliate AI

Minimal fashion-affiliate automation toolkit built around three reusable cores:

1. Google Flow generation through `gflow-cli`
2. Vietnamese TTS through Gemini or Edge TTS
3. Final vertical-video rendering through FFmpeg

The novel/story engine from `Movie-AI-Flow` is intentionally excluded.

## Current pipeline

```text
final character image + clean product image
        ↓
short Flow prompt templates
        ↓
Google Flow via gflow-cli
        ↓
character clip + product clip
        ↓
Gemini / Edge TTS
        ↓
FFmpeg + voice + music + ASS captions
        ↓
final 1080x1920 affiliate video
```

Image extraction and character-wearing-product image generation are upstream steps for the next phase.

## Prompt fallback

Character-video prompts use three complexity levels:

```text
Level 3 → generation fails → prepare Level 2
Level 2 → generation fails → prepare Level 1
```

Fallback only prepares the next prompt. It does not automatically spend another Flow attempt/credit.

## Requirements

- Python 3.11+
- `gflow-cli`
- Google Flow desktop login
- FFmpeg + FFprobe in PATH
- optional Gemini or Edge TTS dependencies

Install:

```powershell
pip install -e ".[tts,dev]"
```

Copy `.env.example` to `.env` and configure the providers you use.

For Flow, authenticate in the desktop session:

```powershell
gflow auth login
gflow auth status
```

Run tests:

```powershell
pytest -q
```

## Package layout

```text
src/flow_affiliate_ai/
├── providers/
│   ├── flow/       # gflow-cli core
│   ├── tts/        # Gemini + Edge TTS
│   └── render/     # FFmpeg renderer
├── prompts/        # fashion prompts + fallback
├── services/       # thin reusable service layer
└── pipeline.py     # affiliate orchestration
```

## Safety around paid Flow generations

Flow jobs use deterministic idempotency keys and durable local state. Ambiguous duplicate submissions are refused. A failed prompt can produce a lower-complexity fallback prompt, but a new paid attempt remains explicit.
