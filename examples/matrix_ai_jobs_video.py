#!/usr/bin/env python3
"""
Matrix-Style AI Jobs Video Generator

Generates 3 cinematic clips about AI taking over jobs (Matrix aesthetic)
and concatenates them into a single video using ffmpeg.

Scene 1: Office worker surrounded by green code, AI awakening
Scene 2: Montage of AI replacing workers across industries
Scene 3: Human "unplugging" from the old work paradigm

Requires: GOOGLE_API_KEY environment variable
"""

import sys
import time
from pathlib import Path

# Add repo root to path for imports
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.tools.gemini_video import generate_video
from src.tools.ffmpeg_video import concat_videos, get_video_info

# Output configuration
OUTPUT_DIR = Path("output/matrix_ai_jobs")

# Scene prompts - Matrix aesthetic, AI replacing human jobs theme
SCENES = [
    {
        "name": "scene_1_awakening",
        "prompt": """Cinematic shot of an office worker at a desk, late at night.
        Green Matrix-style digital code rains down across multiple monitors.
        The worker looks up as holographic AI interfaces materialize around them.
        Eerie green lighting, slow camera push-in, cyberpunk atmosphere.
        Sound: ambient electronic hum, keyboard clicks fading to silence.""",
    },
    {
        "name": "scene_2_replacement",
        "prompt": """Dramatic montage sequence with Matrix green color grading.
        Quick cuts showing: a robot arm doing surgery, AI drones delivering packages,
        automated trucks on highways, code writing itself on screens.
        Human workers fade to translucent ghosts as machines take their place.
        Cinematic slow motion, epic orchestral tension building.
        Camera: dynamic tracking shots, dutch angles.""",
    },
    {
        "name": "scene_3_new_reality",
        "prompt": """A person removes VR goggles in a dark room, gasping.
        They look at their hands, then out a window at a gleaming AI-powered city.
        Sunrise breaking through, shifting from cold green to warm golden light.
        Expression changes from fear to cautious hope, accepting the new world.
        Cinematic shallow depth of field, emotional close-up.
        Sound: heartbeat slowing, ambient city hum, hopeful synth notes.""",
    },
]


def generate_scene(scene: dict, duration: int = 6) -> dict:
    """Generate a single scene video."""
    print(f"\n{'='*60}")
    print(f"Generating: {scene['name']}")
    print(f"{'='*60}")
    print(f"Prompt: {scene['prompt'][:100]}...")
    print("This may take 2-5 minutes...")

    start = time.time()
    result = generate_video(
        prompt=scene["prompt"],
        model="veo-3.1-generate-preview",
        duration_seconds=duration,
        aspect_ratio="16:9",
        negative_prompt="low quality, blurry, watermark, text overlay, amateur",
        output_dir=str(OUTPUT_DIR / "clips"),
        max_wait_seconds=600,
    )
    elapsed = time.time() - start

    if result["success"]:
        # Rename to scene name for clarity
        original_path = Path(result["file_path"])
        new_path = OUTPUT_DIR / "clips" / f"{scene['name']}.mp4"
        original_path.rename(new_path)
        result["file_path"] = str(new_path)
        print(f"Generated in {elapsed:.1f}s: {new_path}")
    else:
        print(f"Failed: {result.get('error', 'Unknown error')}")

    return result


def main():
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║          MATRIX: AI JOBS - Video Generator                   ║
    ║                                                              ║
    ║  "The Matrix is everywhere. It is all around us...          ║
    ║   It is the world that has been pulled over your eyes       ║
    ║   to blind you from the truth: AI is taking your job."      ║
    ╚══════════════════════════════════════════════════════════════╝
    """)

    # Create output directories
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "clips").mkdir(exist_ok=True)

    total_start = time.time()
    generated_clips = []

    # Generate all 3 scenes
    for i, scene in enumerate(SCENES, 1):
        print(f"\n[{i}/{len(SCENES)}] Processing scene...")
        result = generate_scene(scene, duration=6)

        if result["success"]:
            generated_clips.append(result["file_path"])
        else:
            print(f"\nError generating {scene['name']}: {result.get('error')}")
            print("Continuing with remaining scenes...")

    # Check if we have clips to concatenate
    if len(generated_clips) < 2:
        print("\nNot enough clips generated for concatenation.")
        print(f"Generated {len(generated_clips)} clip(s).")
        return

    print(f"\n{'='*60}")
    print(f"Generated {len(generated_clips)} clips successfully!")
    print(f"{'='*60}")

    # Show info for each clip
    for clip in generated_clips:
        print(f"\n{get_video_info(clip)}")

    # Concatenate all clips
    print(f"\n{'='*60}")
    print("Concatenating clips into final video...")
    print(f"{'='*60}")

    final_output = str(OUTPUT_DIR / "matrix_ai_jobs_final.mp4")
    concat_result = concat_videos(
        input_paths=generated_clips,
        output_path=final_output,
        reencode=False,  # Fast mode since all clips have same format
    )

    if "successfully" in concat_result:
        total_elapsed = time.time() - total_start
        print(f"\n{'='*60}")
        print("COMPLETE!")
        print(f"{'='*60}")
        print(f"Final video: {final_output}")
        print(f"Total time: {total_elapsed/60:.1f} minutes")
        print(f"\n{get_video_info(final_output)}")
    else:
        print(f"Concatenation failed: {concat_result}")
        print("Individual clips are still available in output/matrix_ai_jobs/clips/")


if __name__ == "__main__":
    main()
