import os
import json
import time
import threading
import re
from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Global progress tracker
progress_data = {
    "percent": "0%", 
    "speed": "0 MB/s", 
    "eta": "0s", 
    "status": "Idle", 
    "file_path": ""
}

def clean_ansi(text):
    """Removes terminal color codes for a clean UI."""
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)

def progress_hook(d):
    global progress_data
    if d['status'] == 'downloading':
        p = d.get('_percent_str', '0%')
        progress_data.update({
            "percent": clean_ansi(p).replace('%','').strip() + "%",
            "speed": clean_ansi(d.get('_speed_str', '0 MB/s')),
            "eta": clean_ansi(d.get('_eta_str', '0s')),
            "status": "Downloading"
        })
    elif d['status'] == 'finished':
        progress_data.update({
            "status": "Processing...",
            "percent": "100%"
        })

@app.route('/')
def index():
    return "Backend is running. Use /fetch or /download."

@app.route('/fetch', methods=['POST'])
def fetch_info():
    """Fetches video metadata and available resolutions."""
    url = request.json.get('url')
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    
    try:
        # Reset progress for new request
        global progress_data
        progress_data["status"] = "Searching..."

        ydl_opts = {'quiet': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            for f in info.get('formats', []):
                if f.get('vcodec') != 'none' and f.get('height'):
                    res = f"{f['height']}p"
                    formats.append({'id': f['format_id'], 'res': res})
            
            # Deduplicate resolutions and sort High to Low
            unique = {f['res']: f for f in formats}.values()
            sorted_f = sorted(unique, key=lambda x: int(x['res'][:-1]), reverse=True)
            
            return jsonify({
                "formats": sorted_f, 
                "title": info.get('title'),
                "thumbnail": info.get('thumbnail')
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download', methods=['POST'])
def download():
    """Starts the download process in a background thread."""
    global progress_data
    data = request.json
    url = data.get('url')
    fid = data.get('format_id')
    
    progress_data = {"percent": "0%", "speed": "0 MB/s", "eta": "0s", "status": "Starting...", "file_path": ""}

    def run_dl():
        format_spec = f"{fid}+bestaudio/best" if fid != 'mp3' else 'bestaudio/best'
        opts = {
            'progress_hooks': [progress_hook],
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s_%(id)s.%(ext)s',
            'format': format_spec,
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'overwrites': True,
        }
        
        if fid == 'mp3':
            opts.update({
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            })
        
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                actual_path = ydl.prepare_filename(info)
                
                # Handling extension changes (mp4 merge or mp3 conversion)
                base = os.path.splitext(actual_path)[0]
                if fid == 'mp3':
                    actual_path = base + '.mp3'
                elif not actual_path.endswith('.mp4') and os.path.exists(base + '.mp4'):
                    actual_path = base + '.mp4'

                progress_data["file_path"] = actual_path
                progress_data["status"] = "Finished"
        except Exception as e:
            progress_data["status"] = f"Error: {str(e)}"

    threading.Thread(target=run_dl).start()
    return jsonify({"started": True})

@app.route('/progress')
def progress():
    """Event stream for real-time UI updates."""
    def generate():
        while True:
            yield f"data: {json.dumps(progress_data)}\n\n"
            if progress_data["status"] in ["Finished", "Error"]:
                break
            time.sleep(0.8)
    return Response(generate(), mimetype='text/event-stream')

@app.route('/get-file')
def get_file():
    path = request.args.get('path')
    if not path or not os.path.exists(path):
        return "File not found", 404
    return send_file(path, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003, debug=False)
