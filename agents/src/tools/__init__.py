"""Agent tools for model selection, recommendations, and media generation."""

from .model_selector import (
    get_available_models,
    get_model_recommendation,
    compare_models
)

from .code_reader import (
    grab_code,
)

from .gemini_image import (
    generate_image,
    edit_image,
)

from .gemini_video import (
    generate_video,
    generate_video_from_image,
)

from .gemini_music import (
    generate_music,
    generate_music_weighted,
)

from .carbon_image import (
    generate_code_image,
    generate_code_image_from_file,
    list_carbon_themes,
)

from .ffmpeg_video import (
    cut_video,
    concat_videos,
    get_video_info,
    extract_audio,
)

__all__ = [
    # Model selector tools
    "get_available_models",
    "get_model_recommendation",
    "compare_models",
    # Code reader tools
    "grab_code",
    # Carbon image tools
    "generate_code_image",
    "generate_code_image_from_file",
    "list_carbon_themes",
    # Gemini image tools
    "generate_image",
    "edit_image",
    # Gemini video tools
    "generate_video",
    "generate_video_from_image",
    # Gemini music tools
    "generate_music",
    "generate_music_weighted",
    # FFmpeg video tools
    "cut_video",
    "concat_videos",
    "get_video_info",
    "extract_audio",
]

