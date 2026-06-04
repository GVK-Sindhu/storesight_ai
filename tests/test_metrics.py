
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base, DBEvent, DBTransaction
from app.metrics import compute_store_metrics
from app.funnel import compute_store_funnel
from app.heatmap import compute_store_heatmap

@pytest.fixture
def db_session():
    # Setup an in-memory SQLite database for testing metrics
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()

def test_metrics_empty_db(db_session):
    metrics = compute_store_metrics(db_session, "ST1008")
    assert metrics["unique_visitors"] == 0
    assert metrics["conversion_rate"] == 0.0
    assert metrics["queue_depth"] == 0

def test_metrics_calculation(db_session):
    # Seed mock transactions
    tx1 = DBTransaction(
        order_id="TX1001",
        store_id="ST1008",
        brand_name="Faces Canada",
        total_amount=300.0,
        order_time="12:15:00",
        order_date="2026-04-10",
        timestamp="2026-04-10T12:15:00Z"
    )
    db_session.add(tx1)
    db_session.commit()

    # Seed mock events: Visitor 1 visits browse and billing, exits billing at 12:14:00 (converts within 5 mins!)
    events = [
        DBEvent(event_id="e1", store_id="ST1008", camera_id="CAM_ENT_01", visitor_id="VIS_1001", event_type="ENTRY", timestamp="2026-04-10T12:10:00Z", is_staff=False),
        DBEvent(event_id="e2", store_id="ST1008", camera_id="CAM_FLOOR_01", visitor_id="VIS_1001", event_type="ZONE_ENTER", timestamp="2026-04-10T12:11:00Z", zone_id="BROWSE_ZONE_A", is_staff=False),
        DBEvent(event_id="e3", store_id="ST1008", camera_id="CAM_BILL_01", visitor_id="VIS_1001", event_type="ZONE_ENTER", timestamp="2026-04-10T12:13:00Z", zone_id="BILLING_ZONE", is_staff=False),
        # Exit Billing
        DBEvent(event_id="e4", store_id="ST1008", camera_id="CAM_BILL_01", visitor_id="VIS_1001", event_type="ZONE_EXIT", timestamp="2026-04-10T12:14:00Z", zone_id="BILLING_ZONE", dwell_ms=60000, is_staff=False),
        # Exit Store
        DBEvent(event_id="e5", store_id="ST1008", camera_id="CAM_ENT_01", visitor_id="VIS_1001", event_type="EXIT", timestamp="2026-04-10T12:14:30Z", is_staff=False)
    ]
    for ev in events:
        db_session.add(ev)
    db_session.commit()

    # Visitor 2 is a staff member (should be excluded!)
    staff_events = [
        DBEvent(event_id="s1", store_id="ST1008", camera_id="CAM_ENT_01", visitor_id="VIS_STAFF", event_type="ENTRY", timestamp="2026-04-10T12:10:00Z", is_staff=True),
        DBEvent(event_id="s2", store_id="ST1008", camera_id="CAM_BILL_01", visitor_id="VIS_STAFF", event_type="ZONE_ENTER", timestamp="2026-04-10T12:11:00Z", zone_id="BILLING_ZONE", is_staff=True),
        DBEvent(event_id="s3", store_id="ST1008", camera_id="CAM_BILL_01", visitor_id="VIS_STAFF", event_type="ZONE_EXIT", timestamp="2026-04-10T12:15:00Z", zone_id="BILLING_ZONE", dwell_ms=240000, is_staff=True)
    ]
    for ev in staff_events:
        db_session.add(ev)
    db_session.commit()

    metrics = compute_store_metrics(db_session, "ST1008")
    
    # 1 unique customer visitor (staff is excluded!)
    assert metrics["unique_visitors"] == 1
    # 1 converted visitor / 1 unique visitor = 100%
    assert metrics["conversion_rate"] == 100.0
    # Average dwell in BILLING_ZONE is 60 seconds (staff is excluded!)
    assert metrics["avg_dwell_sec"]["BILLING_ZONE"] == 60.0

def test_funnel_calculation(db_session):
    # Seed mock events for funnel test
    # Visitor 1: Entry -> Browse -> Billing -> (No Purchase)
    # Visitor 2: Entry -> Browse -> (No Billing)
    # Visitor 3: Entry -> (No Browse)
    events = [
        DBEvent(event_id="f1", store_id="ST1008", camera_id="CAM_ENT_01", visitor_id="V1", event_type="ENTRY", timestamp="2026-04-10T12:00:00Z"),
        DBEvent(event_id="f2", store_id="ST1008", camera_id="CAM_FLOOR_01", visitor_id="V1", event_type="ZONE_ENTER", timestamp="2026-04-10T12:01:00Z", zone_id="BROWSE_ZONE_A"),
        DBEvent(event_id="f3", store_id="ST1008", camera_id="CAM_BILL_01", visitor_id="V1", event_type="ZONE_ENTER", timestamp="2026-04-10T12:02:00Z", zone_id="BILLING_ZONE"),
        DBEvent(event_id="f4", store_id="ST1008", camera_id="CAM_BILL_01", visitor_id="V1", event_type="ZONE_EXIT", timestamp="2026-04-10T12:03:00Z", zone_id="BILLING_ZONE"),
        
        DBEvent(event_id="f5", store_id="ST1008", camera_id="CAM_ENT_01", visitor_id="V2", event_type="ENTRY", timestamp="2026-04-10T12:05:00Z"),
        DBEvent(event_id="f6", store_id="ST1008", camera_id="CAM_FLOOR_01", visitor_id="V2", event_type="ZONE_ENTER", timestamp="2026-04-10T12:06:00Z", zone_id="BROWSE_ZONE_B"),
        
        DBEvent(event_id="f7", store_id="ST1008", camera_id="CAM_ENT_01", visitor_id="V3", event_type="ENTRY", timestamp="2026-04-10T12:10:00Z")
    ]
    for ev in events:
        db_session.add(ev)
    db_session.commit()

    funnel = compute_store_funnel(db_session, "ST1008")
    stages = funnel["stages"]
    
    # Entry: V1, V2, V3 = 3
    assert stages[0]["count"] == 3
    # Zone Visit: V1, V2 = 2
    assert stages[1]["count"] == 2
    # Billing: V1 = 1
    assert stages[2]["count"] == 1
    # Purchase: 0
    assert stages[3]["count"] == 0
    
    # Check drop-offs
    # Entry -> Zone: 1 - 2/3 = 33.33%
    assert abs(stages[1]["drop_off_pct"] - 33.33) < 0.1
    # Zone -> Billing: 1 - 1/2 = 50%
    assert stages[2]["drop_off_pct"] == 50.0
    # Billing -> Purchase: 1 - 0/1 = 100%
    assert stages[3]["drop_off_pct"] == 100.0
