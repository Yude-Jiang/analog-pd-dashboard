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
GCS_BLOBS = ["data.json", "yjbb_annual.json", "yjbb_quarterly.json", "profiles_xq.json"]

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

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)

@app.route("/refresh", methods=["POST"])
def refresh():
    """
    Triggered manually or by Cloud Scheduler.
    Downloads JSON data from GCS, runs yjbb + profile refresh scripts,
    uploads updated files back to GCS.
    """
    # Auth
    if REFRESH_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {REFRESH_SECRET}":
            abort(403)

    results = {}
    tmpdir  = tempfile.mkdtemp()
    try:
        src_dir = os.path.dirname(os.path.abspath(__file__))

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

        env = {**os.environ, "PYTHONPATH": src_dir}

        # 1. Refresh yjbb annual data (current year)
        r1 = subprocess.run(
            [sys.executable, "fetch_yjbb_annual.py", "--years", "2025"],
            cwd=tmpdir, capture_output=True, text=True, timeout=300, env=env
        )
        results["fetch_yjbb"] = (r1.stdout + r1.stderr)[-2000:]
        print("[fetch_yjbb]\n", results["fetch_yjbb"])

        # 2. Refresh EDGAR quarterly data for MPWR / NVTS
        r_edgar = subprocess.run(
            [sys.executable, "fetch_edgar_to_json.py"],
            cwd=tmpdir, capture_output=True, text=True, timeout=120, env=env
        )
        results["fetch_edgar"] = (r_edgar.stdout + r_edgar.stderr)[-2000:]
        print("[fetch_edgar]\n", results["fetch_edgar"])

        # 3. Refresh Silergy quarterly data from MOPS
        r_silergy = subprocess.run(
            [sys.executable, "fetch_silergy_to_json.py"],
            cwd=tmpdir, capture_output=True, text=True, timeout=120, env=env
        )
        results["fetch_silergy"] = (r_silergy.stdout + r_silergy.stderr)[-2000:]
        print("[fetch_silergy]\n", results["fetch_silergy"])

        # 3. Refresh company profiles (XQ)
        r2 = subprocess.run(
            [sys.executable, "fetch_profiles.py"],
            cwd=tmpdir, capture_output=True, text=True, timeout=300, env=env
        )
        results["fetch_profiles"] = (r2.stdout + r2.stderr)[-2000:]
        print("[fetch_profiles]\n", results["fetch_profiles"])

        # 3. Validate data quality
        r3 = subprocess.run(
            [sys.executable, "validate_data.py"],
            cwd=tmpdir, capture_output=True, text=True, timeout=60, env=env
        )
        results["validate"] = (r3.stdout + r3.stderr)[-1000:]
        print("[validate]\n", results["validate"])

        # 4. Upload all updated JSON blobs back to GCS
        for blob_name in GCS_BLOBS:
            local_path = os.path.join(tmpdir, blob_name)
            if os.path.exists(local_path):
                upload_blob(local_path, blob_name)

        results["status"] = "ok"

    except Exception as e:
        results["status"] = "error"
        results["error"]  = str(e)
        print(f"[refresh] ERROR: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return jsonify(results)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "AnalogPD Competitors Dashboard"})

# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
