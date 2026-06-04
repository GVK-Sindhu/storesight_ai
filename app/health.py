
from datetime import datetime
from sqlalchemy import text
from models import DBEvent
from metrics import parse_iso

def check_service_health(db):
    """
    Checks the health of the system:
    1. Verifies database connectivity.
    2. Calculates feed lag to check for STALE_FEED.
    """
    # 1. Check DB Connection
    try:
        db.execute(text("SELECT 1"))
        db_status = "HEALTHY"
    except Exception as e:
        return {
            "status": "DOWN",
            "database": f"UNAVAILABLE: {str(e)}",
            "stale_feed_warning": False
        }

    # 2. Check Feed Lag
    # Find the latest event in the database
    latest_event = db.query(DBEvent).order_by(DBEvent.timestamp.desc()).first()
    
    if not latest_event:
        return {
            "status": "UP",
            "database": db_status,
            "last_event_timestamp": None,
            "feed_status": "NO_FEED",
            "stale_feed_warning": False,
            "message": "System is running but no events have been ingested yet."
        }
        
    latest_time = parse_iso(latest_event.timestamp)
    # For historic datasets, we compare against the system wall clock.
    # If the user is running live streams, this correctly flags lag.
    # In order to not flag STALE_FEED on the historic demo dataset, we can check the difference from the current UTC time.
    curr_time = datetime.utcnow()
    lag_seconds = (curr_time - latest_time).total_seconds()
    
    # We trigger a stale feed warning if the lag is greater than 600 seconds (10 minutes)
    is_stale = lag_seconds > 600
    
    return {
        "status": "UP",
        "database": db_status,
        "last_event_timestamp": latest_event.timestamp,
        "feed_status": "STALE_FEED" if is_stale else "ACTIVE",
        "stale_feed_warning": is_stale,
        "message": "Warning: Camera feed ingestion lag exceeds 10 minutes." if is_stale else "Camera feeds are active and regular."
    }
