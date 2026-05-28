"""
Gemini Video Understanding Tool

Supports Gemini 3 Pro Preview / Gemini 3 Flash Preview for *video understanding*:
- Upload a video file via the Files API (recommended for >20MB or reuse)
- Pass inline video bytes (<20MB)
- Pass YouTube URLs
- Optional video metadata: clipping (start/end offsets) and fps sampling
- Optional Gemini 3 media resolution control (v1alpha only)

Note: This tool is intentionally separate from `gemini_video.py`, which is Veo video generation.
"""

import os
import time
from pathlib import Path
from typing import Optional

from strands import tool
from google import genai
from google.genai import types


_DEFAULT_INLINE_MAX_BYTES = 20 * 1024 * 1024  # 20MB (docs guidance)


def _guess_mime_type_for_video(path: Path) -> str:
    suffix = path.suffix.lower()
    mime_map = {
        ".mp4": "video/mp4",
        ".mpeg": "video/mpeg",
        ".mpg": "video/mpg",
        ".mov": "video/mov",
        ".avi": "video/avi",
        ".webm": "video/webm",
        ".wmv": "video/wmv",
        ".3gpp": "video/3gpp",
        ".flv": "video/x-flv",
    }
    return mime_map.get(suffix, "video/mp4")


def _build_video_part(
    *,
    file_uri: Optional[str] = None,
    mime_type: Optional[str] = None,
    inline_bytes: Optional[bytes] = None,
    start_offset_s: Optional[int] = None,
    end_offset_s: Optional[int] = None,
    fps: Optional[float] = None,
    media_resolution: Optional[str] = None,
) -> types.Part:
    video_metadata = None
    if start_offset_s is not None or end_offset_s is not None or fps is not None:
        # VideoMetadata expects string offsets like "40s" and fps as number
        video_metadata = types.VideoMetadata(
            start_offset=(f"{start_offset_s}s" if start_offset_s is not None else None),
            end_offset=(f"{end_offset_s}s" if end_offset_s is not None else None),
            fps=fps,
        )

    media_resolution_obj = None
    if media_resolution is not None:
        # Per Gemini 3 docs, media_resolution is a dict-like object with a "level"
        media_resolution_obj = {"level": media_resolution}

    if inline_bytes is not None:
        return types.Part(
            inline_data=types.Blob(
                data=inline_bytes,
                mime_type=(mime_type or "video/mp4"),
            ),
            video_metadata=video_metadata,
            media_resolution=media_resolution_obj,
        )

    if not file_uri:
        raise ValueError("Must provide either inline_bytes or file_uri for the video part.")

    # file_data is used for both uploaded file URIs and YouTube URLs
    return types.Part(
        file_data=types.FileData(
            file_uri=file_uri,
            mime_type=mime_type,
        ),
        video_metadata=video_metadata,
        media_resolution=media_resolution_obj,
    )


def _create_client(*, api_key: str, api_version: str) -> genai.Client:
    # media_resolution is currently documented as v1alpha-only; other requests can use default.
    if api_version:
        return genai.Client(api_key=api_key, http_options={"api_version": api_version})
    return genai.Client(api_key=api_key)


def _wait_for_uploaded_file_ready(
    client: genai.Client,
    uploaded_file: object,
    *,
    max_wait_seconds: int = 300,
    poll_seconds: int = 2,
) -> object:
    """
    Best-effort polling for Files API processing.
    Different SDK versions expose slightly different shapes; we handle both.
    """
    start = time.time()
    current = uploaded_file

    def _state_str(f: object) -> Optional[str]:
        state = getattr(f, "state", None)
        if state is None:
            return None
        # Can be enum-like (state.name) or plain string
        return getattr(state, "name", None) or str(state)

    while True:
        s = _state_str(current)
        if s is None:
            return current
        s_upper = s.upper()
        if "ACTIVE" in s_upper or "READY" in s_upper or "PROCESSED" in s_upper:
            return current
        if "FAILED" in s_upper or "ERROR" in s_upper:
            raise RuntimeError(f"Uploaded file processing failed (state={s}).")
        if time.time() - start > max_wait_seconds:
            raise TimeoutError(f"Timed out waiting for uploaded file processing (last state={s}).")

        # Refresh via client.files.get if possible
        name = getattr(current, "name", None)
        if name:
            try:
                current = client.files.get(name=name)
            except Exception:
                # If get() isn't available / fails, just keep polling with current state.
                pass
        time.sleep(poll_seconds)


@tool
def understand_video(
    prompt: str,
    video_path: Optional[str] = None,
    youtube_url: Optional[str] = None,
    model: str = "gemini-3-flash-preview",
    use_file_api: bool = True,
    max_inline_bytes: int = _DEFAULT_INLINE_MAX_BYTES,
    start_offset_seconds: Optional[int] = None,
    end_offset_seconds: Optional[int] = None,
    fps: Optional[float] = None,
    media_resolution: Optional[str] = None,
    thinking_level: Optional[str] = None,
    max_wait_seconds: int = 300,
) -> dict:
    """
    Ask Gemini to analyze a video and return a text response.

    Provide the video in ONE of these ways:
    - video_path: local path to a video file (inline or Files API)
    - youtube_url: public YouTube URL (preview feature in Gemini API)

    Args:
        prompt: Your question/instructions (you can include timestamps like "00:42").
        video_path: Local path to a video file.
        youtube_url: Public YouTube URL.
        model: "gemini-3-pro-preview" or "gemini-3-flash-preview".
        use_file_api: If True, prefer Files API upload for local files (recommended).
        max_inline_bytes: Max bytes allowed for inline requests; above this we switch to Files API (if enabled).
        start_offset_seconds: Optional clip start offset in seconds.
        end_offset_seconds: Optional clip end offset in seconds.
        fps: Optional frame sampling rate (frames/sec) for video processing.
        media_resolution: Optional Gemini 3 media resolution setting (v1alpha only per docs).
        thinking_level: Optional Gemini 3 thinking level (Flash supports minimal/medium; Pro supports low/high).
        max_wait_seconds: Max time to wait for Files API processing (if used).

    Returns:
        dict with keys:
          - success: bool
          - model: str
          - text: str (if successful)
          - video_source: str ("file_api", "inline", "youtube_url")
          - file_uri: str (if file_api used)
          - error: str (if failed)
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"success": False, "error": "GOOGLE_API_KEY environment variable not set"}

    if bool(video_path) == bool(youtube_url):
        return {"success": False, "error": "Provide exactly one of video_path or youtube_url."}

    if start_offset_seconds is not None and start_offset_seconds < 0:
        return {"success": False, "error": "start_offset_seconds must be >= 0"}
    if end_offset_seconds is not None and end_offset_seconds < 0:
        return {"success": False, "error": "end_offset_seconds must be >= 0"}
    if (
        start_offset_seconds is not None
        and end_offset_seconds is not None
        and end_offset_seconds <= start_offset_seconds
    ):
        return {"success": False, "error": "end_offset_seconds must be > start_offset_seconds"}
    if fps is not None and fps <= 0:
        return {"success": False, "error": "fps must be > 0"}

    # media_resolution is documented as v1alpha-only
    api_version = "v1alpha" if media_resolution is not None else ""

    try:
        client = _create_client(api_key=api_key, api_version=api_version)

        video_source = None
        file_uri = None
        mime_type = None

        if youtube_url:
            video_source = "youtube_url"
            video_part = _build_video_part(
                file_uri=youtube_url,
                mime_type=None,
                start_offset_s=start_offset_seconds,
                end_offset_s=end_offset_seconds,
                fps=fps,
                media_resolution=media_resolution,
            )
        else:
            video_file = Path(str(video_path))
            if not video_file.exists():
                return {"success": False, "error": f"Video file not found: {video_path}"}

            mime_type = _guess_mime_type_for_video(video_file)
            file_size = video_file.stat().st_size

            should_inline = (not use_file_api) and (file_size <= max_inline_bytes)
            should_file_api = use_file_api or (file_size > max_inline_bytes)

            if should_inline:
                video_source = "inline"
                video_bytes = video_file.read_bytes()
                if len(video_bytes) > max_inline_bytes:
                    return {
                        "success": False,
                        "error": f"Video is too large for inline ({len(video_bytes)} bytes). Enable use_file_api=True.",
                    }
                video_part = _build_video_part(
                    inline_bytes=video_bytes,
                    mime_type=mime_type,
                    start_offset_s=start_offset_seconds,
                    end_offset_s=end_offset_seconds,
                    fps=fps,
                    media_resolution=media_resolution,
                )
            elif should_file_api:
                video_source = "file_api"
                uploaded = client.files.upload(file=str(video_file))
                uploaded = _wait_for_uploaded_file_ready(
                    client, uploaded, max_wait_seconds=max_wait_seconds
                )
                file_uri = getattr(uploaded, "uri", None) or getattr(uploaded, "file_uri", None)
                mime_type = getattr(uploaded, "mime_type", None) or mime_type
                if not file_uri:
                    # Fall back to directly passing uploaded object if SDK supports it
                    # (some SDK versions allow passing `uploaded` in contents directly)
                    video_part = uploaded  # type: ignore[assignment]
                else:
                    video_part = _build_video_part(
                        file_uri=file_uri,
                        mime_type=mime_type,
                        start_offset_s=start_offset_seconds,
                        end_offset_s=end_offset_seconds,
                        fps=fps,
                        media_resolution=media_resolution,
                    )
            else:
                return {"success": False, "error": "Unable to determine video input method."}

        # Per Google guidance: if combining text + single video, put text AFTER the video.
        contents = [
            types.Content(
                parts=[
                    video_part,
                    types.Part(text=prompt),
                ]
            )
        ]

        config = types.GenerateContentConfig()
        if thinking_level is not None:
            config.thinking_config = types.ThinkingConfig(thinking_level=thinking_level)

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        text = getattr(response, "text", None)
        if not text:
            # Fallback: try to reconstruct from parts
            parts = getattr(response, "parts", None)
            if parts:
                texts = [getattr(p, "text", "") for p in parts if getattr(p, "text", None)]
                text = "\n".join([t for t in texts if t]).strip() or None

        if not text:
            return {
                "success": False,
                "error": "No text in response (may have been blocked or returned non-text parts).",
                "model": model,
                "video_source": video_source,
                "file_uri": file_uri,
            }

        return {
            "success": True,
            "model": model,
            "text": text,
            "video_source": video_source,
            "file_uri": file_uri,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "model": model}


