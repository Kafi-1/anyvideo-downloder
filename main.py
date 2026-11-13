import ipaddress, re, shutil, socket, tempfile, time, uuid
from pathlib import Path
from urllib.parse import urlparse
import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
try:
    from pytubefix import YouTube
except Exception:
    YouTube = None

app = FastAPI(title="AnyVideo API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "anyvideo"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
TOKENS = {}
TOKEN_TTL = 1800

class URLRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
class DownloadRequest(URLRequest):
    format_id: str = Field(default="best", max_length=200)
    type: str = Field(default="video", pattern="^(video|audio)$")

def validate_url(raw):
    value = raw.strip(); parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "Provide a valid public HTTP or HTTPS URL.")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise HTTPException(400, "Local network URLs are not supported.")
    try:
        for item in socket.getaddrinfo(host, None):
            if not ipaddress.ip_address(item[4][0]).is_global:
                raise HTTPException(400, "Private or local network URLs are not allowed.")
    except socket.gaierror:
        raise HTTPException(400, "URL host could not be resolved.")
    return value

def ydl_opts(extra=None):
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20, "retries": 2, "extractor_retries": 1, "restrictfilenames": True, "cachedir": False}
    if extra: opts.update(extra)
    return opts

def pytube_info(url):
    host = (urlparse(url).hostname or "").lower()
    if YouTube is None or not (host == "youtu.be" or host.endswith("youtube.com")):
        raise RuntimeError("YouTube fallback unavailable")
    yt = YouTube(url)
    streams = yt.streams.filter(progressive=True).order_by("resolution").desc()
    formats = [{"format_id": f"ptf:{s.itag}", "ext": s.subtype or "mp4", "resolution": s.resolution or "unknown", "type": "video"} for s in streams]
    if not formats: raise RuntimeError("No compatible stream")
    return {"_engine": "pytubefix", "_yt": yt, "title": yt.title or "video", "thumbnail": yt.thumbnail_url, "duration": yt.length, "uploader": yt.author or "", "formats": formats}

def get_info(url):
    try:
        with yt_dlp.YoutubeDL(ydl_opts({"skip_download": True})) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as first:
        try: return pytube_info(url)
        except Exception:
            raise HTTPException(422, "Could not read this video. It may be private, restricted, unsupported, or temporarily unavailable.") from first

def formats_for(info):
    if info.get("_engine") == "pytubefix": return info["formats"]
    heights = set()
    for f in info.get("formats") or []:
        if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none") and f.get("height"):
            heights.add(int(f["height"]))
    result = [{"format_id": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]", "ext": "mp4", "resolution": f"{h}p", "type": "video"} for h in sorted(heights, reverse=True)]
    if not result: result.append({"format_id": "best", "ext": "mp4", "resolution": "Best available", "type": "video"})
    result.append({"format_id": "bestaudio/best", "ext": "m4a", "resolution": "Audio", "type": "audio"})
    return result

@app.get("/")
def root(): return {"name": "AnyVideo API", "status": "running", "docs": "/docs"}
@app.get("/health")
def health(): return {"ok": True}
@app.post("/api/info")
def info(body: URLRequest):
    data = get_info(validate_url(body.url))
    return {"title": data.get("title") or "Untitled video", "thumbnail": data.get("thumbnail") or "", "duration": data.get("duration") or 0, "uploader": data.get("uploader") or data.get("channel") or "", "formats": formats_for(data)}

def cleanup():
    now = time.time()
    for token, item in list(TOKENS.items()):
        if item["expires"] < now:
            Path(item["path"]).unlink(missing_ok=True); shutil.rmtree(item["folder"], ignore_errors=True); TOKENS.pop(token, None)

@app.post("/api/download")
def download(body: DownloadRequest):
    url = validate_url(body.url); cleanup()
    folder = Path(tempfile.mkdtemp(prefix="job_", dir=DOWNLOAD_DIR))
    try:
        if body.format_id.startswith("ptf:"):
            data = pytube_info(url); stream = data["_yt"].streams.get_by_itag(int(body.format_id.split(":", 1)[1]))
            if stream is None: raise RuntimeError("Stream unavailable")
            output = Path(stream.download(output_path=str(folder), filename="download"))
        else:
            fmt = "bestaudio/best" if body.type == "audio" else (body.format_id or "best")
            with yt_dlp.YoutubeDL(ydl_opts({"format": fmt, "outtmpl": str(folder / "%(title).80B.%(ext)s"), "merge_output_format": "mp4", "format_sort": ["res", "ext:mp4:m4a"]})) as ydl:
                ydl.download([url])
            found = [p for p in folder.iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
            if not found: raise RuntimeError("No output file")
            output = max(found, key=lambda p: p.stat().st_size)
        if not output.is_file() or output.stat().st_size == 0: raise RuntimeError("Empty output")
        token = uuid.uuid4().hex
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", output.stem).strip("._")[:100] or "video"
        filename += output.suffix or ".mp4"
        TOKENS[token] = {"path": str(output), "folder": str(folder), "filename": filename, "expires": time.time() + TOKEN_TTL}
        return {"download_url": f"/api/file/{token}", "filename": filename}
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True); raise
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, "Download failed. The source may block automated access or the selected format may be unavailable.") from exc

@app.get("/api/file/{token}")
def file(token: str):
    cleanup(); item = TOKENS.get(token)
    if not item: raise HTTPException(404, "Download link expired. Please try again.")
    path = Path(item["path"])
    if not path.is_file(): raise HTTPException(404, "File is no longer available.")
    return FileResponse(path, filename=item["filename"], media_type="application/octet-stream")
