# AnyVideo Backend

FastAPI backend for the AnyVideo downloader frontend.

## Engines
- `yt-dlp` is the primary extractor for many publicly accessible video platforms.
- `pytubefix` is a YouTube-only fallback when yt-dlp extraction fails.
- FFmpeg is installed in the Docker image to merge separate audio/video streams.

No extractor guarantees every URL. Private, DRM-protected, login-restricted, age-restricted, or otherwise access-controlled media is not supported. Only download content you have permission to save.

## Koyeb deployment
1. Connect this GitHub repository.
2. Select **Dockerfile** as the builder.
3. Expose HTTP port **8000**.
4. Deploy. The container listens on `$PORT` (default 8000).

## API
- `GET /` — service status
- `GET /health` — health check
- `POST /api/info` — JSON body: `{"url":"https://..."}`
- `POST /api/download` — JSON body: `{"url":"https://...","format_id":"best","type":"video"}`
- `GET /api/file/{token}` — streams the file; token expires after 30 minutes

The download endpoint returns a relative `download_url`. When the frontend is hosted separately, prepend the backend origin to that path.

## Local run
Requires Python 3.11+ and FFmpeg.

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `/docs` for interactive API documentation.

## Limitations
Downloads consume server CPU, RAM, disk and request time. Free-tier limits may interrupt large downloads. The in-memory token store is intended for one running instance and modest testing, not high-volume production traffic.
