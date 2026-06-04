
from sqlalchemy import func
from models import DBEvent

def compute_store_heatmap(db, store_id: str):
    """
    Computes normalized zone metrics (frequency and dwell) scaled from 0 to 100.
    Returns:
    - zones: dict of zone_id -> {frequency, avg_dwell, norm_frequency, norm_dwell}
    - data_confidence: bool (False if < 20 unique sessions)
    """
    # 1. Check data confidence (total sessions)
    total_sessions_res = db.query(DBEvent.visitor_id)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.is_staff == False)\
        .distinct().count()
        
    data_confidence = total_sessions_res >= 20

    # 2. Get visit counts (frequency) for each zone
    frequency_results = db.query(DBEvent.zone_id, func.count(DBEvent.visitor_id))\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.is_staff == False)\
        .filter(DBEvent.event_type == "ZONE_ENTER")\
        .filter(DBEvent.zone_id != None)\
        .group_by(DBEvent.zone_id).all()

    # 3. Get average dwell time for each zone
    dwell_results = db.query(DBEvent.zone_id, func.avg(DBEvent.dwell_ms))\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.is_staff == False)\
        .filter(DBEvent.event_type == "ZONE_EXIT")\
        .filter(DBEvent.zone_id != None)\
        .group_by(DBEvent.zone_id).all()

    # Combine metrics
    zones_data = {}
    for zone_id, count in frequency_results:
        zones_data[zone_id] = {
            "frequency": count,
            "avg_dwell_ms": 0.0,
            "norm_frequency": 0.0,
            "norm_dwell": 0.0
        }
        
    for zone_id, avg_dwell in dwell_results:
        if zone_id not in zones_data:
            zones_data[zone_id] = {
                "frequency": 0,
                "avg_dwell_ms": 0.0,
                "norm_frequency": 0.0,
                "norm_dwell": 0.0
            }
        zones_data[zone_id]["avg_dwell_ms"] = float(avg_dwell or 0.0)

    # 4. Normalize (0 to 100)
    max_freq = max([z["frequency"] for z in zones_data.values()] + [1])
    max_dwell = max([z["avg_dwell_ms"] for z in zones_data.values()] + [1])

    for zone_id, data in zones_data.items():
        data["norm_frequency"] = round((data["frequency"] / max_freq) * 100, 2)
        data["norm_dwell"] = round((data["avg_dwell_ms"] / max_dwell) * 100, 2)
        # convert ms to seconds for clean display
        data["avg_dwell_sec"] = round(data["avg_dwell_ms"] / 1000.0, 2)

    return {
        "store_id": store_id,
        "zones": zones_data,
        "data_confidence": data_confidence
    }
