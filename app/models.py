
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

# SQLAlchemy Event Model
class DBEvent(Base):
    __tablename__ = "events"
    
    event_id = Column(String, primary_key=True, index=True)
    store_id = Column(String, nullable=False, index=True)
    camera_id = Column(String, nullable=False)
    visitor_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    timestamp = Column(String, nullable=False)  # ISO-8601 string
    zone_id = Column(String, nullable=True)
    dwell_ms = Column(Integer, default=0)
    is_staff = Column(Boolean, default=False)
    confidence = Column(Float, default=1.0)
    
    # Metadata columns from original schema
    queue_depth = Column(Integer, nullable=True)
    sku_zone = Column(String, nullable=True)
    session_seq = Column(Integer, default=0)

    # Demographics and group attributes from new schema
    gender = Column(String, nullable=True)
    age = Column(Integer, nullable=True)
    age_bucket = Column(String, nullable=True)
    is_face_hidden = Column(Boolean, nullable=True)
    group_id = Column(String, nullable=True)
    group_size = Column(Integer, nullable=True)

    # Spatial hotspot coordinates from new schema
    zone_name = Column(String, nullable=True)
    zone_type = Column(String, nullable=True)
    is_revenue_zone = Column(String, nullable=True)
    zone_hotspot_x = Column(Float, nullable=True)
    zone_hotspot_y = Column(Float, nullable=True)

    # Queue-specific fields from new schema
    queue_join_ts = Column(String, nullable=True)
    queue_served_ts = Column(String, nullable=True)
    queue_exit_ts = Column(String, nullable=True)
    wait_seconds = Column(Integer, nullable=True)
    queue_position_at_join = Column(Integer, nullable=True)
    abandoned = Column(Boolean, nullable=True)

# SQLAlchemy Transaction Model
class DBTransaction(Base):
    __tablename__ = "transactions"
    
    order_id = Column(String, primary_key=True, index=True)
    store_id = Column(String, nullable=False, index=True)
    brand_name = Column(String, nullable=False)
    total_amount = Column(Float, default=0.0)
    order_time = Column(String, nullable=False)  # HH:MM:SS
    order_date = Column(String, nullable=False)  # YYYY-MM-DD
    timestamp = Column(String, nullable=False)   # ISO-8601 string for easy matching

# Pydantic Event Metadata Schema (Old schema compatibility)
class EventMetadata(BaseModel):
    queue_depth: Optional[int] = Field(None, description="Current queue depth at billing counter join")
    sku_zone: Optional[str] = Field(None, description="Category of SKU mapped to browse zone")
    session_seq: int = Field(0, description="Session event sequence order")

# Pydantic Unified Event Schema for polymorphic Ingestion
class UnifiedEventIngestSchema(BaseModel):
    # Common / Old fields
    event_id: Optional[str] = Field(None, description="Globally unique UUIDv4 identifier")
    store_id: Optional[str] = Field(None, description="Target Store ID (e.g. ST1008)")
    camera_id: str = Field(..., description="Source camera ID")
    visitor_id: Optional[str] = Field(None, description="Unique Visitor Session Token")
    event_type: str = Field(..., description="Event action")
    timestamp: Optional[str] = Field(None, description="ISO-8601 UTC timestamp")
    zone_id: Optional[str] = Field(None, description="Target Zone ID")
    dwell_ms: Optional[int] = Field(0, description="Dwell duration in milliseconds")
    is_staff: Optional[bool] = Field(False, description="True if visitor is classified as staff")
    confidence: Optional[float] = Field(1.0, description="Confidence score")
    metadata: Optional[EventMetadata] = Field(None, description="Old metadata structure")

    # Gate events fields
    id_token: Optional[str] = Field(None, description="Session ID token")
    store_code: Optional[str] = Field(None, description="Store code")
    event_timestamp: Optional[str] = Field(None, description="Gate event timestamp")
    gender_pred: Optional[str] = Field(None, description="Predicted gender")
    age_pred: Optional[int] = Field(None, description="Predicted age")
    age_bucket: Optional[str] = Field(None, description="Age bracket")
    is_face_hidden: Optional[bool] = Field(None, description="True if face is blurred/hidden")
    group_id: Optional[str] = Field(None, description="Group ID")
    group_size: Optional[int] = Field(None, description="Group size")

    # Zone events fields
    track_id: Optional[int] = Field(None, description="Track ID inside camera")
    zone_name: Optional[str] = Field(None, description="Name of zone")
    zone_type: Optional[str] = Field(None, description="Type of zone")
    is_revenue_zone: Optional[str] = Field(None, description="Revenue generator indicator")
    event_time: Optional[str] = Field(None, description="Zone event timestamp")
    zone_hotspot_x: Optional[float] = Field(None, description="X coordinate center")
    zone_hotspot_y: Optional[float] = Field(None, description="Y coordinate center")
    gender: Optional[str] = Field(None, description="Demographic gender")
    age: Optional[int] = Field(None, description="Demographic age")

    # Queue events fields
    queue_event_id: Optional[str] = Field(None, description="Queue event UUID")
    queue_join_ts: Optional[str] = Field(None, description="Queue join timestamp")
    queue_served_ts: Optional[str] = Field(None, description="Queue served timestamp")
    queue_exit_ts: Optional[str] = Field(None, description="Queue exit timestamp")
    wait_seconds: Optional[int] = Field(None, description="Queue wait duration in seconds")
    queue_position_at_join: Optional[int] = Field(None, description="Position when joining queue")
    abandoned: Optional[bool] = Field(None, description="True if queue was abandoned")
