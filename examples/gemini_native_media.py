"""
Examples: Native Gemini media (no Strands Agent loop).

Requires:
- GOOGLE_API_KEY in environment or .env
- Pillow installed (for image save)

Run:
  cd examples
  python gemini_native_media.py image --prompt "A nano banana dish in a fancy restaurant with a Gemini theme"
  python gemini_native_media.py caption --image ./path/to/image.jpg --prompt "Caption this image."
  python gemini_native_media.py video --prompt "A close up of two people staring at a cryptic drawing..."
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make repo root importable so `from src...` works when running from `examples/`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from src.tools.gemini_media import generate_image, caption_image, generate_video


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Native Gemini media examples.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_img = sub.add_parser("image", help="Generate an image")
    p_img.add_argument("--prompt", required=True)
    p_img.add_argument("--model", default="gemini-3-pro-image-preview")
    p_img.add_argument("--out", default="output/generated_image.png")

    p_cap = sub.add_parser("caption", help="Caption an existing image")
    p_cap.add_argument("--image", required=True)
    p_cap.add_argument("--prompt", default="Caption this image.")
    p_cap.add_argument("--model", default="gemini-3-flash-preview")

    p_vid = sub.add_parser("video", help="Generate a video (Veo)")
    p_vid.add_argument("--prompt", required=True)
    p_vid.add_argument("--model", default="veo-3.1-generate-preview")
    p_vid.add_argument("--out", default="output/generated_video.mp4")
    p_vid.add_argument("--poll-seconds", type=int, default=10)
    p_vid.add_argument("--timeout-seconds", type=int, default=600)

    args = parser.parse_args()

    if args.cmd == "image":
        out_path = Path("examples") / args.out if not str(args.out).startswith("examples/") else Path(args.out)
        p = generate_image(args.prompt, model=args.model, out_path=out_path)
        print(f"Saved image -> {p}")
        return 0

    if args.cmd == "caption":
        text = caption_image(Path(args.image), prompt=args.prompt, model=args.model)
        print(text)
        return 0

    if args.cmd == "video":
        out_path = Path("examples") / args.out if not str(args.out).startswith("examples/") else Path(args.out)
        p = generate_video(
            args.prompt,
            model=args.model,
            out_path=out_path,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
        )
        print(f"Saved video -> {p}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())


