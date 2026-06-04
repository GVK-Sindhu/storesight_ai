
import time
import uuid
import logging
from typing import List
from fastapi import FastAPI, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from models import UnifiedEventIngestSchema, DBEvent
from ingestion import get_db, init_db, ingest_event_db
from metrics import compute_store_metrics
from funnel import compute_store_funnel
from heatmap import compute_store_heatmap
from anomalies import detect_store_anomalies
from health import check_service_health

# Initialize Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("StoreSightAPI")

app = FastAPI(
    title="StoreSight AI - Store Intelligence API",
    description="Production-grade Retail Analytics API for Purplle Tech Challenge 2026",
    version="1.0.0"
)

# Startup DB setup
@app.on_event("startup")
def startup_event():
    init_db()

# Graceful degradation database checker middleware
@app.middleware("http")
async def db_check_middleware(request: Request, call_next):
    # Intercept Database operational issues to return 503
    try:
        response = await call_next(request)
        return response
    except OperationalError as e:
        logger.error(f"Database operational failure: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": "Service Unavailable",
                "message": "The analytics database is currently unreachable. Please retry shortly.",
                "code": "DATABASE_DISCONNECTED"
            }
        )
    except Exception as e:
        logger.error(f"Unhandled system error: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred inside the system server.",
                "code": "INTERNAL_ERROR"
            }
        )

# Custom Structured Logging Middleware
@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next):
    start_time = time.time()
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    
    # Expose trace ID in response headers for tracing
    response = await call_next(request)
    
    process_time = (time.time() - start_time) * 1000  # in ms
    
    # Extract store_id from path if available
    path_params = request.path_params
    store_id = path_params.get("id", "GLOBAL")
    
    # Extract event_count for ingestion endpoint
    event_count = 0
    if request.url.path == "/events/ingest" and request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, list):
                event_count = len(body)
            else:
                event_count = 1
        except Exception:
            event_count = 0
            
    # Print structured log in JSON format
    log_data = {
        "trace_id": trace_id,
        "store_id": store_id,
        "endpoint": request.url.path,
        "method": request.method,
        "latency_ms": round(process_time, 2),
        "event_count": event_count,
        "status_code": response.status_code
    }
    logger.info(f"REQUEST_LOG: {log_data}")
    response.headers["X-Trace-ID"] = trace_id
    return response

# Normalize Store ID helper
def normalize_store_id(store_id_raw: str) -> str:
    store_id = store_id_raw.strip()
    if store_id.lower().startswith("store_"):
        store_id = "ST" + store_id[6:]
    return store_id.upper()

# 0. GET /stores
@app.get("/stores")
def get_stores(db: Session = Depends(get_db)):
    """
    Returns a list of all unique store IDs in the database.
    """
    try:
        from models import DBTransaction
        stores = db.query(DBEvent.store_id).distinct().all()
        tx_stores = db.query(DBTransaction.store_id).distinct().all()
        unique_stores = set([s[0] for s in stores if s[0]] + [s[0] for s in tx_stores if s[0]])
        if not unique_stores:
            unique_stores.add("ST1008")
        return sorted(list(unique_stores))
    except Exception as e:
        logger.error(f"Failed to query stores: {e}")
        return ["ST1008"]

# 1. POST /events/ingest
@app.post("/events/ingest", status_code=status.HTTP_200_OK)
def ingest_events(events: List[UnifiedEventIngestSchema], db: Session = Depends(get_db)):
    """
    Accepts batches of up to 500 visitor events.
    Validates, deduplicates, and stores events in the SQLite database.
    Supports polymorphic schemas (both old and new formats).
    """
    if len(events) > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch size exceeds maximum limit of 500 events."
        )
        
    ingested_count = 0
    duplicate_count = 0
    
    for event in events:
        try:
            success = ingest_event_db(db, event)
            if success:
                ingested_count += 1
            else:
                duplicate_count += 1
        except Exception as e:
            logger.error(f"Failed to ingest event {event.event_id or event.queue_event_id or 'unknown'}: {e}")
            
    return {
        "status": "success",
        "ingested": ingested_count,
        "duplicates_skipped": duplicate_count,
        "total_processed": len(events)
    }

# 2. GET /stores/{id}/metrics (Supports case-insensitive URL binding)
@app.get("/stores/{id}/metrics")
@app.get("/stores/{id}/Metrics")
def get_store_metrics(id: str, db: Session = Depends(get_db)):
    """
    Returns today's real-time metrics for a specific store:
    unique visitors, conversion rate, average zone dwell, queue depth, queue abandonment.
    """
    normalized_id = normalize_store_id(id)
    metrics = compute_store_metrics(db, normalized_id)
    return metrics

# 3. GET /stores/{id}/funnel
@app.get("/stores/{id}/funnel")
def get_store_funnel(id: str, db: Session = Depends(get_db)):
    """
    Returns the session-based visitor conversion funnel and drop-off percentages.
    """
    normalized_id = normalize_store_id(id)
    funnel = compute_store_funnel(db, normalized_id)
    return funnel

# 4. GET /stores/{id}/heatmap
@app.get("/stores/{id}/heatmap")
def get_store_heatmap(id: str, db: Session = Depends(get_db)):
    """
    Returns the normalized zone visit frequency and dwell times (0-100 scales).
    """
    normalized_id = normalize_store_id(id)
    heatmap = compute_store_heatmap(db, normalized_id)
    return heatmap

# 5. GET /stores/{id}/anomalies
@app.get("/stores/{id}/anomalies")
def get_store_anomalies(id: str, db: Session = Depends(get_db)):
    """
    Returns active store operational anomalies (spikes, conversion drops, dead zones).
    """
    normalized_id = normalize_store_id(id)
    anomalies = detect_store_anomalies(db, normalized_id)
    return anomalies

# 5.1 GET /stores/{id}/demographics
@app.get("/stores/{id}/demographics")
def get_store_demographics(id: str, db: Session = Depends(get_db)):
    """
    Returns demographic statistics (age, gender, groups) for a specific store.
    """
    normalized_id = normalize_store_id(id)
    
    # Fetch ENTRY gate events first (representing unique visitor sessions)
    entries = db.query(DBEvent.gender, DBEvent.age, DBEvent.age_bucket, DBEvent.group_id, DBEvent.group_size)\
        .filter(DBEvent.store_id == normalized_id)\
        .filter(DBEvent.event_type == "ENTRY")\
        .filter(DBEvent.is_staff == False)\
        .all()
        
    if not entries:
        # Fallback to unique track records if no gate ENTRY events exist
        entries = db.query(DBEvent.gender, DBEvent.age, DBEvent.age_bucket, DBEvent.group_id, DBEvent.group_size)\
            .filter(DBEvent.store_id == normalized_id)\
            .filter(DBEvent.is_staff == False)\
            .filter(DBEvent.gender != None)\
            .group_by(DBEvent.visitor_id)\
            .all()

    genders = {"F": 0, "M": 0}
    age_buckets = {}
    group_sizes = []
    
    for gender, age, age_bucket, group_id, group_size in entries:
        # Gender
        if gender:
            g_key = str(gender).upper().strip()
            if g_key in ["F", "M"]:
                genders[g_key] = genders.get(g_key, 0) + 1
        
        # Age bucket
        a_key = age_bucket
        if not a_key and age is not None:
            if age < 18: a_key = "Under 18"
            elif age <= 24: a_key = "18-24"
            elif age <= 34: a_key = "25-34"
            elif age <= 44: a_key = "35-44"
            else: a_key = "45+"
        
        if a_key:
            a_key = a_key.strip()
            age_buckets[a_key] = age_buckets.get(a_key, 0) + 1
        
        # Group size
        if group_size is not None:
            group_sizes.append(group_size)
            
    avg_group_size = round(sum(group_sizes) / len(group_sizes), 2) if group_sizes else 1.0
    
    return {
        "genders": genders,
        "age_buckets": age_buckets,
        "avg_group_size": avg_group_size,
        "total_demographics_sampled": len(entries)
    }

# 6. GET /health
@app.get("/health")
@app.get("/healthz")
def get_health(db: Session = Depends(get_db)):
    """
    Returns system status, database connection, and camera lag monitoring.
    """
    health = check_service_health(db)
    return health
