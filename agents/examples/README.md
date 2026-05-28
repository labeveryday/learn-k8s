# Examples

This directory contains example agents demonstrating different use cases.

## MCP Documentation Agent

`mcp_docs_agent.py` - An agent with MCP server integration for accessing AgentCore and Strands documentation.

```bash
python examples/mcp_docs_agent.py
```

This agent can:
- Search and fetch AWS AgentCore documentation
- Search and fetch Strands Agents framework documentation
- Answer questions about building and deploying agents

---

## Gemini Image Generation

`gemini_image_example.py` - Generate and edit images using Gemini 2.5 Flash Image (fast) or Gemini 3 Pro Image Preview (advanced).

```bash
# Generate an image (uses Gemini 3 Pro by default)
python examples/gemini_image_example.py --prompt "A serene mountain landscape at sunset"

# Fast generation with Gemini 2.5 Flash
python examples/gemini_image_example.py --prompt "A cute robot" --model gemini-2.5-flash-image

# Edit an existing image
python examples/gemini_image_example.py --edit path/to/image.png --edit-prompt "Make it more vibrant"

# Interactive mode with session tracking
python examples/gemini_image_example.py --interactive

# Without hub tracking
python examples/gemini_image_example.py --no-hub --prompt "A cute robot"
```

**Features:**
- Text-to-image generation with `generate_image` tool
- Image editing with `edit_image` tool
- Model selection: `gemini-2.5-flash-image` (fast) or `gemini-3-pro-image-preview` (advanced)
- Aspect ratio control (1:1, 16:9, 9:16, etc.)
- Resolution control (1K, 2K, 4K for Gemini 3 Pro)
- Multiple reference images (up to 14 for Gemini 3 Pro)
- Google Search grounding for real-time info (Gemini 3 Pro)
- Hub integration for session tracking and metrics

**Requirements:** `GOOGLE_API_KEY` environment variable

---

## Meme Remix (Understand → Generate in its likeness)

Use a two-step Strands workflow: **analyze** an existing meme image, then **generate** a brand-new, unique meme that keeps the same vibe/layout using reference-image guidance.

```bash
python examples/gemini_meme_remix_example.py \
  --meme path/to/meme.png \
  --prompt "Make a fresh, original version of this meme about debugging at 2am."
```

**How it works:**
- Step 1: `understand_image(...)` extracts template/layout/tone + current caption text
- Step 2: `generate_image(..., reference_images=[meme])` creates a new meme in the same style but with a new joke

**Requirements:** `GOOGLE_API_KEY` environment variable

---

## Gemini Image Understanding

Analyze images using Gemini models for captioning, visual Q&A, object detection, and segmentation.

```bash
# Caption an image
python -c "from src.tools import understand_image; print(understand_image(prompt='Caption this image', image_path='photo.jpg'))"

# Visual Q&A
python -c "from src.tools import understand_image; print(understand_image(prompt='What color is the car?', image_path='car.png'))"

# Compare multiple images
python -c "from src.tools import understand_image; print(understand_image(prompt='What is different?', image_paths=['before.jpg', 'after.jpg']))"

# Object detection with bounding boxes
python -c "from src.tools import detect_objects; print(detect_objects(image_path='photo.jpg', output_dir='output'))"

# Custom object detection
python -c "from src.tools import detect_objects; print(detect_objects(image_path='photo.jpg', prompt='Detect all faces'))"

# Segmentation with masks
python -c "from src.tools import segment_objects; print(segment_objects(image_path='table.jpg', prompt='wooden and glass items'))"
```

**Features:**
- Image captioning and visual Q&A with `understand_image` tool
- Object detection with bounding boxes using `detect_objects` tool (Gemini 2.0+)
- Segmentation with contour masks using `segment_objects` tool (Gemini 2.5+)
- Multiple image comparison (up to 3600 images)
- Image URL support
- Media resolution control (Gemini 3)
- Annotated image output with bounding boxes
- Mask overlay visualization

**Requirements:** `GOOGLE_API_KEY` environment variable, PIL (Pillow), numpy

---

## Gemini Video Generation

`gemini_video_example.py` - Generate videos using Veo 3.1, Veo 3, or Veo 2 with native audio.

```bash
# Generate video from text with audio (takes 1-5 minutes)
python examples/gemini_video_example.py --prompt "A drone shot over a coastal city at golden hour"

# With dialogue and sound effects
python examples/gemini_video_example.py --prompt "A man murmurs, 'This must be it.' The woman whispers, 'What did you find?'"

# Fast generation with Veo 3.1 Fast
python examples/gemini_video_example.py --model veo-3.1-fast-generate-preview --prompt "A cat sleeping"

# High resolution 1080p (requires 8s duration)
python examples/gemini_video_example.py --resolution 1080p --duration 8 --prompt "Mountain landscape"

# Portrait video for social media
python examples/gemini_video_example.py --aspect-ratio 9:16 --prompt "A dancer performing ballet"

# Generate video from image (image becomes first frame)
python examples/gemini_video_example.py --image path/to/image.png --prompt "Animate this scene"

# Frame interpolation (first and last frame)
python examples/gemini_video_example.py --image first.png --last-frame last.png --prompt "Transition between scenes"

# With reference images for consistent subjects (up to 3)
python examples/gemini_video_example.py --reference-images dress.png woman.png --prompt "Woman walks through lagoon"

# Interactive mode
python examples/gemini_video_example.py --interactive
```

**Features:**
- Text-to-video generation with `generate_video` tool
- Image-to-video animation with `generate_video_from_image` tool
- Video extension with `extend_video` tool (extend by 7 seconds, up to 148s total)
- Frame interpolation (specify first and last frames)
- Reference images (up to 3 for Veo 3.1)
- Native audio generation with dialogue and sound effects (Veo 3.1/3)
- Model selection: Veo 3.1, Veo 3.1 Fast, Veo 3, Veo 3 Fast, Veo 2
- Duration options: 4, 6, 8 seconds (5 for Veo 2)
- Aspect ratio: 16:9 (default), 9:16
- Resolution: 720p (default), 1080p (8s duration only)
- Negative prompts to exclude unwanted elements
- Hub integration for session tracking and metrics

**Requirements:** `GOOGLE_API_KEY` environment variable

---

## Gemini Video Understanding (Gemini 3)

`gemini_video_understanding_example.py` - Analyze videos with Gemini 3 Pro/Flash (upload/inline/YouTube URL).

```bash
# YouTube URL analysis (public videos)
python examples/gemini_video_understanding_example.py \
  --youtube-url "https://www.youtube.com/watch?v=9hE5-98ZeCg" \
  --prompt "Please summarize the video in 3 sentences."

# Local file analysis via Files API upload (recommended for large videos / reuse)
python examples/gemini_video_understanding_example.py \
  --video-path path/to/video.mp4 \
  --use-file-api \
  --prompt "Describe key events with timestamps for salient moments."

# Local small file inline analysis (<20MB by default)
python examples/gemini_video_understanding_example.py \
  --video-path path/to/small-video.mp4 \
  --inline \
  --prompt "Please summarize the video in 3 sentences."

# Interactive agent mode (hub integration enabled by default; disable with --no-hub)
python examples/gemini_video_understanding_example.py \
  --youtube-url "https://www.youtube.com/watch?v=9hE5-98ZeCg" \
  --interactive
```

**Features:**
- Upload via Files API (recommended for large files / reuse)
- Inline video bytes (<20MB by default)
- YouTube URL video understanding (preview)
- Clipping (`--start-offset-seconds`, `--end-offset-seconds`) and fps sampling (`--fps`)
- Optional Gemini 3 `media_resolution` control (v1alpha per docs)
- Hub integration for session tracking and metrics (`--no-hub` to disable)

**Note on models:**
- The **tool** uses Gemini 3 (`--model gemini-3-flash-preview|gemini-3-pro-preview`) for video understanding.
- The example includes a **workaround** so you can keep the **agent model on Gemini 3** without hitting thought-signature errors.

**Requirements:** `GOOGLE_API_KEY` environment variable

---

## Gemini Music Generation

`gemini_music_example.py` - Generate music using Lyria RealTime via WebSocket streaming.

```bash
# Generate music from text
python examples/gemini_music_example.py --prompt "Upbeat electronic dance track with synth leads"

# Specify duration (5-120 seconds)
python examples/gemini_music_example.py --duration 30 --prompt "Calm ambient meditation music"

# Weighted prompts for blending styles
python examples/gemini_music_example.py --weighted --prompts "jazz:0.7" "electronic:0.3"

# Interactive mode
python examples/gemini_music_example.py --interactive
```

**Features:**
- Text-to-music generation with `generate_music` tool
- Weighted prompt blending with `generate_music_weighted` tool
- Real-time WebSocket streaming
- Stereo 48kHz WAV output
- Hub integration for session tracking and metrics

**Requirements:** `GOOGLE_API_KEY` environment variable (uses v1alpha API for Lyria)

---

## Deploying to AWS Bedrock AgentCore

Deploy your Strands agent to [AWS Bedrock AgentCore](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html) for serverless, production deployment.

### Step 1: Create AgentCore Entry Point

AgentCore requires a specific entry point pattern using `BedrockAgentCoreApp`:

```python
# agentcore_agent.py
from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
from strands.models.bedrock import BedrockModel

app = BedrockAgentCoreApp()

# Create your agent
model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region_name="us-east-1",
)

agent = Agent(
    model=model,
    system_prompt="You are a helpful assistant...",
)

@app.entrypoint
def invoke(payload, context):
    """AgentCore entry point - receives requests and returns responses."""
    user_message = payload.get("prompt", "Hello!")
    result = agent(user_message)
    return {"result": str(result)}

if __name__ == "__main__":
    app.run()
```

### Step 2: Create Requirements File

```txt
# requirements.txt for AgentCore
bedrock-agentcore
strands-agents>=0.1.0
strands-agents-tools
boto3>=1.34.0
```

### Step 3: Deploy with AgentCore CLI

```bash
# Install AgentCore CLI
pip install bedrock-agentcore-starter-toolkit

# Configure your agent
agentcore configure --entrypoint agentcore_agent.py --non-interactive

# Deploy to AWS
agentcore launch

# Test your deployed agent
agentcore invoke '{"prompt": "Hello world!"}'

# Check status
agentcore status
```

### Key Requirements

- **Entry point**: Use `@app.entrypoint` decorator on your invoke function
- **App wrapper**: Initialize `BedrockAgentCoreApp()` and call `app.run()`
- **Dependencies**: Include `bedrock-agentcore` in requirements.txt
- **Bedrock models**: Use `BedrockModel` for model access within AgentCore

### MCP Tools on AgentCore

For MCP server tools on AgentCore:

1. Use the [AgentCore Gateway](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-gateway.html) to expose MCP servers
2. Configure IAM permissions for cross-service access
3. Update tool endpoints to use Gateway URLs

See the [AgentCore documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html) for detailed setup.
