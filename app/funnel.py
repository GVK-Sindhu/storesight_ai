
from sqlalchemy import func
from datetime import datetime
from models import DBEvent, DBTransaction
from metrics import parse_iso, get_stitched_sessions

def compute_store_funnel(db, store_id: str):
    """
    Computes the visitor conversion funnel stages:
    1. Entry: Unique visitor sessions who entered the store (excluding staff)
    2. Zone Visit: Unique visitor sessions who visited any browse zone (SHELF, DISPLAY)
    3. Billing Queue: Unique visitor sessions who entered the billing queue
    4. Purchase: Unique visitor sessions who completed a purchase (POS correlation)
    """
    # 1. Stitch sessions
    track_to_gate, gate_to_tracks = get_stitched_sessions(db, store_id)

    # 2. Get customer gate session list
    gate_visitors = db.query(DBEvent.visitor_id)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type == "ENTRY")\
        .filter(DBEvent.is_staff == False)\
        .distinct().all()
        
    if gate_visitors:
        all_entry_sessions = [v[0] for v in gate_visitors]
    else:
        # Fallback if no ENTRY gate events exist (e.g. old schema or track-only logs)
        all_visitors = db.query(DBEvent.visitor_id)\
            .filter(DBEvent.store_id == store_id)\
            .filter(DBEvent.is_staff == False)\
            .distinct().all()
        all_entry_sessions = [v[0] for v in all_visitors]

    entry_count = len(all_entry_sessions)
    if entry_count == 0:
        return {
            "stages": [
                {"stage": "Entry", "count": 0, "drop_off_pct": 0.0},
                {"stage": "Zone Visit", "count": 0, "drop_off_pct": 0.0},
                {"stage": "Billing Queue", "count": 0, "drop_off_pct": 0.0},
                {"stage": "Purchase", "count": 0, "drop_off_pct": 0.0}
            ]
        }

    # 3. Track zone enter events
    # Browse zones are any zones that are not billing queues
    zone_enters = db.query(DBEvent.visitor_id, DBEvent.zone_id, DBEvent.zone_type)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type == "ZONE_ENTER")\
        .filter(DBEvent.is_staff == False)\
        .all()
        
    zone_visited_sessions = set()
    billing_queue_sessions = set()

    for track_id, zone_id, zone_type in zone_enters:
        gate_id = track_to_gate.get(track_id, track_id)
        if gate_id not in all_entry_sessions:
            continue
            
        # Classify zone
        is_billing = (zone_type == "BILLING") or (zone_id == "BILLING_ZONE") or (zone_id and "billing" in zone_id.lower())
        if is_billing:
            billing_queue_sessions.add(gate_id)
        else:
            zone_visited_sessions.add(gate_id)

    # Incorporate explicit new schema queue events
    explicit_queues = db.query(DBEvent.visitor_id, DBEvent.event_type)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type.in_(["QUEUE_COMPLETED", "QUEUE_ABANDONED", "BILLING_QUEUE_JOIN"]))\
        .filter(DBEvent.is_staff == False)\
        .all()
        
    for q_ev in explicit_queues:
        gate_id = track_to_gate.get(q_ev[0], q_ev[0])
        if gate_id in all_entry_sessions:
            billing_queue_sessions.add(gate_id)

    # 4. Purchase completion (POS correlation using 1-to-1 chronological mapping)
    transactions = db.query(DBTransaction).filter(DBTransaction.store_id == store_id).all()
    sorted_tx_times = sorted([parse_iso(tx.timestamp) for tx in transactions])
    
    q_completed = db.query(DBEvent)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type == "QUEUE_COMPLETED")\
        .filter(DBEvent.is_staff == False)\
        .all()
        
    all_exits = db.query(DBEvent)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type == "ZONE_EXIT")\
        .filter(DBEvent.is_staff == False)\
        .all()
    billing_exits = [
        ex for ex in all_exits
        if ex.zone_id == "BILLING_ZONE" or ex.zone_type == "BILLING" or (ex.zone_id and "billing" in ex.zone_id.lower())
    ]
    
    candidate_exits = []
    
    if q_completed:
        for q_exit in q_completed:
            exit_time = parse_iso(q_exit.queue_exit_ts or q_exit.timestamp)
            track_id = q_exit.visitor_id
            gate_id = track_to_gate.get(track_id, track_id)
            if gate_id in billing_queue_sessions:
                candidate_exits.append({"time": exit_time, "gate_id": gate_id, "matched": False})
    else:
        for b_exit in billing_exits:
            exit_time = parse_iso(b_exit.timestamp)
            track_id = b_exit.visitor_id
            gate_id = track_to_gate.get(track_id, track_id)
            if gate_id in billing_queue_sessions:
                candidate_exits.append({"time": exit_time, "gate_id": gate_id, "matched": False})
                
    converted_sessions = set()
    
    for tx_time in sorted_tx_times:
        best_candidate_idx = -1
        best_time_diff = float('inf')
        
        for idx, candidate in enumerate(candidate_exits):
            if candidate["matched"]:
                continue
            time_diff = (tx_time - candidate["time"]).total_seconds()
            if 0 <= time_diff <= 300: # 5 min window
                if time_diff < best_time_diff:
                    best_time_diff = time_diff
                    best_candidate_idx = idx
                    
        if best_candidate_idx != -1:
            candidate_exits[best_candidate_idx]["matched"] = True
            converted_sessions.add(candidate_exits[best_candidate_idx]["gate_id"])

    # If zone visit count is 0 but billing count > 0, make zone visit at least equal to billing count (funnel consistency)
    zone_count = len(zone_visited_sessions)
    billing_count = len(billing_queue_sessions)
    purchase_count = len(converted_sessions)
    
    if zone_count < billing_count:
        zone_count = billing_count

    # Calculate drop-off percentages stage-by-stage
    drop_off_zone = round((1 - (zone_count / entry_count)) * 100, 2) if entry_count > 0 else 0.0
    drop_off_billing = round((1 - (billing_count / zone_count)) * 100, 2) if zone_count > 0 else 0.0
    drop_off_purchase = round((1 - (purchase_count / billing_count)) * 100, 2) if billing_count > 0 else 0.0

    return {
        "stages": [
            {"stage": "Entry", "count": entry_count, "drop_off_pct": 0.0},
            {"stage": "Zone Visit", "count": zone_count, "drop_off_pct": drop_off_zone},
            {"stage": "Billing Queue", "count": billing_count, "drop_off_pct": drop_off_billing},
            {"stage": "Purchase", "count": purchase_count, "drop_off_pct": drop_off_purchase}
        ]
    }
