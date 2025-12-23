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

# Use system temp directory instead of local downloads folder
DOWNLOAD_FOLDER = Path(tempfile.gettempdir()) / 'yt_downloads'
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

# Store download status in memory
download_status = {}

print("=" * 50)
print("🚀 YouTube Downloader Backend Starting...")
print("=" * 50)


def cleanup_old_files():
    """Clean up files older than 30 minutes"""
    while True:
        try:
            current_time = time.time()
            for file_path in DOWNLOAD_FOLDER.glob('*'):
                if current_time - file_path.stat().st_mtime > 1800:  # 30 mins
                    file_path.unlink()
                    print(f"🗑️ Cleaned up: {file_path.name}")
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")
        time.sleep(300)  # Check every 5 minutes


cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()


def sanitize_filename(title):
    """Remove special characters from filename"""
    # Remove hashtags, emojis, and special characters
    clean = re.sub(r'[#@$%^&*()+=\[\]{};:\'",<>?/\\|`~]', '', title)
    # Replace multiple spaces with single space
    clean = re.sub(r'\s+', ' ', clean)
    # Limit length
    return clean.strip()[:100]


def download_video(url, download_id):
    """Download video in background with better quality options"""
    try:
        output_template = str(DOWNLOAD_FOLDER / f'{download_id}.%(ext)s')

        ydl_opts = {
            # Best quality: video + audio, prefer mp4
            'format': 'bestvideo[ext=mp4][height<=2160]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio/best[height<=2160]/best',
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'restrictfilenames': True,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'progress_hooks': [lambda d: update_progress(d, download_id)],
            # Additional options for better quality
            'prefer_free_formats': False,
            'youtube_include_dash_manifest': True,
        }

        download_status[download_id] = {'status': 'downloading', 'progress': 0}
        print(f"⬇️ Starting: {download_id}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Get file size info
            filesize = info.get('filesize') or info.get('filesize_approx', 0)
            filesize_mb = filesize / (1024 * 1024) if filesize else 0

            # Find the downloaded file
            for file in DOWNLOAD_FOLDER.glob(f'{download_id}*'):
                if file.suffix == '.mp4':
                    download_status[download_id] = {
                        'status': 'complete',
                        'filename': file.name,
                        'title': sanitize_filename(info.get('title', 'video')),
                        'size_mb': round(filesize_mb, 1)
                    }
                    print(f"✅ Complete: {info.get('title', 'video')} ({filesize_mb:.1f} MB)")
                    return

        download_status[download_id] = {
            'status': 'error',
            'message': 'File not found after download'
        }

    except Exception as e:
        download_status[download_id] = {'status': 'error', 'message': str(e)}
        print(f"❌ Error: {e}")


def update_progress(d, download_id):
    """Update progress"""
    if d['status'] == 'downloading':
        try:
            percent = d.get('_percent_str', '0%').strip().replace('%', '')
            download_status[download_id]['progress'] = float(percent)
        except:
            pass


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    if 'youtube.com' not in url and 'youtu.be' not in url:
        return jsonify({'error': 'Invalid YouTube URL'}), 400

    # Optional: Check video info before downloading
    try:
        ydl_opts_info = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            filesize = info.get('filesize') or info.get('filesize_approx', 0)
            filesize_mb = filesize / (1024 * 1024) if filesize else 0
            
            # Warn if very large (optional - remove if you want no limit)
            if filesize_mb > 1000:  # 1 GB warning
                return jsonify({
                    'error': f'Video is very large ({filesize_mb:.1f} MB). This may fail on free tier. Try a shorter/lower quality video.'
                }), 400
    except Exception as e:
        # If we can't get info, continue anyway
        print(f"⚠️ Could not check video info: {e}")

    download_id = str(uuid.uuid4())
    thread = threading.Thread(target=download_video, args=(url, download_id))
    thread.daemon = True
    thread.start()

    return jsonify({'download_id': download_id})


@app.route('/status/<download_id>')
def check_status(download_id):
    status = download_status.get(download_id, {'status': 'not_found'})
    return jsonify(status)


@app.route('/get/<download_id>')
def get_file(download_id):
    status = download_status.get(download_id)

    if not status or status.get('status') != 'complete':
        return jsonify({'error': 'File not ready'}), 404

    filename = status.get('filename')
    filepath = DOWNLOAD_FOLDER / filename

    if not filepath.exists():
        return jsonify({'error': 'File not found'}), 404

    @after_this_request
    def cleanup(response):
        def delayed_delete():
            time.sleep(5)  # Wait 5 seconds before deleting
            try:
                filepath.unlink()
                if download_id in download_status:
                    del download_status[download_id]
                print(f"🗑️ Cleaned: {filename}")
            except Exception as e:
                print(f"⚠️ Cleanup failed: {e}")

        threading.Thread(target=delayed_delete, daemon=True).start()
        return response

    # Stream file in chunks (better for large files)
    return send_file(
        filepath,
        as_attachment=True,
        download_name=f"{status.get('title', 'video')}.mp4",
        mimetype='video/mp4'
    )


if __name__ == '__main__':
    print("\n✅ Backend Ready!")
    print(f"📂 Download folder: {DOWNLOAD_FOLDER}")
    import os
    port = int(os.environ.get('PORT', 5000))
    print(f"📍 Running on port: {port}")
    print("-" * 50 + "\n")
    app.run(host='0.0.0.0', port=port)
