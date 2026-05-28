"""
Gemini Image Generation Tool

Supports both Gemini 2.5 Flash Image (fast) and Gemini 3 Pro Image Preview (advanced).
Features:
- Text-to-image generation
- Image editing (text-and-image-to-image)
- Multiple reference images (up to 14 for Gemini 3 Pro)
- Aspect ratio control
- Resolution control (1K, 2K, 4K for Gemini 3 Pro)
- Google Search grounding (Gemini 3 Pro)
"""

import os
from pathlib import Path
from datetime import datetime
from typing import Literal, Optional, List

from strands import tool
from google import genai
from google.genai import types


ImageModel = Literal["gemini-2.5-flash-image", "gemini-3-pro-image-preview"]
AspectRatio = Literal["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
ImageSize = Literal["1K", "2K", "4K"]


def _get_mime_type(path: Path) -> str:
    """Get MIME type from file extension."""
    suffix = path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return mime_map.get(suffix, "image/png")


def _load_image_part(image_path: str) -> types.Part:
    """Load an image file and return a Part object."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    with open(path, "rb") as f:
        image_bytes = f.read()

    mime_type = _get_mime_type(path)
    return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)


@tool
def generate_image(
    prompt: str,
    model: ImageModel = "gemini-3-pro-image-preview",
    aspect_ratio: Optional[AspectRatio] = None,
    image_size: Optional[ImageSize] = None,
    use_google_search: bool = False,
    reference_images: Optional[List[str]] = None,
    output_dir: str = "output",
) -> dict:
    """
    Generate an image using Gemini image models.

    Args:
        prompt: Text description of the image to generate. Be specific and descriptive.
        model: Model to use - "gemini-2.5-flash-image" (fast) or "gemini-3-pro-image-preview" (advanced, default).
        aspect_ratio: Output aspect ratio. Options: "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9".
        image_size: Resolution for Gemini 3 Pro only. Options: "1K" (default), "2K", "4K".
        use_google_search: Enable Google Search grounding for real-time info (Gemini 3 Pro only).
        reference_images: List of paths to reference images (up to 3 for Flash, up to 14 for Pro).
        output_dir: Directory to save the generated image (default: "output").

    Returns:
        dict with keys:
            - success: bool indicating if generation succeeded
            - file_path: path to saved image (if successful)
            - message: status message
            - text_response: any text from the model
            - error: error message (if failed)

    Examples:
        # Basic generation
        generate_image(prompt="A serene mountain landscape at sunset")

        # Fast generation with aspect ratio
        generate_image(prompt="A logo for a coffee shop", model="gemini-2.5-flash-image", aspect_ratio="1:1")

        # High-res with Google Search grounding
        generate_image(prompt="Current weather forecast for San Francisco as an infographic",
                      model="gemini-3-pro-image-preview", image_size="2K", use_google_search=True)

        # Style transfer with reference image
        generate_image(prompt="Transform this photo into Van Gogh's Starry Night style",
                      reference_images=["photo.png"])
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"success": False, "error": "GOOGLE_API_KEY environment variable not set"}

    # Validate model-specific features
    if model == "gemini-2.5-flash-image":
        if image_size:
            return {"success": False, "error": "image_size is only supported with gemini-3-pro-image-preview"}
        if use_google_search:
            return {"success": False, "error": "Google Search grounding is only supported with gemini-3-pro-image-preview"}
        if reference_images and len(reference_images) > 3:
            return {"success": False, "error": "gemini-2.5-flash-image supports up to 3 reference images"}

    if reference_images and len(reference_images) > 14:
        return {"success": False, "error": "Maximum 14 reference images supported"}

    try:
        client = genai.Client(api_key=api_key)

        # Build contents
        contents = []

        # Add reference images first if provided
        if reference_images:
            for img_path in reference_images:
                try:
                    contents.append(_load_image_part(img_path))
                except FileNotFoundError as e:
                    return {"success": False, "error": str(e)}

        # Add text prompt
        contents.append(prompt)

        # Build image config
        image_config = {}
        if aspect_ratio:
            image_config["aspect_ratio"] = aspect_ratio
        if image_size and model == "gemini-3-pro-image-preview":
            image_config["image_size"] = image_size

        # Build generation config
        config_kwargs = {
            "response_modalities": ["IMAGE", "TEXT"],
        }
        if image_config:
            config_kwargs["image_config"] = types.ImageConfig(**image_config)

        # Add Google Search tool if requested
        if use_google_search and model == "gemini-3-pro-image-preview":
            config_kwargs["tools"] = [{"google_search": {}}]

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        # Extract image from response
        image_data = None
        text_response = None

        if response.candidates:
            for part in response.candidates[0].content.parts:
                # Skip thought parts
                if hasattr(part, 'thought') and part.thought:
                    continue
                if hasattr(part, 'inline_data') and part.inline_data:
                    image_data = part.inline_data.data
                elif hasattr(part, 'text') and part.text:
                    text_response = part.text

        if not image_data:
            return {
                "success": False,
                "error": "No image data in response",
                "text_response": text_response
            }

        # Save image
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gemini_image_{timestamp}.png"
        file_path = output_path / filename

        with open(file_path, "wb") as f:
            f.write(image_data)

        return {
            "success": True,
            "file_path": str(file_path),
            "message": f"Image saved to {file_path}",
            "model": model,
            "text_response": text_response
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@tool
def edit_image(
    prompt: str,
    image_path: str,
    model: ImageModel = "gemini-3-pro-image-preview",
    aspect_ratio: Optional[AspectRatio] = None,
    image_size: Optional[ImageSize] = None,
    additional_images: Optional[List[str]] = None,
    output_dir: str = "output",
) -> dict:
    """
    Edit an existing image using Gemini image models.

    Supports various editing operations:
    - Add/remove/modify elements
    - Style transfer
    - Inpainting (semantic masking via description)
    - Combine multiple images

    Args:
        prompt: Text description of the edits to make. Be specific about what to change.
        image_path: Path to the primary input image to edit.
        model: Model to use - "gemini-2.5-flash-image" (fast) or "gemini-3-pro-image-preview" (advanced, default).
        aspect_ratio: Output aspect ratio. Options: "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9".
        image_size: Resolution for Gemini 3 Pro only. Options: "1K" (default), "2K", "4K".
        additional_images: Additional reference images for composition/style transfer.
        output_dir: Directory to save the edited image (default: "output").

    Returns:
        dict with keys:
            - success: bool indicating if edit succeeded
            - file_path: path to saved image (if successful)
            - message: status message
            - text_response: any text from the model
            - error: error message (if failed)

    Examples:
        # Simple edit
        edit_image(prompt="Add a wizard hat to the cat", image_path="cat.png")

        # Inpainting (semantic mask)
        edit_image(prompt="Change only the sofa to a brown leather chesterfield", image_path="living_room.png")

        # Style transfer
        edit_image(prompt="Transform into Van Gogh's Starry Night style", image_path="city.png")

        # Combine images (e.g., put dress on model)
        edit_image(prompt="Put the dress from the first image on the woman from the second",
                  image_path="dress.png", additional_images=["model.png"])
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"success": False, "error": "GOOGLE_API_KEY environment variable not set"}

    # Validate model-specific features
    if model == "gemini-2.5-flash-image":
        if image_size:
            return {"success": False, "error": "image_size is only supported with gemini-3-pro-image-preview"}

    try:
        client = genai.Client(api_key=api_key)

        # Build contents - images first, then prompt
        contents = []

        # Add primary image
        try:
            contents.append(_load_image_part(image_path))
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}

        # Add additional images if provided
        if additional_images:
            for img_path in additional_images:
                try:
                    contents.append(_load_image_part(img_path))
                except FileNotFoundError as e:
                    return {"success": False, "error": str(e)}

        # Add text prompt last
        contents.append(prompt)

        # Build image config
        image_config = {}
        if aspect_ratio:
            image_config["aspect_ratio"] = aspect_ratio
        if image_size and model == "gemini-3-pro-image-preview":
            image_config["image_size"] = image_size

        # Build generation config
        config_kwargs = {
            "response_modalities": ["IMAGE", "TEXT"],
        }
        if image_config:
            config_kwargs["image_config"] = types.ImageConfig(**image_config)

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        # Extract image from response
        image_data = None
        text_response = None

        if response.candidates:
            for part in response.candidates[0].content.parts:
                # Skip thought parts
                if hasattr(part, 'thought') and part.thought:
                    continue
                if hasattr(part, 'inline_data') and part.inline_data:
                    image_data = part.inline_data.data
                elif hasattr(part, 'text') and part.text:
                    text_response = part.text

        if not image_data:
            return {
                "success": False,
                "error": "No image data in response",
                "text_response": text_response
            }

        # Save image
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"gemini_edited_{timestamp}.png"
        file_path = output_path / filename

        with open(file_path, "wb") as f:
            f.write(image_data)

        return {
            "success": True,
            "file_path": str(file_path),
            "message": f"Edited image saved to {file_path}",
            "model": model,
            "text_response": text_response
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
