# OpenAI Image Generator API

FastAPI server that accepts a text prompt, generates an image with the OpenAI Images API, and returns an image link.

Current GPT image models return base64 image data, so this server stores the PNG locally in `generated_images/` and returns a URL from the `/generated/{file}` static route. If a selected model returns a URL directly, the API passes that URL through.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set:

```env
OPENAI_API_KEY=your_openai_api_key_here
API_KEYS=changeme-generate-a-strong-key
```

## Authentication

The `/generate-image`, `/upscale-image`, and `/edit-image` endpoints require a gateway API key. Set one or more keys in `API_KEYS` (comma-separated to issue a key per client), and send the key in the `X-API-Key` header on every request. Requests without a valid key get `401 Unauthorized`. The `/health` endpoint and the `/generated` and `/upscaled` image routes stay public.

Generate a strong key, for example:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Run

```powershell
uvicorn app.main:app --reload
```

The API will be available at:

```text
http://127.0.0.1:8000
```

## Generate An Image

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/generate-image `
  -ContentType "application/json" `
  -Headers @{ "X-API-Key" = "changeme-generate-a-strong-key" } `
  -Body '{"prompt":"A clean product photo of a matte black smart speaker on a white desk"}'
```

Example response:

```json
{
  "image_url": "http://127.0.0.1:8000/generated/abc123.png",
  "model": "gpt-image-1.5",
  "saved": true
}
```

Open the returned `image_url` in a browser to view the generated image.

## Edit An Image

`POST /edit-image` accepts multipart form data and edits an uploaded image with the OpenAI Images API. It returns the edited image as a base64 data URL, ready to drop straight onto a canvas (no CORS to handle).

Form fields:

- `image` — the uploaded file (PNG, JPG, JPEG, or WEBP). Required.
- `prompt` — free-text edit instruction. Required unless `action` is given.
- `action` — optional preset: `remove_background`, `transparent_logo`, or `enhance`. The `remove_background` and `transparent_logo` presets produce a transparent background. Combine with `prompt` to add extra instructions.
- `size` — optional output size, defaults to `1024x1024`.

Remove a background:

```powershell
curl.exe -X POST `
  -H "X-API-Key: changeme-generate-a-strong-key" `
  -F "image=@C:\path\to\product.png" `
  -F "action=remove_background" `
  http://127.0.0.1:8000/edit-image
```

Edit with a custom prompt:

```powershell
curl.exe -X POST `
  -H "X-API-Key: changeme-generate-a-strong-key" `
  -F "image=@C:\path\to\product.png" `
  -F "prompt=Place the product on a marble countertop with soft daylight" `
  http://127.0.0.1:8000/edit-image
```

Example response:

```json
{
  "images": [
    {
      "dataUrl": "data:image/png;base64,iVBORw0KGgo...",
      "name": "Edited image"
    }
  ]
}
```

## Upscale An Image With Real-ESRGAN

This project uses the `realesrgan-ncnn-vulkan` Real-ESRGAN executable for local 2x and 4x image upscaling.

1. Download Real-ESRGAN ncnn Vulkan for Windows from the Real-ESRGAN releases page:

```text
https://github.com/xinntao/Real-ESRGAN/releases
```

2. Extract it somewhere on your machine.

3. Set the executable path in `.env`:

```env
REAL_ESRGAN_EXECUTABLE=C:\path\to\realesrgan-ncnn-vulkan.exe
REAL_ESRGAN_MODEL=realesrgan-x4plus
REAL_ESRGAN_TIMEOUT_SECONDS=900
```

If the `models` folder is not next to the executable, also set:

```env
REAL_ESRGAN_MODELS_DIR=C:\path\to\models
```

For large images or slower machines, increase the timeout:

```env
REAL_ESRGAN_TIMEOUT_SECONDS=1800
```

If the process fails because of GPU memory, try a tile size:

```env
REAL_ESRGAN_TILE_SIZE=256
```

Run the server:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Upload and upscale an image by 2x:

```powershell
curl.exe -X POST `
  -H "X-API-Key: changeme-generate-a-strong-key" `
  -F "image=@C:\path\to\input.png" `
  -F "scale=2" `
  http://127.0.0.1:8000/upscale-image
```

Upload and upscale an image by 4x:

```powershell
curl.exe -X POST `
  -H "X-API-Key: changeme-generate-a-strong-key" `
  -F "image=@C:\path\to\input.png" `
  -F "scale=4" `
  http://127.0.0.1:8000/upscale-image
```

Example response:

```json
{
  "image_url": "http://127.0.0.1:8000/upscaled/abc123.png",
  "scale": 4,
  "model": "realesrgan-x4plus",
  "saved": true
}
```
