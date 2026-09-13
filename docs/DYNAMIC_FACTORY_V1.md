# Dynamic Shot Factory V1

This is the new simple factory path. The legacy V1 pipeline remains available.

## Contract

Input:

- one product image
- one original model image
- one logo image

Output:

- isolated product image
- canonical image of the same model wearing the product
- validated ChatGPT shot plan (4–8 shots)
- one Flow video per planned shot
- one final 1080x1920 MP4 assembled in planner-defined order with the original logo overlaid by FFmpeg

## Pipeline

```text
product image
   ↓
Flow image i2i: isolate product
   ↓
original model + isolated product
   ↓
ChatGPT/OpenAI visual planner
   ├─ wear_model_prompt
   ├─ shot_01 prompt + duration + source
   ├─ shot_02 prompt + duration + source
   ├─ ...
   └─ edit_sequence
   ↓
Flow image i2i: canonical worn-model image
   ↓
credit estimate
   ↓ explicit approval
Flow video generation, sequential + checkpointed
   ↓
FFmpeg edit_sequence
   ↓
original logo overlay
   ↓
final_video.mp4
```

The planner JSON is validated before any paid Flow video is submitted. The plan is limited to 4–8 shots and durations 4/6/8/10 seconds.

## Install

```powershell
pip install -e ".[planner]"
```

The existing web/TTS extras may be installed as needed:

```powershell
pip install -e ".[planner,web,tts,dev]"
```

## Configuration

```powershell
$env:OPENAI_API_KEY = "YOUR_KEY"
$env:OPENAI_PLANNER_MODEL = "gpt-5.6-luna"
$env:GFLOW_BIN = "gflow"
$env:GFLOW_PROFILE = "default"
```

OpenAI API billing is separate from Google Flow credits. The OpenAI call is used only to create the shot plan; Flow remains responsible for image/video generation.

## Plan first

Run without video-credit approval:

```powershell
flow-affiliate-factory `
  --job-id dress-001 `
  --model "D:\inputs\model.png" `
  --product "D:\inputs\dress.png" `
  --logo "D:\inputs\logo.png"
```

The pipeline will:

1. isolate the product;
2. ask ChatGPT for the shot plan;
3. create the canonical worn-model image;
4. estimate video credits;
5. stop at `PLAN_READY` without submitting paid video jobs.

Inspect `metadata.estimated_total_credits` and `shot_plan` in the printed job state.

## Approve and render

Rerun the same job with the same three inputs:

```powershell
flow-affiliate-factory `
  --job-id dress-001 `
  --model "D:\inputs\model.png" `
  --product "D:\inputs\dress.png" `
  --logo "D:\inputs\logo.png" `
  --approve-video-credits
```

The existing plan and images are reused. Each completed shot is checkpointed immediately.

## Paid shot retry

If one shot fails, successful shots are preserved. A failed paid shot is never resubmitted silently.

After inspection, retry with:

```powershell
flow-affiliate-factory `
  --job-id dress-001 `
  --model "D:\inputs\model.png" `
  --product "D:\inputs\dress.png" `
  --logo "D:\inputs\logo.png" `
  --approve-video-credits `
  --approve-paid-retry
```

Only the failed/missing shots are submitted again. Already completed Flow videos are reused.

## Workspace

```text
data/
├── jobs/
│   └── <job-id>.json
├── gflow_jobs/
└── runs/
    └── <job-id>/
        ├── images/
        │   ├── product_isolated.*
        │   └── model_wearing_product.*
        ├── planner/
        │   ├── plan.json
        │   └── raw_response.json
        ├── clips/
        │   ├── shot_01/a1/*.mp4
        │   ├── shot_02/a1/*.mp4
        │   └── ...
        └── renders/
            └── final_video.mp4
```

## Planner safety contract

- 4–8 shots only
- sequential IDs: `shot_01`, `shot_02`, ...
- allowed durations: 4, 6, 8, 10 seconds
- source is only `worn_model` or `isolated_product`
- every shot appears exactly once in `edit_sequence`
- Flow prompts are English and self-contained
- prompts may not ask Flow to render logos/text/UI
- the product image is the source of truth
- the original model is the identity source of truth

Invalid planner output stops the job before paid video generation.
