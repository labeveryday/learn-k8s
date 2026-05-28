"""
Optional Strands tool wrappers for the native google-genai media helpers.

Use these if you want an agent to decide when to generate an image/video, while still
ensuring the generation call only receives the minimal prompt (not full conversation).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from strands import tool

from .gemini_media import generate_image, caption_image, generate_video


@tool
def gemini_generate_image(
    prompt: str,
    out_path: str = "examples/output/generated_image.png",
    model: str = "gemini-3-pro-image-preview",
) -> str:
    """
    Generate an image via Google's native Gemini API and save it to disk.

    Args:
        prompt: Image generation prompt.
        out_path: Output file path for the generated image.
        model: Gemini image-capable model id (default: gemini-3-pro-image-preview).

    Returns:
        The saved image path.
    """
    path = generate_image(prompt, model=model, out_path=out_path)
    return str(path)


@tool
def gemini_caption_image(
    image_path: str,
    prompt: str = "Caption this image.",
    model: str = "gemini-3-flash-preview",
) -> str:
    """
    Caption an image via Google's native Gemini API.

    Args:
        image_path: Path to the local image file.
        prompt: Caption instruction.
        model: Gemini model id (default: gemini-3-flash-preview).

    Returns:
        Caption text.
    """
    return caption_image(Path(image_path), prompt=prompt, model=model)


@tool
def gemini_generate_video(
    prompt: str,
    out_path: str = "examples/output/generated_video.mp4",
    model: str = "veo-3.1-generate-preview",
    poll_seconds: int = 10,
    timeout_seconds: int = 600,
) -> str:
    """
    Generate a video via Veo (google-genai) and save it to disk.

    Args:
        prompt: Video generation prompt.
        out_path: Output file path.
        model: Veo model id (default: veo-3.1-generate-preview).
        poll_seconds: Poll interval.
        timeout_seconds: Timeout before failing.

    Returns:
        The saved video path.
    """
    path = generate_video(
        prompt,
        model=model,
        out_path=out_path,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
    )
    return str(path)


