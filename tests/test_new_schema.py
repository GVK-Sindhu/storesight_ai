
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base, DBEvent, DBTransaction, UnifiedEventIngestSchema
from app.ingestion import ingest_event_db
from app.metrics import compute_store_metrics, get_stitched_sessions
from app.funnel import compute_store_funnel
from app.anomalies import detect_store_anomalies
from app.main import normalize_store_id

@pytest.fixture
def db_session():
    # Setup an in-memory SQLite database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()

def test_polymorphic_ingestion(db_session):
    # Test ingesting old schema event format
    old_event = UnifiedEventIngestSchema(
        event_id="e-old-01",
        store_id="ST1008",
        camera_id="CAM_ENT_01",
        visitor_id="VIS_old",
        event_type="ENTRY",
        timestamp="2026-04-10T12:00:00Z",
        is_staff=False,
        confidence=0.95
    )
    assert ingest_event_db(db_session, old_event) is True

    # Test ingesting new schema gate entry event format
    new_gate_event = UnifiedEventIngestSchema(
        event_type="entry",
        id_token="ID_60001",
        store_code="store_1076",
        camera_id="cam1",
        event_timestamp="2026-03-08T18:10:05.120",
        is_staff=False,
        gender_pred="F",
        age_pred=28,
        age_bucket="25-34",
        is_face_hidden=False,
        group_id="G_10",
        group_size=2
    )
    assert ingest_event_db(db_session, new_gate_event) is True

    # Check database entries are normalized
    db_old = db_session.query(DBEvent).filter(DBEvent.event_id == "e-old-01").first()
    assert db_old is not None
    assert db_old.store_id == "ST1008"

    # For new event, unique ID is generated deterministically
    db_new = db_session.query(DBEvent).filter(DBEvent.visitor_id == "ID_60001").first()
    assert db_new is not None
    assert db_new.store_id == "ST1076"  # converted from store_1076
    assert db_new.event_type == "ENTRY"  # normalized to uppercase
    assert db_new.gender == "F"
    assert db_new.age == 28
    assert db_new.group_size == 2
    assert db_new.group_id == "G_10"

def test_demographics_and_stitching(db_session):
    # Seed new transactions for store 1076
    # Transaction maps to 18:15:31 exit time + 5 mins
    tx1 = DBTransaction(
        order_id="TXN_1076_01",
        store_id="ST1076",
        brand_name="Faces Canada",
        total_amount=500.0,
        order_time="18:16:00",
        order_date="2026-03-08",
        timestamp="2026-03-08T18:16:00Z"
    )
    db_session.add(tx1)
    db_session.commit()

    # Seed mock event sequence for Visitor 1
    # 1. Entry at Gate
    e1 = UnifiedEventIngestSchema(
        event_type="entry", id_token="ID_60001", store_code="store_1076",
        camera_id="cam1", event_timestamp="2026-03-08T18:10:00.000",
        is_staff=False, gender_pred="F", age_pred=28, age_bucket="25-34"
    )
    # 2. Zone enter browse zone
    e2 = UnifiedEventIngestSchema(
        event_type="zone_entered", track_id=101, store_id="ST1076",
        camera_id="CAM2", zone_id="PURPLLE_MUM_1076_Z01", zone_name="Left Shelf",
        zone_type="SHELF", event_time="2026-03-08T18:11:00.000",
        gender="F", age=28, age_bucket="25-34"
    )
    # 3. Queue completed
    e3 = UnifiedEventIngestSchema(
        queue_event_id="q-ev-101", event_type="queue_completed", track_id=101,
        store_id="ST1076", camera_id="PURPLLE_MUM_1076_CAM6",
        zone_id="PURPLLE_MUM_1076_Z_BILLING_01", zone_name="Billing Counter Queue",
        zone_type="BILLING", queue_join_ts="2026-03-08T18:13:00.000",
        queue_served_ts="2026-03-08T18:13:10.000", queue_exit_ts="2026-03-08T18:15:00.000",
        wait_seconds=10, queue_position_at_join=2, abandoned=False,
        gender="F", age=28, age_bucket="25-34"
    )
    # 4. Exit
    e4 = UnifiedEventIngestSchema(
        event_type="exit", id_token="ID_60001", store_code="store_1076",
        camera_id="cam1", event_timestamp="2026-03-08T18:15:30.000",
        is_staff=False, gender_pred="F", age_pred=28, age_bucket="25-34"
    )
    
    assert ingest_event_db(db_session, e1) is True
    assert ingest_event_db(db_session, e2) is True
    assert ingest_event_db(db_session, e3) is True
    assert ingest_event_db(db_session, e4) is True

    # Verify stitching logic
    track_to_gate, gate_to_tracks = get_stitched_sessions(db_session, "ST1076")
    assert track_to_gate["TRK_101"] == "ID_60001"
    assert "TRK_101" in gate_to_tracks["ID_60001"]

    # Calculate metrics
    metrics = compute_store_metrics(db_session, "ST1076")
    assert metrics["unique_visitors"] == 1
    assert metrics["conversion_rate"] == 100.0
    assert metrics["abandonment_rate"] == 0.0

    # Calculate funnel
    funnel = compute_store_funnel(db_session, "ST1076")
    stages = funnel["stages"]
    assert stages[0]["count"] == 1  # Entry
    assert stages[1]["count"] == 1  # Zone Visit
    assert stages[2]["count"] == 1  # Billing Queue
    assert stages[3]["count"] == 1  # Purchase
