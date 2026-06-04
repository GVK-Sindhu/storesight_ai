# StoreSight AI — Store Intelligence System

StoreSight AI is a production-ready store intelligence system that processes CCTV footage, tracks visitor traffic, filters out staff members, correlates visits with POS transactions, detects operational anomalies (like queue spikes and dead zones), and exposes retail analytics through robust APIs and a live dashboard.

Developed for the **Purplle Tech Challenge 2026**.

---

## Quick Start (Complete Setup in 4 Commands)

Run the entire system on your local machine using Docker Compose. Ensure you have Docker installed and running.

### 1. Build and Start the Application
Run the system using Docker Compose:
```bash
docker compose up --build
```
*This starts the FastAPI backend (port 8000) and the Streamlit dashboard (port 8501).*

### 2. Verify System Health
```bash
curl http://localhost:8000/health
```
*Confirms API status is UP and database connectivity is healthy.*

### 3. Fetch Real-time Analytics for a Specific Store
```bash
curl http://localhost:8000/stores/ST1076/metrics
```
*Returns today's visitor counts, conversion rates, and billing queue depth for store ST1076 (case-insensitive).*

### 4. Open the Interactive Dashboard
Open your browser and navigate to:
```
http://localhost:8501
```
*Visualizes visitor metrics, conversion funnel drop-offs, spatial heatmaps, customer demographics (gender splits, age buckets, group shopping dynamics), and active operational anomalies for the selected store.*

---

## Running the Computer Vision Pipeline

To process raw CCTV clips and generate structured events locally, follow these steps:

### 1. Install local Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the detection and tracking pipeline for a Store
You can process video files for any specific store by passing CLI arguments:
```bash
python pipeline/emit.py --video-dir "path/to/video/folder" --store-id ST1076 --output-file data/events_store2.jsonl
```
*   `--video-dir`: Path to the video directory containing camera clips.
*   `--store-id`: Target store identifier (e.g. `ST1076`).
*   `--output-file`: File path to output the generated JSONL events.
*   `--frame-skip`: Number of frames to skip to optimize performance (default is 30).

Or run the default script:
```bash
./pipeline/run.sh
```

### 3. Load events into the running API database
To test the API ingestion endpoint, send a batch of events:
```bash
curl -X POST -H "Content-Type: application/json" -d @data/events.jsonl http://localhost:8000/events/ingest
```
*Sends the event data to the FastAPI server, which validates, normalizes, and deduplicates events using a polymorphic schema. Note: The `data/events.jsonl` file is a pre-generated sample event log derived from processing the provided CCTV dataset and is included to allow reviewers to immediately test ingestion and analytics without re-running the computer vision pipeline.*

---

## Core API Endpoints

*   **`GET /stores`**: Returns a list of all unique store IDs currently in the database.
*   **`POST /events/ingest`**: Idempotent batch event ingestion (up to 500 events per request). Supports both original flat schemas and the new multi-schema formats (with coordinates, demographics, and wait times).
*   **`GET /stores/{id}/metrics`**: Real-time metrics (unique visitors, conversion rate, zone dwells, queue depth, queue abandonment).
*   **`GET /stores/{id}/funnel`**: Session-based conversion funnel stages (`Entry -> Zone Visit -> Billing Queue -> Purchase`) using demographic-based session-stitching.
*   **`GET /stores/{id}/demographics`**: Store customer profile metrics (gender ratio, age distribution, average group size).
*   **`GET /stores/{id}/heatmap`**: Normalized zone traffic index and average dwell times.
*   **`GET /stores/{id}/anomalies`**: Active anomalies including queue spikes, conversion rate drops, and dead zones.
*   **`GET /health`**: Health status, database ping, and camera feed lag warning (`STALE_FEED`).
