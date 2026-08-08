# Flow Affiliate AI

Minimal fashion-affiliate automation toolkit built around three reusable cores:

1. Google Flow generation through `gflow-cli`
2. Vietnamese TTS through Gemini or Edge TTS
3. Final vertical-video rendering through FFmpeg

This repository intentionally excludes the novel/story pipeline from `Movie-AI-Flow` and keeps the core generation stack small and reusable.

## Planned pipeline

```text
character image + product image
        ↓
prompt templates / image preparation
        ↓
Google Flow via gflow-cli
        ↓
character clip + product clip
        ↓
TTS voice-over
        ↓
FFmpeg edit / captions / music
        ↓
final 9:16 affiliate video
```

The first implementation commit will add the standalone provider modules, services, fallback prompts, and tests.
