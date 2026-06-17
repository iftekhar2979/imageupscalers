import base64
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


load_dotenv()

IMAGE_DIR = Path(os.getenv("GENERATED_IMAGE_DIR", "generated_images"))
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

UPSCALED_IMAGE_DIR = Path(os.getenv("UPSCALED_IMAGE_DIR", "upscaled_images"))
UPSCALED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "auto"
REAL_ESRGAN_EXECUTABLE = os.getenv("REAL_ESRGAN_EXECUTABLE", "realesrgan-ncnn-vulkan")
REAL_ESRGAN_MODEL = os.getenv("REAL_ESRGAN_MODEL", "realesrgan-x4plus")
REAL_ESRGAN_MODELS_DIR = os.getenv("REAL_ESRGAN_MODELS_DIR")
REAL_ESRGAN_TIMEOUT_SECONDS = int(os.getenv("REAL_ESRGAN_TIMEOUT_SECONDS", "900"))
REAL_ESRGAN_TILE_SIZE = os.getenv("REAL_ESRGAN_TILE_SIZE")
ALLOWED_UPLOAD_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

EDIT_ACTION_PROMPTS = {
    "remove_background": (
        "Remove the background entirely, keeping only the main subject with clean, precise edges."
    ),
    "transparent_logo": (
        "Isolate the logo and remove everything else, producing a clean logo on a transparent background."
    ),
    "enhance": (
        "Enhance the image: improve sharpness, lighting, and detail without changing the subject or composition."
    ),
}
EDIT_TRANSPARENT_ACTIONS = {"remove_background", "transparent_logo"}

API_KEY_HEADER_NAME = "X-API-Key"
API_KEYS = {
    key.strip()
    for key in os.getenv("API_KEYS", "").split(",")
    if key.strip()
}

api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)

CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
    if origin.strip()
]

RATE_LIMIT = os.getenv("RATE_LIMIT", "20/minute")


def require_api_key(provided_key: str | None = Depends(api_key_header)) -> None:
    if not API_KEYS:
        raise HTTPException(
            status_code=500,
            detail="API_KEYS is not configured on the server.",
        )

    if not provided_key or not any(
        secrets.compare_digest(provided_key, allowed) for allowed in API_KEYS
    ):
        raise HTTPException(
            status_code=401,
            detail=f"Missing or invalid {API_KEY_HEADER_NAME} header.",
        )


def rate_limit_key(request: Request) -> str:
    return request.headers.get(API_KEY_HEADER_NAME) or get_remote_address(request)


limiter = Limiter(key_func=rate_limit_key)

app = FastAPI(title="OpenAI Image Generator API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/generated", StaticFiles(directory=str(IMAGE_DIR)), name="generated")
app.mount("/upscaled", StaticFiles(directory=str(UPSCALED_IMAGE_DIR)), name="upscaled")


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=32000)
    model: str | None = Field(default=None, examples=["gpt-image-1.5"])
    size: str = Field(default=DEFAULT_SIZE, examples=["1024x1024"])
    quality: str = Field(default=DEFAULT_QUALITY, examples=["auto", "low", "medium", "high"])


class ImageGenerateResponse(BaseModel):
    image_url: str
    model: str
    saved: bool


class ImageUpscaleResponse(BaseModel):
    image_url: str
    scale: int
    model: str
    saved: bool


class EditedImage(BaseModel):
    dataUrl: str
    name: str


class ImageEditResponse(BaseModel):
    images: list[EditedImage]


def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=api_key)


def resolve_realesrgan_executable() -> str:
    configured_path = Path(REAL_ESRGAN_EXECUTABLE)
    if configured_path.exists():
        return str(configured_path)

    executable = shutil.which(REAL_ESRGAN_EXECUTABLE)
    if executable:
        return executable

    if not REAL_ESRGAN_EXECUTABLE.lower().endswith(".exe"):
        executable = shutil.which(f"{REAL_ESRGAN_EXECUTABLE}.exe")
        if executable:
            return executable

    raise HTTPException(
        status_code=500,
        detail=(
            "Real-ESRGAN executable was not found. Set REAL_ESRGAN_EXECUTABLE "
            "to the full path of realesrgan-ncnn-vulkan.exe."
        ),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/generate-image",
    response_model=ImageGenerateResponse,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit(RATE_LIMIT)
def generate_image(payload: ImageGenerateRequest, request: Request) -> ImageGenerateResponse:
    model = payload.model or DEFAULT_MODEL
    openai_client = get_openai_client()

    try:
        result = openai_client.images.generate(
            model=model,
            prompt=payload.prompt,
            size=payload.size,
            quality=payload.quality,
            n=1,
        )
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    image = result.data[0]

    if image.url:
        return ImageGenerateResponse(image_url=image.url, model=model, saved=False)

    if not image.b64_json:
        raise HTTPException(status_code=502, detail="OpenAI returned no image data.")

    filename = f"{uuid4().hex}.png"
    output_path = IMAGE_DIR / filename
    output_path.write_bytes(base64.b64decode(image.b64_json))

    return ImageGenerateResponse(
        image_url=str(request.url_for("generated", path=filename)),
        model=model,
        saved=True,
    )


@app.post(
    "/upscale-image",
    response_model=ImageUpscaleResponse,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit(RATE_LIMIT)
async def upscale_image(
    request: Request,
    image: UploadFile = File(...),
    scale: int = Form(..., ge=2, le=4),
) -> ImageUpscaleResponse:
    if scale not in {2, 4}:
        raise HTTPException(status_code=400, detail="Scale must be either 2 or 4.")

    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG, JPEG, or WEBP image.")

    executable = resolve_realesrgan_executable()
    input_path = UPSCALED_IMAGE_DIR / f"{uuid4().hex}{suffix}"
    output_filename = f"{uuid4().hex}.png"
    output_path = UPSCALED_IMAGE_DIR / output_filename
    upload_bytes = await image.read()

    if not upload_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty.")

    try:
        input_path.write_bytes(upload_bytes)

        command = [
            executable,
            "-i",
            str(input_path),
            "-o",
            str(output_path),
            "-s",
            str(scale),
            "-n",
            REAL_ESRGAN_MODEL,
            "-f",
            "png",
        ]

        if REAL_ESRGAN_MODELS_DIR:
            command.extend(["-m", REAL_ESRGAN_MODELS_DIR])

        if REAL_ESRGAN_TILE_SIZE:
            command.extend(["-t", REAL_ESRGAN_TILE_SIZE])

        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=REAL_ESRGAN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Real-ESRGAN timed out after {REAL_ESRGAN_TIMEOUT_SECONDS} seconds. "
                "Try a smaller image, use scale=2, or increase REAL_ESRGAN_TIMEOUT_SECONDS."
            ),
        ) from exc
    finally:
        input_path.unlink(missing_ok=True)

    if completed.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"Real-ESRGAN failed: {completed.stderr or completed.stdout}",
        )

    if not output_path.exists():
        raise HTTPException(status_code=502, detail="Real-ESRGAN did not create an output image.")

    return ImageUpscaleResponse(
        image_url=str(request.url_for("upscaled", path=output_filename)),
        scale=scale,
        model=REAL_ESRGAN_MODEL,
        saved=True,
    )


@app.post(
    "/edit-image",
    response_model=ImageEditResponse,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit(RATE_LIMIT)
async def edit_image(
    request: Request,
    image: UploadFile = File(...),
    prompt: str = Form(""),
    action: str | None = Form(None),
    size: str = Form(DEFAULT_SIZE),
) -> ImageEditResponse:
    if action is not None and action not in EDIT_ACTION_PROMPTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action. Choose one of: {', '.join(sorted(EDIT_ACTION_PROMPTS))}.",
        )

    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="Upload a PNG, JPG, JPEG, or WEBP image.")

    prompt_parts = []
    if action:
        prompt_parts.append(EDIT_ACTION_PROMPTS[action])
    if prompt.strip():
        prompt_parts.append(prompt.strip())
    if not prompt_parts:
        raise HTTPException(status_code=400, detail="Provide a prompt or an action.")
    effective_prompt = " ".join(prompt_parts)

    upload_bytes = await image.read()
    if not upload_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty.")

    edit_kwargs = {
        "model": DEFAULT_MODEL,
        "image": (image.filename or f"upload{suffix}", upload_bytes),
        "prompt": effective_prompt,
        "size": size,
        "n": 1,
    }
    if action in EDIT_TRANSPARENT_ACTIONS:
        edit_kwargs["background"] = "transparent"

    openai_client = get_openai_client()

    try:
        result = openai_client.images.edit(**edit_kwargs)
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    edited = result.data[0]
    if not edited.b64_json:
        raise HTTPException(status_code=502, detail="OpenAI returned no image data.")

    return ImageEditResponse(
        images=[
            EditedImage(
                dataUrl=f"data:image/png;base64,{edited.b64_json}",
                name="Edited image",
            )
        ]
    )
