#!/usr/bin/env python3
"""
Gemini Music Generation Example

Demonstrates using the generate_music and generate_music_weighted tools
with Lyria RealTime model.

Includes hub integration for session tracking and metrics.
"""

import sys
import argparse
import time
from pathlib import Path

# Add repo root to path for imports
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

from strands import Agent
from src.tools.gemini_music import generate_music, generate_music_weighted
from src.models import gemini_model
from src.hub import (
    create_session_manager,
    MetricsExporter,
    AgentRegistry,
)
from src.hub.session import generate_run_id

# Agent configuration
AGENT_ID = "gemini-music-generator"
AGENT_NAME = "Gemini Music Generator"


def main():
    parser = argparse.ArgumentParser(description="Generate music with Lyria RealTime")
    parser.add_argument(
        "--prompt",
        type=str,
        default="Upbeat electronic dance track with synth leads",
        help="Music generation prompt"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=15,
        help="Music duration in seconds (5-120)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="examples/output",
        help="Output directory for generated audio"
    )
    parser.add_argument(
        "--weighted",
        action="store_true",
        help="Use weighted prompts mode"
    )
    parser.add_argument(
        "--prompts",
        type=str,
        nargs="+",
        default=None,
        help="Weighted prompts in format 'text:weight' (e.g., 'jazz:0.7' 'electronic:0.3')"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Use interactive agent mode"
    )
    parser.add_argument(
        "--no-hub",
        action="store_true",
        help="Disable hub tracking"
    )

    args = parser.parse_args()

    # Ensure output directory exists
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # Initialize hub components (unless disabled)
    run_id = None
    registry = None
    metrics = None

    if not args.no_hub:
        run_id = generate_run_id(AGENT_ID)

        # Register agent
        registry = AgentRegistry()
        registry.register(
            agent_id=AGENT_ID,
            description="Generate music using Lyria RealTime (text and weighted prompts)",
            tags=["gemini", "music", "lyria", "generation"],
            model_id="lyria-realtime-exp",
        )

        # Initialize metrics
        metrics = MetricsExporter(
            agent_id=AGENT_ID,
            run_id=run_id,
        )

        print(f"Run ID: {run_id}")

    start_time = time.time()
    success = False

    if args.interactive:
        # Interactive agent mode
        print(f"\n{AGENT_NAME}")
        print("Available tools: generate_music, generate_music_weighted")
        print("Note: Music generation is real-time streaming")
        print("Type 'exit' to quit\n")

        # Create session manager for interactive mode
        session_manager = None
        if not args.no_hub:
            session_manager = create_session_manager(agent_id=AGENT_ID, run_id=run_id)

        model = gemini_model(model_id="gemini-2.0-flash")
        agent = Agent(
            model=model,
            tools=[generate_music, generate_music_weighted],
            session_manager=session_manager,
            system_prompt=f"""You are a music generation assistant using Lyria RealTime.

You can:
- Generate music from text descriptions using generate_music
- Blend musical styles using generate_music_weighted with weighted prompts

When generating music:
- Describe genre, mood, tempo, and instruments
- Use weighted prompts to blend styles (e.g., jazz 0.7 + electronic 0.3)
- Duration can be 5-120 seconds
- Output is stereo 48kHz WAV format

Always confirm the output file path after generation.
Save music to the '{args.output_dir}' directory.""",
        )

        generation_count = 0
        while True:
            try:
                user_input = input("\nYou: ").strip()
                if user_input.lower() in ["exit", "quit"]:
                    print("Goodbye!")
                    success = True
                    break
                if not user_input:
                    continue

                response = agent(user_input)
                print(f"\nAgent: {response}")
                generation_count += 1

            except KeyboardInterrupt:
                print("\nGoodbye!")
                success = True
                break

        if metrics:
            metrics.set_stats("generation_count", generation_count)

    elif args.weighted and args.prompts:
        # Weighted prompts mode
        weighted_prompts = []
        for p in args.prompts:
            if ":" in p:
                text, weight = p.rsplit(":", 1)
                weighted_prompts.append({"text": text, "weight": float(weight)})
            else:
                weighted_prompts.append({"text": p, "weight": 1.0})

        print(f"Generating music with weighted prompts...")
        for wp in weighted_prompts:
            print(f"  - {wp['text']} (weight: {wp['weight']})")
        print(f"Duration: {args.duration}s")

        result = generate_music_weighted(
            prompts=weighted_prompts,
            output_dir=args.output_dir,
            duration_seconds=args.duration,
        )

        if result["success"]:
            print(f"Success! Music saved to: {result['file_path']}")
            success = True
            if metrics:
                metrics.set_stats("operation", "weighted")
                metrics.set_stats("output_file", result["file_path"])
                metrics.set_stats("num_prompts", len(weighted_prompts))
                metrics.set_stats("duration_seconds", result["duration"])
        else:
            print(f"Error: {result.get('error', 'Unknown error')}")
            if metrics:
                metrics.set_stats("error", result.get("error"))

    else:
        # Single prompt mode
        print(f"Generating music...")
        print(f"Prompt: {args.prompt}")
        print(f"Duration: {args.duration}s")

        result = generate_music(
            prompt=args.prompt,
            output_dir=args.output_dir,
            duration_seconds=args.duration,
        )

        if result["success"]:
            print(f"Success! Music saved to: {result['file_path']}")
            success = True
            if metrics:
                metrics.set_stats("operation", "single_prompt")
                metrics.set_stats("output_file", result["file_path"])
                metrics.set_stats("prompt", args.prompt)
                metrics.set_stats("duration_seconds", result["duration"])
        else:
            print(f"Error: {result.get('error', 'Unknown error')}")
            if metrics:
                metrics.set_stats("error", result.get("error"))

    # Export metrics and record run
    if metrics:
        elapsed = time.time() - start_time
        metrics.set_timing("total_duration", elapsed)
        metrics_path = metrics.export()
        print(f"Metrics saved to: {metrics_path}")

    if registry:
        registry.record_run(agent_id=AGENT_ID, run_id=run_id, success=success)


if __name__ == "__main__":
    main()
