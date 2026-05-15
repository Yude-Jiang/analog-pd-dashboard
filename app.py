"""
Cloud Run entry point — serves dashboard.html + JSON data files from GCS,
exposes /refresh endpoint for manual or scheduled data updates.
"""
import json
import os
import subprocess
import sys
import tempfile
import shutil
import datetime
import threading

from flask import Flask, send_from_directory, jsonify, request, abort, Response
from google.cloud import storage

app = Flask(__name__, static_folder=".")

# ---------------------------------------------------------------------------
# Config (set via Cloud Run environment variables)
# ---------------------------------------------------------------------------
GCS_BUCKET     = os.environ.get("GCS_BUCKET", "st-china-ai-force-dashboard")
REFRESH_SECRET = os.environ.get("REFRESH_SECRET", "")
PORT           = int(os.environ.get("PORT", 8080))

# All JSON blobs served from GCS (fallback to bundled file if GCS missing)
GCS_BLOBS = ["data.json", "yjbb_annual.json", "yjbb_quarterly.json", "profiles_xq.json",
             "refresh_meta.json"]

# Determine current and previous year for data fetch scope
_NOW          = datetime.datetime.utcnow()
FETCH_YEARS   = "2025 2026" if _NOW.year >= 2026 else "2024 2025"

# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------

def gcs_client():
    return storage.Client()

def download_blob(blob_name: str, dest_path: str):
    client = gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    blob   = bucket.blob(blob_name)
    blob.download_to_filename(dest_path)

def upload_blob(src_path: str, blob_name: str):
    client = gcs_client()
    bucket = client.bucket(GCS_BUCKET)
    blob   = bucket.blob(blob_name)
    blob.upload_from_filename(src_path, content_type="application/json")
    print(f"[GCS] Uploaded {src_path} → gs://{GCS_BUCKET}/{blob_name}")

def serve_blob(blob_name: str) -> Response:
    """Stream a JSON blob from GCS; fall back to bundled file."""
    try:
        client  = gcs_client()
        bucket  = client.bucket(GCS_BUCKET)
        blob    = bucket.blob(blob_name)
        content = blob.download_as_text(encoding="utf-8")
        return Response(content, mimetype="application/json")
    except Exception as e:
        print(f"[GCS] Could not fetch {blob_name}: {e} — falling back to bundled file")
        try:
            return send_from_directory(".", blob_name)
        except Exception:
            abort(404)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(".", "dashboard.html")

@app.route("/data.json")
def serve_data():
    return serve_blob("data.json")

@app.route("/yjbb_annual.json")
def serve_yjbb():
    return serve_blob("yjbb_annual.json")

@app.route("/yjbb_quarterly.json")
def serve_yjbb_quarterly():
    return serve_blob("yjbb_quarterly.json")

@app.route("/profiles_xq.json")
def serve_profiles():
    return serve_blob("profiles_xq.json")

@app.route("/refresh_meta.json")
def serve_refresh_meta():
    return serve_blob("refresh_meta.json")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)

def _do_refresh(job_id: str):
    """Run all fetch scripts in a background thread and upload results to GCS."""
    src_dir = os.path.dirname(os.path.abspath(__file__))
    tmpdir  = tempfile.mkdtemp()
    try:
        # Copy all Python scripts into tmpdir
        for fname in os.listdir(src_dir):
            if fname.endswith(".py"):
                shutil.copy(os.path.join(src_dir, fname), os.path.join(tmpdir, fname))

        # Download all JSON blobs from GCS (fall back to bundled)
        for blob_name in GCS_BLOBS:
            dest = os.path.join(tmpdir, blob_name)
            try:
                download_blob(blob_name, dest)
                print(f"[refresh] Downloaded {blob_name} from GCS")
            except Exception as e:
                bundled = os.path.join(src_dir, blob_name)
                if os.path.exists(bundled):
                    shutil.copy(bundled, dest)
                    print(f"[refresh] {blob_name} not in GCS ({e}), using bundled copy")

        env   = {**os.environ, "PYTHONPATH": src_dir}
        years = FETCH_YEARS
        ts    = datetime.datetime.utcnow()
        meta  = {"job_id": job_id, "refresh_started": ts.isoformat() + "Z",
                 "fetch_years": years, "sources": {}}

        def _run(label, cmd, timeout=300):
            r = subprocess.run(cmd, cwd=tmpdir, capture_output=True, text=True,
                               timeout=timeout, env=env)
            out = (r.stdout + r.stderr)[-2000:]
            print(f"[{label}]\n", out)
            meta["sources"][label] = {
                "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
                "exit_code": r.returncode,
                "ok": r.returncode == 0,
            }
            return r

        # 1. A-share annual (yjbb)
        _run("fetch_yjbb_annual",
             [sys.executable, "fetch_yjbb_annual.py", "--years"] + years.split())

        # 2. A-share quarterly (yjbb)
        _run("fetch_yjbb_quarterly",
             [sys.executable, "fetch_yjbb_quarterly.py", "--years"] + years.split())

        # 3. US quarterly — MPWR / NVTS via SEC EDGAR
        _run("fetch_edgar",
             [sys.executable, "fetch_edgar_to_json.py"], timeout=120)

        # 4. Taiwan quarterly — Silergy via MOPS
        _run("fetch_silergy",
             [sys.executable, "fetch_silergy_to_json.py"], timeout=120)

        # 5. Company profiles (XQ)
        _run("fetch_profiles",
             [sys.executable, "fetch_profiles.py"])

        # 6. Validate data quality
        _run("validate",
             [sys.executable, "validate_data.py"], timeout=60)

        # 7. Write refresh metadata
        meta["refresh_completed"] = datetime.datetime.utcnow().isoformat() + "Z"
        meta["status"] = "ok"
        meta_path = os.path.join(tmpdir, "refresh_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 8. Upload all updated JSON blobs back to GCS
        for blob_name in GCS_BLOBS:
            local_path = os.path.join(tmpdir, blob_name)
            if os.path.exists(local_path):
                upload_blob(local_path, blob_name)

        print(f"[refresh] job {job_id} completed ok")

    except Exception as e:
        print(f"[refresh] job {job_id} ERROR: {e}")
        try:
            meta["status"] = "error"
            meta["error"]  = str(e)
            meta["refresh_completed"] = datetime.datetime.utcnow().isoformat() + "Z"
            meta_path = os.path.join(tmpdir, "refresh_meta.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            upload_blob(meta_path, "refresh_meta.json")
        except Exception:
            pass
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@app.route("/refresh", methods=["POST"])
def refresh():
    """
    Triggered manually or by Cloud Scheduler / GitHub Actions.
    Spawns a background thread and returns 202 immediately to avoid proxy timeouts.
    """
    if REFRESH_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {REFRESH_SECRET}":
            abort(403)

    job_id = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    t = threading.Thread(target=_do_refresh, args=(job_id,), daemon=True)
    t.start()
    return jsonify({"status": "accepted", "job_id": job_id}), 202

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "AnalogPD Competitors Dashboard"})

# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
