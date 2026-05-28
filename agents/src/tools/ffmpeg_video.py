"""
Tools for video editing using ffmpeg.

Provides cut, trim, and concatenate operations for video files.
Requires ffmpeg to be installed on the system.
"""

from __future__ import annotations

import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from strands import tool


def _check_ffmpeg() -> bool:
    """Check if ffmpeg is available."""
    return shutil.which("ffmpeg") is not None


def _run_ffmpeg(args: list[str], timeout: int = 300) -> tuple[bool, str]:
    """Run ffmpeg with given arguments."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y"] + args,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            return False, result.stderr
        return True, "Success"
    except subprocess.TimeoutExpired:
        return False, f"Operation timed out after {timeout} seconds"
    except Exception as e:
        return False, str(e)


def _parse_timestamp(ts: str) -> str:
    """Validate and return timestamp in HH:MM:SS or HH:MM:SS.mmm format."""
    # Accept formats: SS, MM:SS, HH:MM:SS, or with decimals
    ts = ts.strip()
    parts = ts.split(":")
    if len(parts) == 1:
        # Just seconds
        return f"00:00:{float(parts[0]):06.3f}"
    elif len(parts) == 2:
        # MM:SS
        return f"00:{int(parts[0]):02d}:{float(parts[1]):06.3f}"
    elif len(parts) == 3:
        # HH:MM:SS
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}:{float(parts[2]):06.3f}"
    return ts


@tool
def cut_video(
    input_path: str,
    output_path: str,
    start_time: str,
    end_time: Optional[str] = None,
    duration: Optional[str] = None,
) -> str:
    """
    Cut/trim a segment from a video file.

    You must provide either end_time OR duration (not both).

    Args:
        input_path: Path to the input video file.
        output_path: Path for the output video file.
        start_time: Start time for the cut (format: HH:MM:SS, MM:SS, or SS).
        end_time: End time for the cut (format: HH:MM:SS, MM:SS, or SS).
        duration: Duration of the segment (format: HH:MM:SS, MM:SS, or SS).

    Returns:
        Success message or error description.

    Examples:
        cut_video("input.mp4", "output.mp4", "00:01:30", end_time="00:02:45")
        cut_video("input.mp4", "clip.mp4", "30", duration="60")
    """
    if not _check_ffmpeg():
        return "Error: ffmpeg is not installed. Install it with: brew install ffmpeg (macOS) or apt install ffmpeg (Linux)"

    input_file = Path(input_path).expanduser().resolve()
    output_file = Path(output_path).expanduser().resolve()

    if not input_file.exists():
        return f"Error: Input file not found: {input_path}"

    if end_time is None and duration is None:
        return "Error: Must provide either end_time or duration"

    if end_time is not None and duration is not None:
        return "Error: Provide either end_time or duration, not both"

    # Build ffmpeg command
    args = ["-i", str(input_file), "-ss", _parse_timestamp(start_time)]

    if end_time:
        args.extend(["-to", _parse_timestamp(end_time)])
    else:
        args.extend(["-t", _parse_timestamp(duration)])

    # Copy codecs for speed (no re-encoding)
    args.extend(["-c", "copy", str(output_file)])

    success, msg = _run_ffmpeg(args)
    if success:
        return f"Video cut successfully: {output_file}"
    return f"Error cutting video: {msg}"


@tool
def concat_videos(
    input_paths: list[str],
    output_path: str,
    reencode: bool = False,
) -> str:
    """
    Concatenate multiple video clips into a single video.

    For videos with the same codec/resolution, use reencode=False (faster).
    For videos with different formats, use reencode=True.

    Args:
        input_paths: List of paths to input video files (in order).
        output_path: Path for the output concatenated video.
        reencode: If True, re-encode videos (slower but handles different formats).

    Returns:
        Success message or error description.

    Examples:
        concat_videos(["clip1.mp4", "clip2.mp4", "clip3.mp4"], "final.mp4")
        concat_videos(["a.mov", "b.mp4"], "combined.mp4", reencode=True)
    """
    if not _check_ffmpeg():
        return "Error: ffmpeg is not installed. Install it with: brew install ffmpeg (macOS) or apt install ffmpeg (Linux)"

    if len(input_paths) < 2:
        return "Error: Need at least 2 videos to concatenate"

    # Resolve all input paths
    resolved_inputs = []
    for p in input_paths:
        input_file = Path(p).expanduser().resolve()
        if not input_file.exists():
            return f"Error: Input file not found: {p}"
        resolved_inputs.append(input_file)

    output_file = Path(output_path).expanduser().resolve()

    if reencode:
        # Use filter_complex for re-encoding (handles different formats)
        filter_inputs = "".join(f"[{i}:v][{i}:a]" for i in range(len(resolved_inputs)))
        filter_complex = f"{filter_inputs}concat=n={len(resolved_inputs)}:v=1:a=1[outv][outa]"

        args = []
        for f in resolved_inputs:
            args.extend(["-i", str(f)])
        args.extend([
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            str(output_file)
        ])
    else:
        # Use concat demuxer (fast, no re-encoding)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for input_file in resolved_inputs:
                f.write(f"file '{input_file}'\n")
            concat_list = f.name

        args = [
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            str(output_file)
        ]

    success, msg = _run_ffmpeg(args, timeout=600)
    if success:
        return f"Videos concatenated successfully: {output_file}"
    return f"Error concatenating videos: {msg}"


@tool
def get_video_info(input_path: str) -> str:
    """
    Get information about a video file (duration, resolution, codec, etc.).

    Args:
        input_path: Path to the video file.

    Returns:
        Video information or error description.

    Examples:
        get_video_info("video.mp4")
    """
    if not shutil.which("ffprobe"):
        return "Error: ffprobe is not installed. Install ffmpeg which includes ffprobe."

    input_file = Path(input_path).expanduser().resolve()
    if not input_file.exists():
        return f"Error: File not found: {input_path}"

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(input_file)
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return f"Error: {result.stderr}"

        import json
        data = json.loads(result.stdout)

        # Extract key info
        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0))
        size_mb = int(fmt.get("size", 0)) / (1024 * 1024)

        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

        info = [
            f"File: {input_file.name}",
            f"Duration: {int(duration // 60)}:{int(duration % 60):02d} ({duration:.2f}s)",
            f"Size: {size_mb:.2f} MB",
        ]

        if video_stream:
            info.append(f"Video: {video_stream.get('codec_name', 'unknown')} "
                       f"{video_stream.get('width', '?')}x{video_stream.get('height', '?')} "
                       f"@ {eval(video_stream.get('r_frame_rate', '0/1')):.2f} fps")

        if audio_stream:
            info.append(f"Audio: {audio_stream.get('codec_name', 'unknown')} "
                       f"{audio_stream.get('sample_rate', '?')} Hz "
                       f"{audio_stream.get('channels', '?')} channels")

        return "\n".join(info)

    except Exception as e:
        return f"Error getting video info: {e}"


@tool
def extract_audio(
    input_path: str,
    output_path: str,
    format: str = "mp3",
) -> str:
    """
    Extract audio track from a video file.

    Args:
        input_path: Path to the input video file.
        output_path: Path for the output audio file.
        format: Audio format (mp3, aac, wav, flac). Default: mp3.

    Returns:
        Success message or error description.

    Examples:
        extract_audio("video.mp4", "audio.mp3")
        extract_audio("movie.mkv", "soundtrack.wav", format="wav")
    """
    if not _check_ffmpeg():
        return "Error: ffmpeg is not installed."

    input_file = Path(input_path).expanduser().resolve()
    output_file = Path(output_path).expanduser().resolve()

    if not input_file.exists():
        return f"Error: Input file not found: {input_path}"

    codec_map = {
        "mp3": "libmp3lame",
        "aac": "aac",
        "wav": "pcm_s16le",
        "flac": "flac",
    }

    codec = codec_map.get(format.lower(), "libmp3lame")

    args = [
        "-i", str(input_file),
        "-vn",  # No video
        "-acodec", codec,
        str(output_file)
    ]

    success, msg = _run_ffmpeg(args)
    if success:
        return f"Audio extracted successfully: {output_file}"
    return f"Error extracting audio: {msg}"
