import os
import re
import uuid
import json
import time
import shutil
import threading
from pathlib import Path
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_from_directory, session, redirect, render_template
from werkzeug.middleware.proxy_fix import ProxyFix
import yt_dlp

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-2026")
BASE_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/tmp/china_downloader_downloads"))
BASE_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=".", static_folder=".", static_url_path="")
app.secret_key = SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

jobs = {}
lock = threading.Lock()
URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)


def clean_old_files(max_age_hours=12):
    cutoff = time.time() - max_age_hours * 3600
    for p in BASE_DIR.glob("*"):
        try:
            if p.stat().st_mtime < cutoff:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
        except Exception:
            pass


def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if APP_PASSWORD and not session.get("ok"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
            return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper


def extract_urls(text):
    urls = []
    seen = set()
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(".,);]\n\r\t")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def safe_name(name):
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name or "video")
    return name[:120].strip(" ._") or "video"


def job_update(job_id, **data):
    with lock:
        if job_id in jobs:
            jobs[job_id].update(data)


def run_download(job_id, urls, mode):
    clean_old_files()
    job_dir = BASE_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    total = len(urls)
    results = []

    def progress_hook(d):
        status = d.get("status")
        if status == "downloading":
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            percent = int(downloaded * 100 / total_bytes) if total_bytes else 0
            speed = d.get("speed") or 0
            eta = d.get("eta")
            job_update(job_id, progress=max(1, min(percent, 99)), speed=int(speed), eta=eta)
        elif status == "finished":
            job_update(job_id, progress=99)

    for idx, url in enumerate(urls, start=1):
        job_update(job_id, status="running", current=idx, total=total, message=f"{idx}/{total}")
        before = set(job_dir.glob("*"))
        try:
            ydl_opts = {
                "outtmpl": str(job_dir / "%(title).120s_%(id)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [progress_hook],
                "restrictfilenames": False,
                "windowsfilenames": True,
                "retries": 2,
                "fragment_retries": 2,
                "socket_timeout": 30,
            }
            if mode == "audio":
                ydl_opts.update({
                    "format": "bestaudio/best",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                })
            else:
                ydl_opts.update({
                    "format": "bv*+ba/best",
                    "merge_output_format": "mp4",
                })

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            after = set(job_dir.glob("*"))
            new_files = [p for p in after - before if p.is_file() and not p.name.endswith((".part", ".ytdl", ".temp"))]
            if not new_files:
                new_files = sorted([p for p in job_dir.glob("*") if p.is_file() and not p.name.endswith((".part", ".ytdl", ".temp"))], key=lambda p: p.stat().st_mtime, reverse=True)[:1]
            for p in new_files:
                nice = safe_name(p.name)
                if p.name != nice:
                    target = p.with_name(nice)
                    try:
                        p.rename(target)
                        p = target
                    except Exception:
                        pass
                results.append({"name": p.name, "url": f"/file/{job_id}/{p.name}", "size": p.stat().st_size})
            job_update(job_id, progress=int(idx * 100 / total), files=results)
        except Exception as e:
            results.append({"name": "실패", "url": "", "size": 0, "error": str(e)[:260], "source": url})
            job_update(job_id, files=results)

    job_update(job_id, status="done", progress=100, message="완료", files=results)


@app.get("/login")
def login_page():
    if not APP_PASSWORD or session.get("ok"):
        return redirect("/")
    return render_template("login.html")


@app.post("/login")
def login_post():
    if not APP_PASSWORD:
        session["ok"] = True
        return redirect("/")
    password = request.form.get("password", "")
    if password == APP_PASSWORD:
        session["ok"] = True
        return redirect("/")
    return render_template("login.html", error="비밀번호가 맞지 않습니다."), 401


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login" if APP_PASSWORD else "/")


@app.get("/")
@auth_required
def index():
    return render_template("index.html")


@app.post("/api/download")
@auth_required
def api_download():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    mode = data.get("mode", "video")
    if mode not in {"video", "audio"}:
        mode = "video"
    urls = extract_urls(text)
    if not urls:
        return jsonify({"ok": False, "error": "링크가 없습니다."}), 400
    urls = urls[:20]
    job_id = uuid.uuid4().hex[:12]
    with lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "current": 0,
            "total": len(urls),
            "message": "대기",
            "files": [],
            "speed": 0,
            "eta": None,
            "created": time.time(),
        }
    t = threading.Thread(target=run_download, args=(job_id, urls, mode), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})


@app.get("/api/job/<job_id>")
@auth_required
def api_job(job_id):
    with lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "작업을 찾을 수 없습니다."}), 404
    return jsonify({"ok": True, "job": job})


@app.get("/file/<job_id>/<path:filename>")
@auth_required
def get_file(job_id, filename):
    return send_from_directory(BASE_DIR / job_id, filename, as_attachment=True)


@app.get("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
