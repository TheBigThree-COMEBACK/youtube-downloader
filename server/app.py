#!/usr/bin/env python3
"""
YouTube Video Downloader - Backend API
"""

from flask import Flask, render_template, request, send_file, jsonify, after_this_request
import yt_dlp
import os
import uuid
import threading
import time
import re
import tempfile
from pathlib import Path

app = Flask(__name__)

# Temp download directory (Render-safe)
DOWNLOAD_FOLDER = Path(tempfile.gettempdir()) / "yt_downloads"
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

# In-memory status
download_status = {}

# Free tier limit (MB)
FREE_TIER_LIMIT_MB = 500


def cleanup_old_files():
    """Delete files older than 30 minutes"""
    while True:
        now = time.time()
        try:
            for f in DOWNLOAD_FOLDER.glob("*"):
                if now - f.stat().st_mtime > 1800:
                    f.unlink(missing_ok=True)
        except Exception as e:
            print("Cleanup error:", e)
        time.sleep(300)


threading.Thread(target=cleanup_old_files, daemon=True).start()


def sanitize_filename(title):
    title = re.sub(r"[^\w\s-]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()[:100]


def update_progress(d, download_id):
    if d["status"] == "downloading":
        try:
            percent = d.get("_percent_str", "0%").replace("%", "").strip()
            download_status[download_id]["progress"] = float(percent)
        except:
            pass


def download_video(url, download_id):
    try:
        output_template = str(DOWNLOAD_FOLDER / f"{download_id}.%(ext)s")

        ydl_opts = {
            # OUTPUT
            "outtmpl": output_template,
            "merge_output_format": "mp4",

            # ✅ BEST QUALITY (NO DOWNGRADE)
            "format": "bestvideo[height<=2160]+bestaudio/best",

            # QUIET
            "quiet": True,
            "no_warnings": True,

            # 🔥 ANDROID CLIENT (ANTI-BOT)
            "extractor_args": {
                "youtube": {
                    "player_client": ["android"],
                    "player_skip": ["webpage", "configs"],
                }
            },

            # ANDROID HEADERS
            "http_headers": {
                "User-Agent": (
                    "com.google.android.youtube/17.36.4 "
                    "(Linux; Android 12)"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },

            # FFmpeg merge → PERFECT SYNC
            "force_ipv4": True,
            "socket_timeout": 30,
            "concurrent_fragment_downloads": 1,

            "progress_hooks": [lambda d: update_progress(d, download_id)],
        }

        download_status[download_id] = {"status": "downloading", "progress": 0}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            filesize = info.get("filesize") or info.get("filesize_approx") or 0
            size_mb = round(filesize / (1024 * 1024), 1)

            for file in DOWNLOAD_FOLDER.glob(f"{download_id}*.mp4"):
                download_status[download_id] = {
                    "status": "complete",
                    "filename": file.name,
                    "title": sanitize_filename(info.get("title", "video")),
                    "size_mb": size_mb,
                }
                return

        download_status[download_id] = {
            "status": "error",
            "message": "Download failed",
        }

    except Exception as e:
        download_status[download_id] = {
            "status": "error",
            "message": str(e),
        }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if "youtube.com" not in url and "youtu.be" not in url:
        return jsonify({"error": "Invalid YouTube URL"}), 400

    # Pre-check size
    try:
        ydl_opts_info = {
            "quiet": True,
            "no_warnings": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android"],
                }
            },
            "http_headers": {
                "User-Agent": (
                    "com.google.android.youtube/17.36.4 "
                    "(Linux; Android 12)"
                )
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            size = info.get("filesize") or info.get("filesize_approx") or 0
            size_mb = size / (1024 * 1024)

            if size_mb > FREE_TIER_LIMIT_MB:
                return jsonify({
                    "error": "size_limit",
                    "size_mb": round(size_mb, 1),
                    "limit_mb": FREE_TIER_LIMIT_MB,
                }), 403

    except Exception as e:
        print("Size check failed:", e)

    download_id = str(uuid.uuid4())
    threading.Thread(
        target=download_video,
        args=(url, download_id),
        daemon=True,
    ).start()

    return jsonify({"download_id": download_id})


@app.route("/status/<download_id>")
def status(download_id):
    return jsonify(download_status.get(download_id, {"status": "not_found"}))


@app.route("/get/<download_id>")
def get_file(download_id):
    status = download_status.get(download_id)

    if not status or status["status"] != "complete":
        return jsonify({"error": "Not ready"}), 404

    filepath = DOWNLOAD_FOLDER / status["filename"]

    if not filepath.exists():
        return jsonify({"error": "File missing"}), 404

    @after_this_request
    def cleanup(response):
        def delayed():
            time.sleep(5)
            filepath.unlink(missing_ok=True)
            download_status.pop(download_id, None)
        threading.Thread(target=delayed, daemon=True).start()
        return response

    return send_file(
        filepath,
        as_attachment=True,
        download_name=f"{status['title']}.mp4",
        mimetype="video/mp4",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
