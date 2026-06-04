
import pytest
import numpy as np
from datetime import datetime
from pipeline.tracker import Track, MultiCameraTracker
from pipeline.detect import PersonDetector

def test_track_initialization():
    track_id = "test_track_123"
    bbox = [100, 150, 200, 300, 0.95]
    timestamp = datetime.utcnow()
    track = Track(track_id, bbox, frame_idx=0, timestamp=timestamp)
    
    assert track.track_id == track_id
    assert track.visitor_id.startswith("VIS_")
    assert track.centroid == (150.0, 225.0)
    assert len(track.history) == 1
    assert track.lost_count == 0
    assert not track.is_staff

def test_track_update():
    track_id = "test_track_123"
    bbox1 = [100, 150, 200, 300, 0.95]
    timestamp1 = datetime.utcnow()
    track = Track(track_id, bbox1, frame_idx=0, timestamp=timestamp1)
    
    bbox2 = [105, 155, 205, 305, 0.90]
    timestamp2 = timestamp1 + datetime.resolution
    track.update(bbox2, frame_idx=5, timestamp=timestamp2)
    
    assert track.centroid == (155.0, 230.0)
    assert len(track.history) == 2
    assert track.lost_count == 0

def test_multi_camera_tracker_iou():
    tracker = MultiCameraTracker()
    box1 = [0, 0, 10, 10]
    box2 = [2, 2, 12, 12]
    iou = tracker.compute_iou(box1, box2)
    
    # Intersection = 8 * 8 = 64
    # Area1 = 100, Area2 = 100
    # Union = 100 + 100 - 64 = 136
    # IoU = 64 / 136 = 0.47058
    assert abs(iou - 0.47) < 0.01

def test_tracker_update_camera():
    tracker = MultiCameraTracker()
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    timestamp = datetime.utcnow()
    
    # 1. First frame - create a new track
    detections = [[100, 100, 200, 300, 0.9]]
    active_tracks, events = tracker.update_camera("CAM_ENT_01", detections, frame, frame_idx=0, timestamp=timestamp)
    
    assert len(active_tracks) == 1
    assert len(events) == 1
    assert events[0]['event_type'] == 'NEW_ENTRY'
    track_id = list(active_tracks.keys())[0]
    
    # 2. Second frame - track updates (IoU match)
    detections_2 = [[105, 105, 205, 305, 0.9]]
    active_tracks_2, events_2 = tracker.update_camera("CAM_ENT_01", detections_2, frame, frame_idx=5, timestamp=timestamp)
    
    assert len(active_tracks_2) == 1
    assert track_id in active_tracks_2
    assert len(events_2) == 0  # No new entry event

def test_staff_camera_exclusion():
    tracker = MultiCameraTracker()
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    timestamp = datetime.utcnow()
    
    detections = [[100, 100, 200, 300, 0.9]]
    # Ingesting from staff-only restroom camera should mark visitor as staff
    active_tracks, events = tracker.update_camera("CAM_STAFF_01", detections, frame, frame_idx=0, timestamp=timestamp)
    
    assert len(active_tracks) == 1
    track = list(active_tracks.values())[0]
    assert track.is_staff == True
