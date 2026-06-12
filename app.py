import os
import re
import time
import uuid
import shutil
import threading
from pathlib import Path
from typing import Dict, Any, List

from flask import Flask, jsonify, render_template, request, send_from_directory, session, redirect, url_for
from yt_dlp import YoutubeDL

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(24).hex())
BASE_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/tmp/china_social_downloads"))
MAX_LINKS_PER_JOB = int(os.environ.get("MAX_LINKS_PER_JOB", "5"))
MAX_AGE_SECONDS = int(os.environ.get("MAX_AGE_SECONDS", "7200"))

BASE_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = SECRET_KEY

jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()

URL_RE = re.compile(r"https?://[^\s\n\r\t]+", re.IGNORECASE)


def is_authed() -> bool:
    if not APP_PASSWORD:
        return True
    return session.get("authed") is True


def require_auth_json():
    if not is_authed():
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    return None


def extract_urls(text: str) -> List[str]:
    raw = URL_RE.findall(text or "")
    cleaned = []
    for u in raw:
        u = u.strip().strip('"\'<>').rstrip('.,;)》】]')
        if u not in cleaned:
            cleaned.append(u)
    return cleaned[:MAX_LINKS_PER_JOB]


def human_bytes(value):
    if not value:
        return ""
    units = ["B", "KB", "MB", "GB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024


def update_job(job_id: str, **kwargs):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(kwargs)
            jobs[job_id]["updated_at"] = time.time()


def add_log(job_id: str, msg: str):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].setdefault("logs", []).append(msg)
            jobs[job_id]["logs"] = jobs[job_id]["logs"][-30:]
            jobs[job_id]["updated_at"] = time.time()


def cleanup_old_files():
    now = time.time()
    for p in BASE_DIR.iterdir():
        try:
            if p.is_dir() and now - p.stat().st_mtime > MAX_AGE_SECONDS:
                shutil.rmtree(p, ignore_errors=True)
        except FileNotFoundError:
            pass

    with jobs_lock:
        old_ids = [jid for jid, j in jobs.items() if now - j.get("created_at", now) > MAX_AGE_SECONDS]
        for jid in old_ids:
            jobs.pop(jid, None)


def collect_files(job_dir: Path, job_id: str) -> List[Dict[str, str]]:
    files = []
    if not job_dir.exists():
        return files
    for f in sorted(job_dir.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if not f.is_file():
            continue
        if f.suffix.lower() in {".part", ".ytdl", ".tmp", ".webp", ".jpg", ".json"}:
            continue
        files.append({
            "name": f.name,
            "size": human_bytes(f.stat().st_size),
            "url": f"/file/{job_id}/{f.name}",
        })
    return files


def run_download(job_id: str, urls: List[str], mode: str):
    job_dir = BASE_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    cleanup_old_files()

    def hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = int(done * 100 / total) if total else 0
            filename = Path(d.get("filename", "")).name
            speed = human_bytes(d.get("speed")) + "/s" if d.get("speed") else ""
            eta = d.get("eta")
            update_job(
                job_id,
                status="downloading",
                progress=pct,
                filename=filename,
                speed=speed,
                eta=eta,
            )
        elif status == "finished":
            update_job(job_id, status="processing", progress=95, filename=Path(d.get("filename", "")).name)

    try:
        update_job(job_id, status="starting", progress=1)
        add_log(job_id, f"링크 {len(urls)}개 감지")

        common_opts = {
            "outtmpl": str(job_dir / "%(title).120s_%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "progress_hooks": [hook],
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
            "concurrent_fragment_downloads": 2,
            "windowsfilenames": True,
        }

        if mode == "audio":
            ydl_opts = {
                **common_opts,
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            }
        else:
            ydl_opts = {
                **common_opts,
                "format": "bv*+ba/best[ext=mp4]/best",
                "merge_output_format": "mp4",
            }

        with YoutubeDL(ydl_opts) as ydl:
            for idx, url in enumerate(urls, start=1):
                add_log(job_id, f"{idx}/{len(urls)} 다운로드 시작")
                update_job(job_id, current_url=url)
                ydl.download([url])

        files = collect_files(job_dir, job_id)
        if not files:
            raise RuntimeError("저장된 파일을 찾지 못했습니다. 사이트 구조 변경, 비공개 링크, 로그인 필요 링크일 수 있습니다.")
        update_job(job_id, status="done", progress=100, files=files, filename="")
        add_log(job_id, "완료")
    except Exception as e:
        update_job(job_id, status="error", error=str(e), progress=0, files=collect_files(job_dir, job_id))
        add_log(job_id, f"오류: {e}")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD:
        return redirect(url_for("index"))
    error = ""
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == APP_PASSWORD:
            session["authed"] = True
            return redirect(url_for("index"))
        error = "비밀번호가 맞지 않습니다."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET"])
def index():
    if not is_authed():
        return redirect(url_for("login"))
    prefill = request.args.get("text", "")
    return render_template("index.html", prefill=prefill, password_enabled=bool(APP_PASSWORD))


@app.route("/share", methods=["GET", "POST"])
def share():
    if not is_authed():
        return redirect(url_for("login"))
    text = ""
    if request.method == "POST":
        text = request.form.get("text") or request.form.get("url") or request.form.get("title") or ""
    else:
        text = request.args.get("text") or request.args.get("url") or ""
    return render_template("index.html", prefill=text, password_enabled=bool(APP_PASSWORD))


@app.route("/api/start", methods=["POST"])
def api_start():
    auth = require_auth_json()
    if auth:
        return auth
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    mode = data.get("mode", "video")
    urls = extract_urls(text)
    if not urls:
        return jsonify({"ok": False, "error": "링크가 없습니다. 공유 문구 전체를 붙여넣어도 됩니다."}), 400

    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "files": [],
            "logs": [],
            "created_at": time.time(),
            "updated_at": time.time(),
            "mode": mode,
        }

    t = threading.Thread(target=run_download, args=(job_id, urls, mode), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    auth = require_auth_json()
    if auth:
        return auth
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "작업을 찾을 수 없습니다."}), 404
        return jsonify({"ok": True, "job": job})


@app.route("/file/<job_id>/<path:filename>")
def file(job_id, filename):
    if not is_authed():
        return redirect(url_for("login"))
    job_dir = BASE_DIR / job_id
    safe = Path(filename).name
    return send_from_directory(job_dir, safe, as_attachment=True)


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
