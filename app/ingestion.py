
import os
import csv
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, DBEvent, DBTransaction, UnifiedEventIngestSchema

import pathlib
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "store_sight.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """
    Creates tables and seeds the database with POS transactions if not already seeded.
    """
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Check if transactions are already seeded
        tx_count = db.query(DBTransaction).count()
        if tx_count == 0:
            print("Seeding transactions from CSV...")
            # Point to the updated challenge transactions file
            csv_path = os.getenv("POS_CSV_PATH", str(PROJECT_ROOT / "updated_problemstatement" / "POS - sample transactionsb1e826f.csv"))
            if os.path.exists(csv_path):
                df = pd.read_csv(csv_path)
                for _, row in df.iterrows():
                    # Parse DD-MM-YYYY to YYYY-MM-DD
                    date_str = str(row['order_date']).strip()
                    time_str = str(row['order_time']).strip()
                    
                    try:
                        parts = date_str.split("-")
                        if len(parts) == 3:
                            formatted_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
                        else:
                            formatted_date = date_str
                    except Exception:
                        formatted_date = date_str
                        
                    iso_timestamp = f"{formatted_date}T{time_str}Z"
                    
                    tx = DBTransaction(
                        order_id=str(row['order_id']),
                        store_id=str(row['store_id']),
                        brand_name=str(row['brand_name']),
                        total_amount=float(row['total_amount']),
                        order_time=time_str,
                        order_date=formatted_date,
                        timestamp=iso_timestamp
                    )
                    db.merge(tx)  # merge handles duplicates gracefully
                db.commit()
                print(f"Seeded {df['order_id'].nunique()} transactions successfully.")
            else:
                print(f"CSV file not found at {csv_path}. Skipping seeding.")
    except Exception as e:
        print(f"Error during database initialization: {e}")
        db.rollback()
    finally:
        db.close()

def ingest_event_db(db, event: UnifiedEventIngestSchema) -> bool:
    """
    Ingests an event into the database with idempotency check.
    Supports both old and new schema fields by normalizing them.
    Returns True if ingested, False if it was a duplicate.
    """
    import uuid

    # 1. Normalize store_id
    store_id = event.store_id
    if not store_id and event.store_code:
        store_id = event.store_code
    
    if store_id:
        store_id = store_id.strip()
        # Convert store_1076 -> ST1076 for consistency
        if store_id.startswith("store_"):
            store_id = "ST" + store_id[6:]
        store_id = store_id.upper()
    else:
        store_id = "ST1008"

    # 2. Normalize visitor_id
    visitor_id = event.visitor_id
    if not visitor_id:
        if event.id_token:
            visitor_id = event.id_token
        elif event.track_id is not None:
            visitor_id = f"TRK_{event.track_id}"
        else:
            visitor_id = "VIS_UNKNOWN"

    # 3. Normalize event_type to uppercase format
    raw_type = event.event_type.strip().lower()
    if raw_type == "entry":
        event_type = "ENTRY"
    elif raw_type == "exit":
        event_type = "EXIT"
    elif raw_type in ["zone_enter", "zone_entered"]:
        event_type = "ZONE_ENTER"
    elif raw_type in ["zone_exit", "zone_exited"]:
        event_type = "ZONE_EXIT"
    elif raw_type == "queue_completed":
        event_type = "QUEUE_COMPLETED"
    elif raw_type == "queue_abandoned":
        event_type = "QUEUE_ABANDONED"
    else:
        event_type = event.event_type.upper()

    # 4. Normalize timestamp
    timestamp = event.timestamp
    if not timestamp:
        if event.event_timestamp:
            timestamp = event.event_timestamp
        elif event.event_time:
            timestamp = event.event_time
        elif event.queue_join_ts:
            timestamp = event.queue_join_ts
        else:
            timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            
    # Standardize format: ensure Z ending for UTC
    if timestamp and not timestamp.endswith("Z") and "T" in timestamp:
        # If it doesn't end with Z and doesn't have an offset, append Z
        if "+" not in timestamp and len(timestamp) >= 19:
            timestamp = timestamp + "Z"

    # 5. Determine unique event_id
    event_id = event.event_id
    if not event_id:
        if event.queue_event_id:
            event_id = event.queue_event_id
        else:
            # Generate deterministic UUID based on unique fields to prevent duplicates on batch replay
            unique_str = f"{visitor_id}_{store_id}_{event_type}_{timestamp}"
            event_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, unique_str))

    # Idempotency check: see if event already exists by event_id or by unique fields
    existing = db.query(DBEvent).filter(DBEvent.event_id == event_id).first()
    if not existing:
        existing = db.query(DBEvent).filter(
            DBEvent.visitor_id == visitor_id,
            DBEvent.store_id == store_id,
            DBEvent.camera_id == event.camera_id,
            DBEvent.event_type == event_type,
            DBEvent.timestamp == timestamp
        ).first()

    if existing:
        return False

    # Extract metadata fields
    queue_depth = event.metadata.queue_depth if event.metadata else None
    sku_zone = event.metadata.sku_zone if event.metadata else None
    session_seq = event.metadata.session_seq if event.metadata else 0

    # Extract demographics
    gender = event.gender if event.gender else event.gender_pred
    age = event.age if event.age is not None else event.age_pred
    age_bucket = event.age_bucket

    # Extract wait time
    dwell_ms = event.dwell_ms
    if event.wait_seconds is not None and (dwell_ms == 0 or dwell_ms is None):
        dwell_ms = event.wait_seconds * 1000

    db_event = DBEvent(
        event_id=event_id,
        store_id=store_id,
        camera_id=event.camera_id,
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=timestamp,
        zone_id=event.zone_id,
        dwell_ms=dwell_ms,
        is_staff=event.is_staff,
        confidence=event.confidence,
        queue_depth=queue_depth,
        sku_zone=sku_zone,
        session_seq=session_seq,
        gender=gender,
        age=age,
        age_bucket=age_bucket,
        is_face_hidden=event.is_face_hidden,
        group_id=event.group_id,
        group_size=event.group_size,
        zone_name=event.zone_name,
        zone_type=event.zone_type,
        is_revenue_zone=event.is_revenue_zone,
        zone_hotspot_x=event.zone_hotspot_x,
        zone_hotspot_y=event.zone_hotspot_y,
        queue_join_ts=event.queue_join_ts,
        queue_served_ts=event.queue_served_ts,
        queue_exit_ts=event.queue_exit_ts,
        wait_seconds=event.wait_seconds,
        queue_position_at_join=event.queue_position_at_join,
        abandoned=event.abandoned
    )
    db.add(db_event)
    db.commit()
    return True
