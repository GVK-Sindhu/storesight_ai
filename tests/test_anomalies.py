
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base, DBEvent, DBTransaction
from app.anomalies import detect_store_anomalies

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()

def test_no_anomalies_init(db_session):
    anoms = detect_store_anomalies(db_session, "ST1008")
    assert len(anoms) == 0

def test_queue_spike_trigger(db_session):
    # Add a mock latest event to set "current" time
    db_session.add(DBEvent(
        event_id="e_time",
        store_id="ST1008",
        camera_id="CAM_ENT_01",
        visitor_id="VIS_1",
        event_type="ENTRY",
        timestamp="2026-04-10T12:00:00Z"
    ))
    # Add a billing join event with queue_depth > 5
    db_session.add(DBEvent(
        event_id="e_spike",
        store_id="ST1008",
        camera_id="CAM_BILL_01",
        visitor_id="VIS_2",
        event_type="BILLING_QUEUE_JOIN",
        timestamp="2026-04-10T12:00:00Z",
        queue_depth=6
    ))
    db_session.commit()

    anoms = detect_store_anomalies(db_session, "ST1008")
    spike_anom = [a for a in anoms if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    
    assert len(spike_anom) == 1
    assert spike_anom[0]["severity"] == "WARN"
    assert "depth has reached 6" in spike_anom[0]["message"]

def test_dead_zone_trigger(db_session):
    # If there are no browse events at all, it's a dead zone!
    db_session.add(DBEvent(
        event_id="e_time",
        store_id="ST1008",
        camera_id="CAM_ENT_01",
        visitor_id="VIS_1",
        event_type="ENTRY",
        timestamp="2026-04-10T12:30:00Z"
    ))
    db_session.commit()

    anoms = detect_store_anomalies(db_session, "ST1008")
    dead_zones = [a for a in anoms if a["anomaly_type"] == "DEAD_ZONE"]
    
    # Both BROWSE_ZONE_A and BROWSE_ZONE_B have 0 visits in the last 15 mins
    assert len(dead_zones) == 2
    assert dead_zones[0]["severity"] == "INFO"
    assert "No visitor traffic" in dead_zones[0]["message"]
