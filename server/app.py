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
from pathlib import Path

app = Flask(__name__)

# Create downloads directory in server folder
DOWNLOAD_FOLDER = Path(__file__).parent / 'downloads'
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

# Store download status in memory
download_status = {}

print("=" * 50)
print("🚀 YouTube Downloader Backend Starting...")
print("=" * 50)


def cleanup_old_files():
    """Clean up files older than 1 hour"""
    while True:
        try:
            current_time = time.time()
            for file_path in DOWNLOAD_FOLDER.glob('*'):
                if current_time - file_path.stat().st_mtime > 3600:
                    file_path.unlink()
                    print(f"🗑️ Cleaned up: {file_path.name}")
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")
        time.sleep(600)


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
    """Download video in background"""
    try:
        output_template = str(DOWNLOAD_FOLDER / f'{download_id}.%(ext)s')

        ydl_opts = {
            'format':
            'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
            'outtmpl':
            output_template,
            'merge_output_format':
            'mp4',
            'quiet':
            True,
            'no_warnings':
            True,
            'restrictfilenames':
            True,  # Remove special characters
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'progress_hooks': [lambda d: update_progress(d, download_id)],
        }

        download_status[download_id] = {'status': 'downloading', 'progress': 0}
        print(f"⬇️ Starting: {download_id}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            # Find the downloaded file
            for file in DOWNLOAD_FOLDER.glob(f'{download_id}*'):
                if file.suffix == '.mp4':
                    download_status[download_id] = {
                        'status': 'complete',
                        'filename': file.name,
                        'title': sanitize_filename(info.get('title', 'video'))
                    }
                    print(f"✅ Complete: {info.get('title', 'video')}")
                    return

        download_status[download_id] = {
            'status': 'error',
            'message': 'File not found'
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
            time.sleep(3)
            try:
                filepath.unlink()
                if download_id in download_status:
                    del download_status[download_id]
                print(f"🗑️ Cleaned: {filename}")
            except:
                pass

        threading.Thread(target=delayed_delete, daemon=True).start()
        return response

    return send_file(filepath,
                     as_attachment=True,
                     download_name=f"{status.get('title', 'video')}.mp4")


if __name__ == '__main__':
    print("\n✅ Backend Ready!")
    print("📍 Running on: http://0.0.0.0:5002")
    print("-" * 50 + "\n")
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
