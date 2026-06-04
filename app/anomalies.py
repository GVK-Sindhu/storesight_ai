
from datetime import datetime, timedelta
from models import DBEvent, DBTransaction
from metrics import parse_iso, compute_store_metrics

def detect_store_anomalies(db, store_id: str):
    """
    Scans the event database to detect active operational anomalies:
    1. BILLING_QUEUE_SPIKE: Queue depth > 5 people (WARN)
    2. CONVERSION_DROP: Conversion rate drops below 15% (CRITICAL)
    3. DEAD_ZONE: No visits to a browse zone in the last 15 minutes of event activity (INFO)
    """
    anomalies = []
    
    # Get latest event timestamp to establish "current" time in the simulation
    latest_event = db.query(DBEvent)\
        .filter(DBEvent.store_id == store_id)\
        .order_by(DBEvent.timestamp.desc())\
        .first()
        
    if not latest_event:
        return anomalies  # No events, no anomalies
        
    curr_time = parse_iso(latest_event.timestamp)
    
    # 1. Check BILLING_QUEUE_SPIKE
    # Latest queue depth from billing zone events
    latest_queue = db.query(DBEvent)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type.in_(["BILLING_QUEUE_JOIN", "QUEUE_COMPLETED", "QUEUE_ABANDONED"]))\
        .order_by(DBEvent.timestamp.desc())\
        .first()
        
    if latest_queue:
        q_depth = 0
        if latest_queue.queue_depth is not None:
            q_depth = latest_queue.queue_depth
        elif latest_queue.queue_position_at_join is not None:
            if latest_queue.event_type in ["QUEUE_COMPLETED", "QUEUE_ABANDONED"]:
                q_depth = max(0, latest_queue.queue_position_at_join - 1)
            else:
                q_depth = latest_queue.queue_position_at_join
                
        if q_depth > 5:
            anomalies.append({
                "anomaly_type": "BILLING_QUEUE_SPIKE",
                "severity": "WARN",
                "timestamp": latest_queue.timestamp,
                "message": f"Billing queue depth has reached {q_depth} visitors.",
                "suggested_action": "Deploy additional staff to open a secondary checkout counter."
            })
        
    # 2. Check CONVERSION_DROP
    metrics = compute_store_metrics(db, store_id)
    conv_rate = metrics["conversion_rate"]
    
    # Trigger if conversion rate is abnormally low (e.g. < 15%) and we have some visitors
    if metrics["unique_visitors"] > 5 and conv_rate < 15.0:
        anomalies.append({
            "anomaly_type": "CONVERSION_DROP",
            "severity": "CRITICAL",
            "timestamp": latest_event.timestamp,
            "message": f"Store conversion rate has dropped to {conv_rate}%.",
            "suggested_action": "Check POS systems for payment processing failures or queue checkout delays."
        })
        
    # 3. Check DEAD_ZONE
    # Find all browse zone IDs dynamically from event log
    zones_res = db.query(DBEvent.zone_id)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.zone_id != None)\
        .filter(DBEvent.event_type.in_(["ZONE_ENTER", "ZONE_ENTERED"]))\
        .distinct().all()
        
    browse_zones = []
    for z in zones_res:
        z_id = z[0]
        if z_id and "billing" not in z_id.lower() and z_id != "BILLING_ZONE":
            browse_zones.append(z_id)
            
    if not browse_zones:
        # Fallback to defaults
        browse_zones = ["BROWSE_ZONE_A", "BROWSE_ZONE_B"]
        
    for zone in browse_zones:
        # Find if there was any ZONE_ENTER in the 15 minutes prior to the latest event
        window_start = curr_time - timedelta(minutes=15)
        window_start_str = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        recent_visit = db.query(DBEvent)\
            .filter(DBEvent.store_id == store_id)\
            .filter(DBEvent.zone_id == zone)\
            .filter(DBEvent.event_type.in_(["ZONE_ENTER", "ZONE_ENTERED"]))\
            .filter(DBEvent.timestamp >= window_start_str)\
            .first()
            
        if not recent_visit:
            anomalies.append({
                "anomaly_type": "DEAD_ZONE",
                "severity": "INFO",
                "timestamp": latest_event.timestamp,
                "message": f"No visitor traffic detected in zone {zone} for the last 15 minutes.",
                "suggested_action": "Inspect the visual merchandising display or check lighting in this area."
            })
            
    return anomalies
