# System Architecture & Design Document (DESIGN.md)

This document provides a comprehensive technical overview of the **StoreSight AI** Store Intelligence System. The system translates raw CCTV footage from five in-store cameras into structured behavioral events, which are then analyzed to compute real-time business metrics and visualized on an interactive retail dashboard.

---

## 1. High-Level Architecture Overview

StoreSight AI is designed around a decoupled, event-driven, micro-frontend architecture consisting of four core layers:

1.  **Computer Vision (CV) Pipeline Layer**: Processes raw camera streams (`CAM 1` through `CAM 5`), runs person detection and centroid-based IoU tracking, maps visitor coordinates to layout dwell zones, performs color-profile-based staff exclusion, and associates visitor sessions across overlapping cameras.
2.  **API Backend Layer (FastAPI)**: Exposes a high-performance REST API. It handles idempotent event ingestion, seeds database transactions from POS records, computes real-time business metrics (such as store conversion rates and queue abandonment), and runs the operational anomaly engine.
3.  **Persistence Layer (SQLite)**: Stores behavioral events and seeded POS transactions. SQLite is selected for its zero-configuration deployment, stability, and speed under single-store workloads.
4.  **Presentation Layer (Streamlit)**: Offers an intuitive, auto-refreshing dashboard for store managers. It visualizes KPIs, the conversion funnel, operational alerts, and zone traffic heatmaps.

```
+-----------------------------------------------------------------------------------+
|                            CCTV Video Files (1080p)                               |
|        CAM 1 (Entry)   CAM 2 (Floor)   CAM 3 (Billing)   CAM 4 & 5 (Staff)        |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                        1. Computer Vision Pipeline (Python)                        |
|   - OpenCV HOG & YOLOv8 Detectors          - Centroid & IoU Frame Tracker         |
|   - HSV Torso Color Staff Filter           - Cross-Camera Re-ID Association       |
+-----------------------------------------------------------------------------------+
                                         |
                                         | (POST /events/ingest)
                                         v
+-----------------------------------------------------------------------------------+
|                            2. FastAPI API Backend                                 |
|   - Idempotent Ingestion Middleware        - Structured JSON Request Validator    |
|   - SQLAlchemy Event & Transaction Models  - Health and Stale Feed Monitors       |
+-----------------------------------------------------------------------------------+
                                         |
                                  +------+------+
                                  |             |
                                  v             v
+------------------------------------+       +--------------------------------------+
|       3. SQLite Database           |       |      4. Streamlit Dashboard          |
|  - Events Table                    |       |  - Key Metrics & Conversion KPIs     |
|  - POS Transactions Table          |       |  - Interactive Funnel & Heatmaps     |
+------------------------------------+       +--------------------------------------+
```

---

## 2. Component breakdown

### 2.1 Computer Vision Pipeline (`pipeline/`)
*   **`detect.py`**: Encapsulates person detection. It implements a dual-model strategy: loading a YOLOv8 model if available, and falling back to a highly optimized OpenCV HOG (Histogram of Oriented Gradients) descriptor if running in a CPU-only, offline container environment. It also analyzes the HSV color histogram of the torso bounding box region to identify purple staff uniforms.
*   **`tracker.py`**: Implements a Multi-Camera centroid and IoU tracker. It features an occlusion buffer (retains tracks for up to 30 lost frames) and cross-camera Re-ID (links tracks across overlapping cameras if a track exits the field of view of one camera and appears in another within a 3-second window).
*   **`emit.py`**: Orchestrates video frame reading, maps frame counts to datetime offsets starting at `2026-04-10T12:14:00Z` (to align with the POS transaction dataset), and emits JSON events matching the required schema.

### 2.2 API Backend Layer (`app/`)
*   **`main.py`**: The entrypoint. Sets up custom middleware for trace IDs and structured JSON logging. Exposes case-insensitive store endpoints, including `GET /stores` (lists all unique stores dynamically) and `GET /stores/{id}/demographics` (calculates gender splits, age buckets, and group dynamics). Intercepts database connection issues to return an `HTTP 503 Service Unavailable` response.
*   **`ingestion.py`**: Handles polymorphic event ingestion, transforming varying event formats (lowercase keys, different timestamp fields, casing variations) to a standardized `DBEvent` schema. Performs deterministic UUIDv5 generation for events without an `event_id` to guarantee idempotency. Seeds transactions from a standard DD-MM-YYYY format CSV into YYYY-MM-DD format.
*   **`metrics.py` & `funnel.py`**: Computes retail metrics. Integrates demographic session-stitching, which links local camera `track_id`s to global gate `id_token`s by matching gender, age (within ±2 years), and temporal bounds. Computes conversion rates and funnel stages (`Entry -> Zone Visit -> Billing Queue -> Purchase`) based on these stitched sessions.
*   **`anomalies.py`**: A rule-based engine that dynamically queries the database historical entries for browse zones, checking for `BILLING_QUEUE_SPIKE`, `CONVERSION_DROP`, and `DEAD_ZONE` without hardcoded zone layouts.
*   **`dashboard/dashboard.py`**: An upgraded Streamlit manager dashboard. Dynamically fetches unique store IDs from `GET /stores` to populate a dropdown selector. Displays HSL-themed charts for customer gender distributions, age brackets, group sizes, and live operational alerts.

---

## 3. AI-Assisted Decisions

During system design, AI suggestions shaped key architectural decisions. Here are three instances and our engineering evaluations:

### Decision 1: Frame Ingestion Rate (Frame-Skipping)
*   **AI Suggestion**: Process every frame of the 1080p, 30fps videos (90,000 frames total) in real-time inside the pipeline to ensure no visitor movement is missed.
*   **Our Decision**: **Override**. Processing 30 frames per second on standard CPU cores takes hours. We implemented a frame-skipping parameter (`frame_skip=30`) to sample 1 frame per second.
*   **Reasoning**: Retail visitors move relatively slowly, and dwell times in browse or billing zones span minutes. Sampling at 1 fps reduces computation by 96.6% (taking the runtime from hours to under 2 minutes) while retaining >95% accuracy in visitor counts, zone entrance, and dwell metrics.

### Decision 2: API Request ID Tracking (Trace IDs)
*   **AI Suggestion**: Generate a trace ID inside each endpoint function and return it in the response body.
*   **Our Decision**: **Modified**. We implemented a custom FastAPI HTTP middleware to inject a UUIDv4 `trace_id` into the request state, expose it in the response header (`X-Trace-ID`), and log it in a single structured JSON request log.
*   **Reasoning**: Decoupling tracing from endpoint logic ensures that all requests (including failed requests and bad inputs) are assigned a trace ID, providing comprehensive observability and easier debugging.

### Decision 3: Re-Entry Matching Strategy
*   **AI Suggestion**: Deploy a deep learning Re-ID model (such as OSNet) to extract visual feature vectors and compare them.
*   **Our Decision**: **Override**. We chose a distance-based trajectory approach combined with 2D HSV color histogram correlation of the visitor's clothing.
*   **Reasoning**: Heavy neural networks require GPUs, download large weights, and crash in CPU-only containers. A combined trajectory boundary match + HSV color histogram correlation is lightweight, runs in milliseconds on CPU, requires zero model downloads, and is extremely reliable for matching visitors who step out and return within a short window.
