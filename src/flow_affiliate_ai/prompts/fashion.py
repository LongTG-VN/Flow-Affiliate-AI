from dataclasses import dataclass


EXTRACT_PRODUCT_PROMPT = """Extract only the clothing product from the provided product image.
Remove the mannequin, person, background, and all unrelated objects.
Keep the garment design, color, pattern, proportions, and visible details accurate.
Show the product alone, centered, front-facing, on a clean background."""


WEAR_PRODUCT_PROMPT = """Create a realistic full-body image using the provided character image and clothing reference image.
Use the character image only for the woman's face, hairstyle, body proportions, skin tone, and overall appearance.
Ignore the original clothing in the character image and replace it completely.
Use only the dress from the clothing reference image as the final outfit.
Natural elegant standing pose, slight three-quarter angle, soft indoor lighting, clean background, photorealistic fashion image."""


CHARACTER_VIDEO_LEVELS = {
    3: """Create a realistic 10-second vertical video from the provided image.
The woman takes two slow graceful steps forward, gently adjusts her hair, and turns slightly toward the camera.
Smooth full-body shot, soft daylight, natural movement.""",
    2: """Create a realistic vertical video from the provided image.
The woman takes one slow step and gently adjusts her hair.
Smooth full-body shot, soft daylight, natural movement.""",
    1: """The woman slowly turns toward the camera.
Smooth full-body shot, natural movement.""",
}


PRODUCT_VIDEO_PROMPTS = {
    "zoom": """The camera slowly moves closer from a full product view to a medium close-up of the dress.
Smooth product shot, soft lighting, realistic fabric details.""",
    "pan": """The camera slowly pans from the collar down along the dress to the hem.
Smooth product shot, soft lighting, realistic fabric details.""",
}


DEFAULT_VOICE_SCRIPT = (
    "Mẫu này lên hình nhìn rất gọn và nữ tính. "
    "Mình để thêm một đoạn cận cảnh để mọi người nhìn rõ thiết kế hơn. "
    "Nếu thích phong cách nhẹ nhàng như vậy thì có thể xem thêm thông tin sản phẩm ở phần gắn kèm nha."
)


@dataclass(frozen=True)
class PromptAttempt:
    level: int
    prompt: str


def character_video_attempt(level: int = 3) -> PromptAttempt:
    if level not in CHARACTER_VIDEO_LEVELS:
        raise ValueError("character video level must be 1, 2, or 3")
    return PromptAttempt(level=level, prompt=CHARACTER_VIDEO_LEVELS[level])


def fallback_level(current_level: int) -> int | None:
    if current_level == 3:
        return 2
    if current_level == 2:
        return 1
    return None
