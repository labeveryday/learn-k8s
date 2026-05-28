"""
Gemini Music Generation Tool

Uses Lyria RealTime for AI music generation via WebSocket streaming.
Supports weighted prompts for blending musical styles.

Note: Requires the v1alpha API version.
"""

import os
import asyncio
import wave
from pathlib import Path
from datetime import datetime
from strands import tool
from google import genai
from google.genai import types


def _save_audio_to_wav(audio_data: bytes, file_path: str) -> None:
    """Save raw PCM audio data to WAV file (48kHz stereo 16-bit)."""
    with wave.open(file_path, 'wb') as wav_file:
        wav_file.setnchannels(2)  # Stereo
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(48000)  # 48kHz
        wav_file.writeframes(audio_data)


async def _generate_music_async(
    api_key: str,
    prompts: list,
    duration_seconds: int,
) -> bytes:
    """Async music generation using Lyria RealTime WebSocket."""
    # Use v1alpha API version for Lyria
    client = genai.Client(
        api_key=api_key,
        http_options={'api_version': 'v1alpha'}
    )

    # Build weighted prompts
    weighted_prompts = []
    for p in prompts:
        if isinstance(p, dict):
            weighted_prompts.append(
                types.WeightedPrompt(text=p.get("text", ""), weight=p.get("weight", 1.0))
            )
        else:
            weighted_prompts.append(types.WeightedPrompt(text=str(p), weight=1.0))

    audio_chunks = []

    # Calculate approximate chunks needed (each chunk is ~0.5s of audio)
    chunks_needed = duration_seconds * 2

    async with client.aio.live.music.connect(model='lyria-realtime-exp') as session:
        # Set the prompts
        await session.set_weighted_prompts(weighted_prompts)

        # Start playback
        await session.play()

        # Collect audio chunks
        chunk_count = 0
        async for msg in session.receive():
            if msg.server_content and msg.server_content.audio_chunks:
                for audio_chunk in msg.server_content.audio_chunks:
                    if audio_chunk.data:
                        audio_chunks.append(audio_chunk.data)
                        chunk_count += 1

            if chunk_count >= chunks_needed:
                break

        await session.stop()

    return b"".join(audio_chunks)


@tool
def generate_music(
    prompt: str,
    output_dir: str = "output",
    duration_seconds: int = 30,
) -> dict:
    """
    Generate music using Lyria RealTime.

    Args:
        prompt: Text description of the music to generate. Describe genre, mood,
                instruments, and style (e.g., "upbeat electronic dance track with
                synth leads and driving bass").
        output_dir: Directory to save the generated audio (default: "output")
        duration_seconds: Duration of music in seconds, 5-120 (default: 30)

    Returns:
        dict with keys:
            - success: bool indicating if generation succeeded
            - file_path: path to saved audio (if successful)
            - message: status message
            - error: error message (if failed)

    Notes:
        - Lyria generates high-quality instrumental music in real-time
        - Output is stereo 48kHz WAV format
        - Uses WebSocket streaming via v1alpha API
        - Requires GOOGLE_API_KEY environment variable
    """
    if duration_seconds < 5 or duration_seconds > 120:
        return {
            "success": False,
            "error": "duration_seconds must be between 5 and 120"
        }

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "GOOGLE_API_KEY environment variable not set"
        }

    try:
        # Run async generation
        audio_data = asyncio.run(_generate_music_async(
            api_key=api_key,
            prompts=[prompt],
            duration_seconds=duration_seconds,
        ))

        if not audio_data:
            return {
                "success": False,
                "error": "No audio data generated"
            }

        # Save audio
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gemini_music_{timestamp}.wav"
        file_path = output_path / filename

        _save_audio_to_wav(audio_data, str(file_path))

        return {
            "success": True,
            "file_path": str(file_path),
            "message": f"Music saved to {file_path}",
            "duration": duration_seconds,
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@tool
def generate_music_weighted(
    prompts: list,
    output_dir: str = "output",
    duration_seconds: int = 30,
) -> dict:
    """
    Generate music with weighted prompts using Lyria RealTime.

    Args:
        prompts: List of weighted prompts. Each prompt can be:
                 - A string (weight defaults to 1.0)
                 - A dict with "text" and "weight" keys
                 Example: [{"text": "jazz piano", "weight": 0.7},
                          {"text": "ambient synth", "weight": 0.3}]
        output_dir: Directory to save the generated audio (default: "output")
        duration_seconds: Duration of music in seconds, 5-120 (default: 30)

    Returns:
        dict with keys:
            - success: bool indicating if generation succeeded
            - file_path: path to saved audio (if successful)
            - message: status message
            - error: error message (if failed)

    Notes:
        - Weighted prompts allow blending multiple musical styles
        - Weights control the influence of each style
        - Requires GOOGLE_API_KEY environment variable
    """
    if not prompts:
        return {
            "success": False,
            "error": "At least one prompt is required"
        }

    if duration_seconds < 5 or duration_seconds > 120:
        return {
            "success": False,
            "error": "duration_seconds must be between 5 and 120"
        }

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "GOOGLE_API_KEY environment variable not set"
        }

    try:
        # Run async generation
        audio_data = asyncio.run(_generate_music_async(
            api_key=api_key,
            prompts=prompts,
            duration_seconds=duration_seconds,
        ))

        if not audio_data:
            return {
                "success": False,
                "error": "No audio data generated"
            }

        # Save audio
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gemini_music_weighted_{timestamp}.wav"
        file_path = output_path / filename

        _save_audio_to_wav(audio_data, str(file_path))

        return {
            "success": True,
            "file_path": str(file_path),
            "message": f"Music saved to {file_path}",
            "duration": duration_seconds,
            "num_prompts": len(prompts)
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
