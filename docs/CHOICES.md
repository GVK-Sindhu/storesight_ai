# Architectural Choices & Trade-Offs (CHOICES.md)

This document provides a detailed breakdown of three critical engineering decisions made during the design and implementation of the **StoreSight AI** system, detailing the options considered, trade-offs, and future scaling pathways.

---

## Decision 1: Person Detection Model Selection

### Problem Statement
We need an object detection model to locate people in 1080p camera feeds. The model must balance accuracy, CPU execution speed, deployment footprint, and container portability.

### Options Considered
1.  **YOLOv8 / YOLOv11 (Ultralytics)**: Deep learning-based real-time object detector.
2.  **Torchvision Faster R-CNN**: Standard PyTorch object detector.
3.  **OpenCV HOG (Histogram of Oriented Gradients) + Linear SVM**: Classic computer vision person detector.

### AI Recommendation
The AI recommended using **YOLOv8 Nano (`yolov8n.pt`)** because of its high accuracy (mAP) and ability to detect small and partially occluded objects.

### Final Choice & Reasoning
We implemented a **Dual-Model Strategy** in `pipeline/detect.py`:
*   The system first attempts to load **YOLOv8** if the `ultralytics` package is installed.
*   If `ultralytics` is missing, it falls back to **OpenCV HOG People Detector**.

**Why HOG fallback wins for submission stability**:
If the evaluation container has no GPU, running deep learning models on a CPU can be extremely slow and might trigger container timeouts. The OpenCV HOG detector has zero external dependencies, requires no weight downloads, runs fast on any CPU, and guarantees the pipeline executes successfully during reviewer inspection.

### Trade-offs
*   **Accuracy vs. Execution Speed**: HOG has lower accuracy on crowded scenes and partial occlusions compared to YOLO.
*   **Deployment Size**: Using YOLO requires downloading a ~15MB weight file and installing ~1GB of PyTorch/CUDA dependencies. HOG is built directly into the lightweight OpenCV binary.

### Future Scaling Considerations
In a production deployment, we would deploy YOLOv11 on edge gateway hardware with dedicated NVIDIA Jetson accelerators, using TensorRT quantization to run detection at 30+ FPS with sub-10ms latency.

---

## Decision 2: Event Schema Design Rationale

### Problem Statement
We need a unified event schema to represent diverse retail behaviors (store entry, zone dwell, billing queues, staff presence, and store exit) that can support complex real-time metrics and POS transaction correlation.

### Options Considered
1.  **Strict Behavior Schema**: Separate schemas for different behaviors (e.g., EntryEvent, DwellEvent, QueueEvent).
2.  **Generic Event Schema**: A single, flat schema representing all events with a flexible metadata block.

### AI Recommendation
The AI suggested a generic schema containing a `metadata` dictionary to easily accommodate future metrics.

### Final Choice & Reasoning
We implemented a **Unified Event Schema with Typed Metadata** in `app/models.py`:
*   Every event has primary fields: `event_id`, `store_id`, `camera_id`, `visitor_id`, `event_type`, `timestamp`, `zone_id`, `dwell_ms`, `is_staff`, and `confidence`.
*   A structured, typed `metadata` block is used to store context-specific details: `queue_depth` (for billing queue joins) and `sku_zone` (for browse zone entries).

This design ensures that a single PostgreSQL/SQLite database table can store all behavioral logs. Exposing a single `POST /events/ingest` endpoint accepts any event type, reducing API routing complexity.

### Trade-offs
*   **Database Normalization vs. Query Speed**: Storing metadata in JSON/flexible columns makes queries slightly slower on traditional SQL databases. However, it simplifies the schema and allows adding new camera types without altering the database schema.

### Future Scaling Considerations
For a retail chain with 1,000+ stores emitting millions of events, we would ingest these events into an Apache Kafka stream, route them to a Time-Series Database (such as InfluxDB or ClickHouse), and partition tables by `store_id` and `timestamp`.

---

## Decision 3: Transaction Correlation & Attribution Choice

### Problem Statement
POS transactions do not contain a customer identity (anonymized data). We must correlate physical visitor sessions at the billing zone with purchase timestamps to calculate conversion rates and billing queue abandonments.

### Options Considered
1.  **Nearest Neighbor Time Match**: Match each transaction with the closest visitor who exited the billing counter.
2.  **Sliding Time-Window Correlation**: Associate a visitor with a transaction if they exited the `BILLING_ZONE` within a 5-minute window before the transaction timestamp.

### AI Recommendation
The AI recommended Nearest Neighbor matching, mapping the closest transaction to the closest billing visitor.

### Final Choice & Reasoning
We selected **Sliding Time-Window Correlation** (5-minute window: `exit_time <= tx_time <= exit_time + 5 mins`):
*   A transaction at `12:15:00` is correlated with any visitor who was in the billing zone and exited between `12:10:00` and `12:15:00`.
*   If a visitor exits the billing zone and no transaction is recorded within 5 minutes, the session is marked as `BILLING_QUEUE_ABANDON`.

**Why Sliding Window wins**:
In physical stores, checkout processing (scanning items, paying, packing) takes time. Nearest Neighbor matching fails when multiple customers pay in a different sequence than they queued. A 5-minute sliding window is robust to queue shuffling and checkout delays, providing a more reliable and consistent conversion rate.

### Trade-offs
*   **Attribution Overlap**: In highly crowded periods, a single transaction might overlap with multiple billing zone exits, leading to double-counting conversion if not properly session-deduplicated. We solved this by tracking conversion on the unique `visitor_id` level, ensuring each visitor session is only counted as converted once.

### Future Scaling Considerations
To scale this attribution, we would integrate BLE (Bluetooth Low Energy) beacons or RFID tags on shopping baskets to link physical visitor paths directly to POS cash register receipts, achieving 100% precise individual attribution.

---

## Decision 4: Polymorphic Event Ingestion Schema

### Problem Statement
The challenge transitioned from flat, simple events to richer multi-schema events featuring demographics, queue wait times, and spatial coordinate hotspots. We need to support both formats seamlessly without breaking existing analytics or database structure.

### Options Considered
1. **Separated Tables**: Create distinct database tables and endpoints for gate, zone, and queue events.
2. **Unified Polymorphic Model**: Create a single database table (`events`) and Pydantic model (`UnifiedEventIngestSchema`) that dynamically normalizes fields.

### Final Choice & Reasoning
We selected the **Unified Polymorphic Model**. All incoming fields are mapped to a single SQLAlchemy `DBEvent` model. 
* Event type and casing are standardized (e.g. `entry` -> `ENTRY`).
* Missing keys are resolved or generated (e.g., deterministic UUIDv5 generation when `event_id` is missing).
* Store codes (`store_1076`) and store IDs (`ST1076`) are standardized to uppercase store codes.

This avoids multiple table joins for analytics and keeps the API surface clean, unified, and fully backwards-compatible.

---

## Decision 5: Demographic-Based Cross-Camera Session Stitching

### Problem Statement
Camera tracking events only contain local, camera-scoped `track_id`s, whereas gate entries/exits contain global session identifiers (`id_token`). To attribute customer behavior and calculate full-funnel conversion rates, we must stitch local track sessions to global entry tokens.

### Options Considered
1. **Strict Temporal Mapping**: Map tracks to the closest gate entry time-wise, ignoring demographics.
2. **Demographic & Temporal Stitching**: Correlate track IDs and gate entries by combining predicted gender and age (with a ±2-year buffer) alongside temporal overlap constraints.

### Final Choice & Reasoning
We implemented **Demographic & Temporal Stitching** in [metrics.py](file:///d:/projects/StoreSight%20AI/app/metrics.py):
* A visitor's camera track (`TRK_<id>`) is stitched to a gate session (`ID_<token>`) if their gender matches, their age is within ±2 years (to account for predictor jitter), and the track overlap occurs within the session bounds (after entry, before exit).
* If multiple sessions match, the track is assigned to the temporally closest session.

This approach provides high accuracy on visitor journey attribution without requiring expensive deep-learning Re-ID networks.

---

## Decision 6: Dynamic Browse Zone Discovery in Anomalies Engine

### Problem Statement
The anomaly detection engine needs to check for `DEAD_ZONE` issues. Hardcoding zone IDs (like `BROWSE_ZONE_A`) breaks when supporting arbitrary stores with different layouts (such as `ST1076` vs `ST1008`).

### Options Considered
1. **Store Configuration Registry**: Hardcode a layout map for every possible store ID.
2. **Dynamic Database Discovery**: Query the database for active browse zone IDs historically seen for that store.

### Final Choice & Reasoning
We chose **Dynamic Database Discovery**. The anomaly engine queries the database dynamically for all distinct `zone_id`s recorded for the store where `zone_type` is not `BILLING` and `is_staff = False`.

This decouples the codebase from layout metadata, enabling instant support for new stores simply by ingesting their layout events.
