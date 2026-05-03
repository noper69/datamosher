import os
import re
import shutil
import subprocess
import uuid
import json
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename

def resource_path(*parts):
    """Return a path relative to the project directory."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


BASE_DIR = resource_path()
DATA_DIR = os.environ.get('DATAMOSHER_DATA_DIR', os.path.join(BASE_DIR, 'data'))
FFMPEG = os.environ.get(
    'FFMPEG_BINARY',
    resource_path('bin', 'ffmpeg') if os.path.exists(resource_path('bin', 'ffmpeg')) else 'ffmpeg'
)
FFPROBE = os.environ.get(
    'FFPROBE_BINARY',
    resource_path('bin', 'ffprobe') if os.path.exists(resource_path('bin', 'ffprobe')) else 'ffprobe'
)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(DATA_DIR, 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(DATA_DIR, 'outputs')
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('DATAMOSHER_MAX_UPLOAD_MB', '500')) * 1024 * 1024

ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm'}
ID_PATTERN = re.compile(r'^[a-f0-9]{8}$')
EVEN_DIMENSIONS_FILTER = 'scale=trunc(iw/2)*2:trunc(ih/2)*2'


def ensure_storage_dirs():
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

def allowed_file(f):
    return '.' in f and f.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def valid_id(value):
    return isinstance(value, str) and bool(ID_PATTERN.fullmatch(value))


def binary_available(binary):
    """Return True when a configured binary path or PATH command exists."""
    if os.path.sep in binary:
        return os.path.exists(binary) and os.access(binary, os.X_OK)
    return shutil.which(binary) is not None


def missing_dependencies():
    missing = []
    if not binary_available(FFMPEG):
        missing.append('ffmpeg')
    if not binary_available(FFPROBE):
        missing.append('ffprobe')
    return missing


def dependency_error_response():
    missing = missing_dependencies()
    if not missing:
        return None
    return jsonify({
        'error': (
            f"Missing required dependency: {', '.join(missing)}. "
            "Install ffmpeg (for example: brew install ffmpeg) and restart Datamosher."
        )
    }), 503

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_video_info(path):
    r = subprocess.run(
        [FFPROBE, '-v', 'quiet', '-print_format', 'json',
         '-show_streams', '-show_format', path],
        capture_output=True, text=True, check=True
    )
    info = json.loads(r.stdout)
    vs = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
    fps_str = vs.get('r_frame_rate', '30/1') if vs else '30/1'
    n, d = map(int, fps_str.split('/'))
    fps = n / d
    dur = float(info['format'].get('duration', 0))
    w = int(vs['width']) if vs else 640
    h = int(vs['height']) if vs else 360
    return {'fps': fps, 'duration': dur, 'width': w, 'height': h}


def detect_cuts(path, threshold=0.35):
    """Return list of timestamps (seconds) where scene cuts occur."""
    r = subprocess.run([
        FFMPEG, '-y', '-i', path,
        '-vf', f'select=gt(scene\\,{threshold}),showinfo',
        '-vsync', 'vfr', '-f', 'null', '-'
    ], capture_output=True, text=True)
    cuts = []
    for line in r.stderr.split('\n'):
        m = re.search(r'pts_time:([0-9.]+)', line)
        if m:
            t = float(m.group(1))
            if not cuts or t - cuts[-1] > 0.5:   # debounce
                cuts.append(t)
    return cuts


def find_ivops(data):
    """Find all MPEG4 I-VOP and P-VOP positions in raw AVI/MPEG4 data."""
    vops = []
    i = 0
    n = len(data)
    while i < n - 5:
        if data[i:i+4] == b'\x00\x00\x01\xb6':
            vop_type = (data[i+4] >> 6) & 0x03
            vops.append({'pos': i, 'type': vop_type})
        i += 1
    return vops


def encode_mpeg4_segment(src, out, ss, to, fps, width, height, q=3):
    """Encode a time slice of src to MPEG4 AVI with no scene-detection keyframes."""
    cmd = [
        FFMPEG, '-y',
        '-ss', str(ss), '-to', str(to),
        '-i', src,
        '-c:v', 'mpeg4',
        '-q:v', str(q),
        '-g', '9999',           # no periodic keyframes
        '-sc_threshold', '0',   # no scene-detection keyframes
        '-bf', '0',             # no B-frames
        '-pix_fmt', 'yuv420p',
        '-an',
        out
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def get_last_pvop(data, vops):
    """Return bytes of the last P-VOP in data."""
    pvops = [v for v in vops if v['type'] == 1]
    if not pvops:
        return None
    last = pvops[-1]
    idx = vops.index(last)
    start = last['pos']
    end = vops[idx+1]['pos'] if idx+1 < len(vops) else len(data)
    return data[start:end]


def get_first_pvop_after_ivop(data, vops):
    """Return bytes of the first P-VOP after the first I-VOP."""
    found_i = False
    for idx, v in enumerate(vops):
        if v['type'] == 0:
            found_i = True
            continue
        if found_i and v['type'] == 1:
            start = v['pos']
            end = vops[idx+1]['pos'] if idx+1 < len(vops) else len(data)
            return data[start:end]
    return None


def strip_mpeg4_headers(data, vops):
    """Return data starting from the first VOP (skip codec headers before it)."""
    if not vops:
        return data
    return data[vops[0]['pos']:]


# ---------------------------------------------------------------------------
# Core datamosh: proper P-frame duplication technique
# ---------------------------------------------------------------------------

def datamosh_iframe_removal(input_path, output_path, intensity):
    """
    Aggressive motion-vector datamosh by stitching MPEG4 segments while
    removing the later segments' I-VOP anchor frames.

    This deliberately creates reference mismatches between neighboring video
    chunks so the next chunk's P-frame motion vectors decode against the
    previous chunk's image. The result is the classic moving smear/tear effect,
    not a long frozen-frame hold.
    """
    uid = str(uuid.uuid4())[:6]
    tmp = os.path.dirname(os.path.abspath(input_path))
    made_files = []

    def run(cmd):
        return subprocess.run(cmd, check=True, capture_output=True, text=True)

    info = get_video_info(input_path)
    fps = info['fps'] or 30.0
    dur = info['duration']
    if dur <= 0:
        raise ValueError('Could not read input duration with ffprobe')

    intensity = max(0.0, min(1.0, float(intensity)))

    # Real cuts are best, but the slider should still matter on clips without
    # many hard cuts. Blend real cuts with intensity-controlled artificial
    # breakpoints: low intensity = fewer, wider chunks; high intensity = many
    # smaller chunks, which creates more I-VOP removals and visible smears.
    real_cuts = [t for t in detect_cuts(input_path, threshold=0.16) if 0.25 < t < dur - 0.25]
    step = max(0.28, 1.55 - intensity * 1.18)
    artificial_cuts = []
    t = step
    while t < dur - 0.25:
        artificial_cuts.append(t)
        t += step

    min_spacing = max(0.22, 0.72 - intensity * 0.50)
    max_cuts = max(2, int(round(4 + intensity * 32)))
    clean_cuts = []
    for t in sorted(real_cuts + artificial_cuts):
        if not clean_cuts or t - clean_cuts[-1] >= min_spacing:
            clean_cuts.append(t)
    cuts = clean_cuts[:max_cuts]

    boundaries = [0.0] + cuts + [dur]
    segments = []
    for start, end in zip(boundaries, boundaries[1:]):
        if end - start >= 0.12:
            segments.append((start, end))

    if len(segments) < 2:
        mid = dur / 2
        segments = [(0.0, mid), (mid, dur)]

    # Tiny anchor hold only. Large values caused the bad freeze-frame look.
    hold_frames = int(round(intensity * 3))

    try:
        raw_data = []
        for i, (ss, to) in enumerate(segments):
            seg_path = os.path.join(tmp, f'{uid}_seg{i}.avi')
            raw_path = os.path.join(tmp, f'{uid}_raw{i}.m4v')
            made_files.extend([seg_path, raw_path])

            encode_mpeg4_segment(input_path, seg_path, ss, to, fps, None, None)
            run([FFMPEG, '-y', '-i', seg_path, '-c:v', 'copy', '-f', 'm4v', raw_path])

            with open(raw_path, 'rb') as f:
                data = bytearray(f.read())
            vops = find_ivops(data)
            if not vops:
                raise ValueError(f'No MPEG4 VOP frames found in segment {i}')
            raw_data.append((data, vops))

        out_raw = os.path.join(tmp, f'{uid}_moshed.m4v')
        made_files.append(out_raw)

        # Low intensity keeps the timeline mostly chronological/subtle. Medium
        # intensity alternates between near and far chunks. High intensity uses
        # farthest-chunk jumps for the strongest reference mismatch.
        order = [0]
        if len(raw_data) > 2 and intensity >= 0.72:
            # Walk through chunks with large jumps so adjacent output chunks
            # usually come from distant times. This makes the following P-VOPs
            # decode against a very different reference image, which reads as
            # stronger classic datamosh smear rather than subtle compression.
            remaining = set(range(1, len(raw_data)))
            cursor = 0
            while remaining:
                cursor = max(
                    remaining,
                    key=lambda idx: min(abs(idx - cursor), len(raw_data) - abs(idx - cursor))
                )
                order.append(cursor)
                remaining.remove(cursor)
        elif len(raw_data) > 2 and intensity >= 0.38:
            left = list(range(1, (len(raw_data) + 1) // 2))
            right = list(range((len(raw_data) + 1) // 2, len(raw_data)))
            for pair in zip(right, left):
                order.extend(pair)
            longer = right if len(right) > len(left) else left
            order.extend(longer[len(order[1:]) // 2:])
            order = list(dict.fromkeys(order))
            order.extend(i for i in range(len(raw_data)) if i not in order)
        else:
            order.extend(range(1, len(raw_data)))

        last_p = None
        removed_i = 0
        with open(out_raw, 'wb') as fout:
            for out_index, source_index in enumerate(order):
                data, vops = raw_data[source_index]
                if out_index == 0:
                    # First segment includes codec headers and its first I-VOP.
                    fout.write(data)
                else:
                    if last_p is not None:
                        for _ in range(hold_frames):
                            fout.write(last_p)

                    # Keep only VOP payload from later segments and drop the
                    # initial I-VOP. This makes following P-VOPs use the old
                    # reference image, which creates moving datamosh smear.
                    first_i = next((idx for idx, v in enumerate(vops) if v['type'] == 0), None)
                    if first_i is None or first_i + 1 >= len(vops):
                        fout.write(strip_mpeg4_headers(data, vops))
                    else:
                        start_after_i = vops[first_i + 1]['pos']
                        fout.write(data[start_after_i:])
                        removed_i += 1

                lp = get_last_pvop(data, vops)
                if lp is not None:
                    last_p = lp

        if removed_i == 0:
            raise ValueError('No removable MPEG4 I-VOP frames were created')

        run([
            FFMPEG, '-y',
            '-f', 'm4v', '-r', str(fps), '-i', out_raw,
            '-i', input_path,
            '-map', '0:v:0', '-map', '1:a:0?',
            '-vf', EVEN_DIMENSIONS_FILTER,
            '-c:v', 'libx264', '-crf', '18', '-preset', 'veryfast',
            '-c:a', 'aac', '-b:a', '192k',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
            '-shortest',
            output_path
        ])
    finally:
        for f in made_files:
            try:
                os.remove(f)
            except OSError:
                pass

    return output_path

def datamosh_pixel_drift(input_path, output_path, intensity):
    blend = 0.3 + intensity * 0.65
    blur = intensity * 1.5
    vf = f'tblend=all_mode=average:all_opacity={blend:.2f}'
    if blur > 0.2:
        vf += f',gblur=sigma={blur:.2f}'
    vf += f',{EVEN_DIMENSIONS_FILTER}'
    subprocess.run([
        FFMPEG, '-y', '-i', input_path,
        '-vf', vf,
        '-c:v', 'libx264', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        output_path
    ], check=True, capture_output=True)


def datamosh_color_bleed(input_path, output_path, intensity):
    cbh = int(intensity * 40)
    crh = -int(intensity * 30)
    cbv = int(intensity * 15)
    crv = -int(intensity * 10)
    blend = 0.2 + intensity * 0.55
    vf = f'chromashift=cbh={cbh}:cbv={cbv}:crh={crh}:crv={crv},tblend=all_mode=average:all_opacity={blend:.2f},{EVEN_DIMENSIONS_FILTER}'
    subprocess.run([
        FFMPEG, '-y', '-i', input_path,
        '-vf', vf,
        '-c:v', 'libx264', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        output_path
    ], check=True, capture_output=True)


def datamosh_feedback(input_path, output_path, intensity):
    blend = 0.3 + intensity * 0.65
    sat = 1.0 + intensity * 2.5
    sharp = 0.5 + intensity * 1.5
    vf = f'tblend=all_mode=phoenix:all_opacity={blend:.2f},eq=saturation={sat:.2f},unsharp=5:5:{sharp:.2f}:3:3:0,{EVEN_DIMENSIONS_FILTER}'
    subprocess.run([
        FFMPEG, '-y', '-i', input_path,
        '-vf', vf,
        '-c:v', 'libx264', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k',
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        output_path
    ], check=True, capture_output=True)


def datamosh(input_path, output_path, params):
    mode = params.get('mode', 'iframe_removal')
    intensity = float(params.get('intensity', 0.5))
    if mode == 'iframe_removal':
        datamosh_iframe_removal(input_path, output_path, intensity)
    elif mode == 'pixel_drift':
        datamosh_pixel_drift(input_path, output_path, intensity)
    elif mode == 'color_bleed':
        datamosh_color_bleed(input_path, output_path, intensity)
    elif mode == 'feedback':
        datamosh_feedback(input_path, output_path, intensity)
    else:
        raise ValueError(f"Unknown mode: {mode}")
    return output_path


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html', missing_dependencies=missing_dependencies())


@app.route('/health')
def health():
    missing = missing_dependencies()
    return jsonify({
        'ok': not missing,
        'missing_dependencies': missing,
        'ffmpeg': FFMPEG,
        'ffprobe': FFPROBE,
    }), 200 if not missing else 503


@app.route('/upload', methods=['POST'])
def upload():
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': f'Allowed types: {", ".join(ALLOWED_EXTENSIONS)}'}), 400
    filename = secure_filename(file.filename)
    uid = str(uuid.uuid4())[:8]
    path = os.path.join(app.config['UPLOAD_FOLDER'], f'{uid}_{filename}')
    file.save(path)
    return jsonify({'id': uid, 'filename': filename, 'size': os.path.getsize(path)})


@app.route('/mosh', methods=['POST'])
def mosh():
    dependency_error = dependency_error_response()
    if dependency_error:
        return dependency_error
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
    data = request.json or {}
    uid = data.get('id')
    filename = data.get('filename')
    params = data.get('params', {})
    if not uid or not filename:
        return jsonify({'error': 'Missing id or filename'}), 400
    if not valid_id(uid):
        return jsonify({'error': 'Invalid upload id'}), 400
    safe = secure_filename(filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{uid}_{safe}')
    if not os.path.exists(input_path):
        return jsonify({'error': 'File not found. Upload again.'}), 404
    out_uid = str(uuid.uuid4())[:8]
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{out_uid}_moshed.mp4')
    try:
        datamosh(input_path, output_path, params)
        return jsonify({'output_id': out_uid, 'size': os.path.getsize(output_path)})
    except subprocess.CalledProcessError as e:
        err = e.stderr or e.stdout or str(e)
        if isinstance(err, bytes):
            err = err.decode(errors='replace')
        app.logger.error('ffmpeg failed: %s', err[-1200:])
        return jsonify({'error': 'ffmpeg failed while processing this video.'}), 500
    except Exception:
        app.logger.exception('Datamosh failed')
        return jsonify({'error': 'Datamosh failed while processing this video.'}), 500


@app.route('/download/<output_id>')
def download(output_id):
    if not all(c.isalnum() or c == '-' for c in output_id):
        return 'Invalid ID', 400
    path = os.path.join(app.config['OUTPUT_FOLDER'], f'{output_id}_moshed.mp4')
    if not os.path.exists(path):
        return 'File not found', 404
    return send_file(path, as_attachment=True, download_name='datamoshed.mp4', mimetype='video/mp4')


if __name__ == '__main__':
    ensure_storage_dirs()
    app.run(host='127.0.0.1', debug=os.environ.get('FLASK_DEBUG') == '1', port=5555)
