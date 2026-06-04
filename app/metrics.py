
from sqlalchemy import func
from datetime import datetime, timedelta
from models import DBEvent, DBTransaction

def parse_iso(ts_str):
    if not ts_str:
        return datetime.utcnow()
    try:
        # Clean suffix
        ts_str = ts_str.replace("Z", "")
        # Handle fractional seconds if any
        if "." in ts_str:
            return datetime.fromisoformat(ts_str)
        return datetime.fromisoformat(ts_str)
    except Exception:
        try:
            return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return datetime.utcnow()

def get_stitched_sessions(db, store_id: str):
    """
    Stitches visitor track IDs (e.g. TRK_101) with gate session IDs (e.g. ID_60001)
    using store, demographics, and temporal overlap.
    Returns:
      - track_to_gate: dict of TRK_xxx -> ID_xxx
      - gate_to_tracks: dict of ID_xxx -> list of TRK_xxx
    """
    # 1. Fetch gate sessions (ENTRY and EXIT events) in a single query
    gate_events = db.query(DBEvent)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type.in_(["ENTRY", "EXIT"]))\
        .all()
        
    events_by_visitor = {}
    for ev in gate_events:
        if ev.visitor_id not in events_by_visitor:
            events_by_visitor[ev.visitor_id] = {"entry": None, "exit": None}
        if ev.event_type == "ENTRY":
            events_by_visitor[ev.visitor_id]["entry"] = ev
        elif ev.event_type == "EXIT":
            events_by_visitor[ev.visitor_id]["exit"] = ev
            
    gate_sessions = []
    for vid, evs in events_by_visitor.items():
        ge = evs["entry"]
        exit_ev = evs["exit"]
        if ge:
            gate_sessions.append({
                "gate_id": ge.visitor_id,
                "entry_time": parse_iso(ge.timestamp),
                "exit_time": parse_iso(exit_ev.timestamp) if exit_ev else None,
                "gender": ge.gender,
                "age": ge.age,
                "is_staff": ge.is_staff
            })
        
    # 2. Fetch all tracking events starting with TRK_ in a single query, sorted by timestamp
    track_events = db.query(DBEvent)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.visitor_id.like("TRK_%"))\
        .order_by(DBEvent.timestamp.asc())\
        .all()
    
    seen_tracks = set()
    tracks = []
    track_ids = []
    
    for ev in track_events:
        tid = ev.visitor_id
        if tid not in seen_tracks:
            seen_tracks.add(tid)
            track_ids.append(tid)
            tracks.append({
                "track_id": tid,
                "first_time": parse_iso(ev.timestamp),
                "gender": ev.gender,
                "age": ev.age,
                "is_staff": ev.is_staff
            })
            
    # 3. Stitch them
    track_to_gate = {}
    gate_to_tracks = {}
    
    for trk in tracks:
        best_match = None
        best_time_diff = float('inf')
        
        for gate in gate_sessions:
            # Demographic match (allow 2 years age difference for model prediction jitter)
            if trk["gender"] and gate["gender"] and trk["gender"] != gate["gender"]:
                continue
            if trk["age"] is not None and gate["age"] is not None and abs(trk["age"] - gate["age"]) > 2:
                continue
                
            # Temporal overlap match
            time_after_entry = (trk["first_time"] - gate["entry_time"]).total_seconds()
            
            if time_after_entry >= -120:  # 2 mins buffer before entry
                if gate["exit_time"]:
                    time_before_exit = (gate["exit_time"] - trk["first_time"]).total_seconds()
                    if time_before_exit >= -120:  # 2 mins buffer after exit
                        diff = abs(time_after_entry)
                        if diff < best_time_diff:
                            best_time_diff = diff
                            best_match = gate["gate_id"]
                else:
                    diff = abs(time_after_entry)
                    if diff < best_time_diff:
                        best_time_diff = diff
                        best_match = gate["gate_id"]
                        
        if best_match:
            track_to_gate[trk["track_id"]] = best_match
            if best_match not in gate_to_tracks:
                gate_to_tracks[best_match] = []
            gate_to_tracks[best_match].append(trk["track_id"])
            
    # Fallback: if no matches, tracks map to themselves
    for tid in track_ids:
        if tid not in track_to_gate:
            track_to_gate[tid] = tid
            
    return track_to_gate, gate_to_tracks

def compute_store_metrics(db, store_id: str):
    """
    Computes real-time store metrics for the given store:
    - Unique Visitors (excluding staff)
    - Conversion Rate
    - Average Dwell Time per Zone (excluding staff)
    - Billing Queue Depth
    - Queue Abandonment Rate
    """
    # 1. Stitch sessions
    track_to_gate, gate_to_tracks = get_stitched_sessions(db, store_id)

    # 2. Total unique customer visitors (excluding staff)
    # Check if we have ENTRY events
    gate_visitors = db.query(DBEvent.visitor_id)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type == "ENTRY")\
        .filter(DBEvent.is_staff == False)\
        .distinct().all()
        
    if gate_visitors:
        unique_visitors_count = len(gate_visitors)
        customer_visitor_ids = [v[0] for v in gate_visitors]
    else:
        # Fallback to unique track visitor IDs if no gate ENTRY events exist
        all_visitors = db.query(DBEvent.visitor_id)\
            .filter(DBEvent.store_id == store_id)\
            .filter(DBEvent.is_staff == False)\
            .distinct().all()
        customer_visitor_ids = [v[0] for v in all_visitors]
        unique_visitors_count = len(customer_visitor_ids)

    if unique_visitors_count == 0:
        return {
            "unique_visitors": 0,
            "conversion_rate": 0.0,
            "avg_dwell_sec": {},
            "queue_depth": 0,
            "abandonment_rate": 0.0
        }

    # 3. Compute conversion rate and queue abandonment
    # Get all transactions for this store
    transactions = db.query(DBTransaction).filter(DBTransaction.store_id == store_id).all()
    sorted_tx_times = sorted([parse_iso(tx.timestamp) for tx in transactions])
    
    # Check if there are explicit new schema queue events
    explicit_queue_events = db.query(DBEvent)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type.in_(["QUEUE_COMPLETED", "QUEUE_ABANDONED"]))\
        .all()

    converted_visitors = set()
    
    if explicit_queue_events:
        # Calculate queue abandonment directly from explicit events
        total_queue_joins = len(explicit_queue_events)
        abandoned_queues = len([q for q in explicit_queue_events if q.abandoned is True or q.event_type == "QUEUE_ABANDONED"])
        abandonment_rate = abandoned_queues / total_queue_joins if total_queue_joins > 0 else 0.0
        
        # For conversion rate: match successful queue completed exits with transactions using 1-to-1 chronological mapping
        completed_exits = [q for q in explicit_queue_events if not q.abandoned and q.event_type == "QUEUE_COMPLETED"]
        
        candidate_exits = []
        for q_exit in completed_exits:
            exit_time = parse_iso(q_exit.queue_exit_ts or q_exit.timestamp)
            track_id = q_exit.visitor_id
            gate_id = track_to_gate.get(track_id, track_id)
            candidate_exits.append({"time": exit_time, "gate_id": gate_id, "matched": False})
            
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
                converted_visitors.add(candidate_exits[best_candidate_idx]["gate_id"])
    else:
        # Fallback to old sliding window method for old schema compatibility (using 1-to-1 matching)
        total_billing_visits = 0
        
        # Get billing zone exits for customer tracks
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
        for b_exit in billing_exits:
            exit_time = parse_iso(b_exit.timestamp)
            gate_id = track_to_gate.get(b_exit.visitor_id, b_exit.visitor_id)
            candidate_exits.append({"time": exit_time, "gate_id": gate_id, "matched": False})
            total_billing_visits += 1
            
        for tx_time in sorted_tx_times:
            best_candidate_idx = -1
            best_time_diff = float('inf')
            
            for idx, candidate in enumerate(candidate_exits):
                if candidate["matched"]:
                    continue
                time_diff = (tx_time - candidate["time"]).total_seconds()
                if 0 <= time_diff <= 300:
                    if time_diff < best_time_diff:
                        best_time_diff = time_diff
                        best_candidate_idx = idx
                        
            if best_candidate_idx != -1:
                candidate_exits[best_candidate_idx]["matched"] = True
                converted_visitors.add(candidate_exits[best_candidate_idx]["gate_id"])
                
        abandoned_billing_visits = sum(1 for c in candidate_exits if not c["matched"])
        abandonment_rate = abandoned_billing_visits / total_billing_visits if total_billing_visits > 0 else 0.0

    conversion_rate = len(converted_visitors) / unique_visitors_count if unique_visitors_count > 0 else 0.0

    # 4. Average dwell per zone (excluding staff)
    avg_dwell = {}
    zone_dwells = db.query(DBEvent.zone_name, DBEvent.zone_id, func.avg(DBEvent.dwell_ms))\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.is_staff == False)\
        .filter(DBEvent.event_type.in_(["ZONE_EXIT", "ZONE_EXITED", "QUEUE_COMPLETED", "QUEUE_ABANDONED"]))\
        .group_by(DBEvent.zone_name, DBEvent.zone_id).all()
        
    for zone_name, zone_id, avg_ms in zone_dwells:
        # Use user-friendly zone_name if available, otherwise zone_id
        z_key = zone_name if zone_name else zone_id
        if z_key:
            avg_dwell[z_key] = round((avg_ms or 0) / 1000.0, 2)  # convert to seconds

    # 5. Current Billing Queue Depth
    # Read directly from explicit queue positions or default billing queue joins
    latest_queue_event = db.query(DBEvent)\
        .filter(DBEvent.store_id == store_id)\
        .filter(DBEvent.event_type.in_(["BILLING_QUEUE_JOIN", "QUEUE_COMPLETED", "QUEUE_ABANDONED"]))\
        .order_by(DBEvent.timestamp.desc())\
        .first()
        
    if latest_queue_event:
        if latest_queue_event.queue_depth is not None:
            queue_depth = latest_queue_event.queue_depth
        elif latest_queue_event.queue_position_at_join is not None:
            # If it's a queue exit, depth drops or we estimate it
            if latest_queue_event.event_type in ["QUEUE_COMPLETED", "QUEUE_ABANDONED"]:
                queue_depth = max(0, latest_queue_event.queue_position_at_join - 1)
            else:
                queue_depth = latest_queue_event.queue_position_at_join
        else:
            queue_depth = 0
    else:
        queue_depth = 0

    return {
        "unique_visitors": unique_visitors_count,
        "conversion_rate": round(conversion_rate * 100, 2),  # percentage
        "avg_dwell_sec": avg_dwell,
        "queue_depth": queue_depth,
        "abandonment_rate": round(abandonment_rate * 100, 2)  # percentage
    }
