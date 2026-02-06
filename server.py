import os, json, time, threading, re
from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)
@app.route('/fetch', methods=['POST'])
def fetch_meta():
# Create a downloads folder if it doesn't exist
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Global dictionary to store progress for the EventSource
progress_data = {
    "percent": "0%", 
    "speed": "0 MB/s", 
    "eta": "0s", 
    "status": "Idle", 
    "file_path": ""
}

def clean_ansi(text):
    """Removes terminal color codes (like [0;32m) from yt-dlp output"""
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)

def progress_hook(d):
    global progress_data
    if d['status'] == 'downloading':
        # Clean the percentage and stats for a smooth UI
        p = d.get('_percent_str', '0%')
        progress_data.update({
            "percent": clean_ansi(p).replace('%','').strip() + "%",
            "speed": clean_ansi(d.get('_speed_str', '0 MB/s')),
            "eta": clean_ansi(d.get('_eta_str', '0s')),
            "status": "Downloading"
        })
    elif d['status'] == 'finished':
        progress_data.update({
            "status": "Processing...", # This is when FFmpeg merges video + audio
            "percent": "100%"
        })

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/fetch', methods=['POST'])
def fetch_info():
    url = request.json.get('url')
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    try:
        # We use a fresh instance to avoid caching issues
        with yt_dlp.YoutubeDL({'quiet': True, 'noplaylist': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            for f in info.get('formats', []):
                # Filter for formats that have video and resolution height
                if f.get('vcodec') != 'none' and f.get('height'):
                    res = f"{f['height']}p"
                    formats.append({'id': f['format_id'], 'res': res})
            
            # Remove duplicates (like different bitrates of same res) and sort high to low
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
    global progress_data
    data = request.json
    url = data.get('url')
    fid = data.get('format_id')
    
    # Reset global progress for the new task
    progress_data = {"percent": "0%", "speed": "0 MB/s", "eta": "0s", "status": "Starting...", "file_path": ""}

    def run_dl():
        # format_spec: 'fid+bestaudio' ensures we merge the selected video with HQ audio
        format_spec = f"{fid}+bestaudio/best" if fid != 'mp3' else 'bestaudio/best'
        
        opts = {
            'progress_hooks': [progress_hook],
            # Adding format_id to filename prevents IsADirectoryError and overwriting
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s_%(format_id)s.%(ext)s',
            'format': format_spec,
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'overwrites': True,
            'prefer_ffmpeg': True,
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
                # Determine the final filename after merging/post-processing
                actual_path = ydl.prepare_filename(info)
                
                # If it was merged to mp4 or converted to mp3, update path accordingly
                if fid == 'mp3':
                    actual_path = os.path.splitext(actual_path)[0] + '.mp3'
                elif not actual_path.endswith('.mp4'):
                    # Check if a merged .mp4 version exists
                    base_path = os.path.splitext(actual_path)[0]
                    if os.path.exists(base_path + '.mp4'):
                        actual_path = base_path + '.mp4'

                progress_data["file_path"] = actual_path
                progress_data["status"] = "Finished"
        except Exception as e:
            progress_data["status"] = f"Error: {str(e)}"

    # Run the download in a separate thread so the server doesn't freeze
    threading.Thread(target=run_dl).start()
    return jsonify({"started": True})

@app.route('/progress')
def progress():
    def generate():
        while True:
            yield f"data: {json.dumps(progress_data)}\n\n"
            if progress_data["status"] in ["Finished", "Error"]:
                break
            time.sleep(0.7) # Slightly faster updates for the UI
    return Response(generate(), mimetype='text/event-stream')

@app.route('/get-file')
def get_file():
    path = request.args.get('path')
    # Final Safety Check
    if not path or not os.path.exists(path) or os.path.isdir(path):
        return "File not found. It might still be merging.", 404
    return send_file(path, as_attachment=True)

if __name__ == '__main__':
    # Running on 0.0.0.0 allows you to access it from your phone's browser
    app.run(host='0.0.0.0', port=5003, debug=False)
