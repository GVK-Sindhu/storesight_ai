
import numpy as np
import uuid
import cv2
from datetime import datetime, timedelta

class Track:
    def __init__(self, track_id, bbox, frame_idx, timestamp, is_staff=False, color_hist=None):
        self.track_id = track_id
        self.visitor_id = f"VIS_{track_id[:6]}"  # Initial visitor ID mapping
        self.bbox = bbox  # [x1, y1, x2, y2, conf]
        self.centroid = self.get_centroid(bbox)
        self.history = [self.centroid]
        self.bboxes = [bbox]
        self.timestamps = [timestamp]
        self.frame_indices = [frame_idx]
        self.lost_count = 0
        self.is_staff = is_staff
        self.color_hist = color_hist
        self.zones_visited = set()
        self.last_zone = None
        self.dwell_start_times = {}  # zone_id -> start_time
        self.last_dwell_emission = {}  # zone_id -> last_emission_time
        self.session_seq = 0

    def get_centroid(self, bbox):
        x1, y1, x2, y2 = bbox[:4]
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    def update(self, bbox, frame_idx, timestamp):
        self.bbox = bbox
        self.centroid = self.get_centroid(bbox)
        self.history.append(self.centroid)
        self.bboxes.append(bbox)
        self.timestamps.append(timestamp)
        self.frame_indices.append(frame_idx)
        self.lost_count = 0

class MultiCameraTracker:
    def __init__(self, max_lost=45, min_iou=0.15, max_dist=150):
        self.max_lost = max_lost  # 1.5 seconds at 30 fps
        self.min_iou = min_iou
        self.max_dist = max_dist
        self.active_tracks = {}  # camera_id -> {track_id: Track}
        self.global_history = []  # list of exited Tracks (for Re-ID / Re-entry)
        
    def compute_iou(self, boxA, boxB):
        # Determine the (x, y)-coordinates of the intersection rectangle
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        
        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[0]) # wait boxA area
        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[0])
        
        # Correct area calculations
        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        
        unionArea = boxAArea + boxBArea - interArea
        if unionArea == 0:
            return 0
        return interArea / float(unionArea)

    def compute_color_hist(self, frame, bbox):
        """
        Computes 2D HSV color histogram of the torso region.
        """
        x1, y1, x2, y2 = bbox[:4]
        h, w, _ = frame.shape
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(x2)), min(h, int(y2))
        
        if (x2 - x1) <= 0 or (y2 - y1) <= 0:
            return None
            
        # Crop the torso region
        box_h = y2 - y1
        box_w = x2 - x1
        torso_y1 = int(y1 + 0.25 * box_h)
        torso_y2 = int(y1 + 0.65 * box_h)
        torso_x1 = int(x1 + 0.2 * box_w)
        torso_x2 = int(x1 + 0.8 * box_w)
        
        torso = frame[torso_y1:torso_y2, torso_x1:torso_x2]
        if torso.size == 0:
            return None
            
        hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
        # Compute histogram for Hue (8 bins) and Saturation (8 bins)
        hist = cv2.calcHist([hsv], [0, 1], None, [8, 8], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist

    def compare_color_hists(self, hist1, hist2):
        if hist1 is None or hist2 is None:
            return 0.0
        # Correlation method (higher is better, max 1.0)
        return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)

    def update_camera(self, camera_id, detections, frame, frame_idx, timestamp):
        """
        Updates tracks for a single camera.
        detections: list of [x1, y1, x2, y2, conf]
        """
        if camera_id not in self.active_tracks:
            self.active_tracks[camera_id] = {}
            
        curr_tracks = self.active_tracks[camera_id]
        
        # 1. Compute properties for all detections
        dets_info = []
        for det in detections:
            hist = self.compute_color_hist(frame, det)
            dets_info.append({
                'bbox': det,
                'centroid': ((det[0]+det[2])/2.0, (det[1]+det[3])/2.0),
                'hist': hist
            })
            
        # 2. Match detections with active tracks
        matched_det_indices = set()
        matched_track_ids = set()
        
        track_ids = list(curr_tracks.keys())
        
        # Match using IoU first (high overlap)
        for t_id in track_ids:
            track = curr_tracks[t_id]
            best_iou = -1
            best_det_idx = -1
            
            for d_idx, det in enumerate(dets_info):
                if d_idx in matched_det_indices:
                    continue
                iou = self.compute_iou(track.bbox, det['bbox'])
                if iou > best_iou:
                    best_iou = iou
                    best_det_idx = d_idx
                    
            if best_iou >= self.min_iou:
                # Update track
                det = dets_info[best_det_idx]
                track.update(det['bbox'], frame_idx, timestamp)
                if det['hist'] is not None:
                    track.color_hist = det['hist']
                matched_det_indices.add(best_det_idx)
                matched_track_ids.add(t_id)

        # Match remaining tracks using Centroid Distance
        for t_id in track_ids:
            if t_id in matched_track_ids:
                continue
            track = curr_tracks[t_id]
            best_dist = float('inf')
            best_det_idx = -1
            
            for d_idx, det in enumerate(dets_info):
                if d_idx in matched_det_indices:
                    continue
                dist = np.linalg.norm(np.array(track.centroid) - np.array(det['centroid']))
                if dist < best_dist:
                    best_dist = dist
                    best_det_idx = d_idx
                    
            if best_dist < self.max_dist:
                det = dets_info[best_det_idx]
                track.update(det['bbox'], frame_idx, timestamp)
                if det['hist'] is not None:
                    track.color_hist = det['hist']
                matched_det_indices.add(best_det_idx)
                matched_track_ids.add(t_id)

        # 3. Handle unmatched tracks (lost/exited)
        exited_tracks = []
        for t_id in track_ids:
            if t_id not in matched_track_ids:
                track = curr_tracks[t_id]
                track.lost_count += 1
                if track.lost_count > self.max_lost:
                    exited_tracks.append(t_id)

        # Remove exited tracks from active list and move to global history
        for t_id in exited_tracks:
            track = curr_tracks[t_id]
            # Flag staff restroom/back office cameras directly
            if camera_id in ["CAM_STAFF_01", "CAM_BACK_01"]:
                track.is_staff = True
            self.global_history.append(track)
            del curr_tracks[t_id]

        # 4. Handle unmatched detections (new tracks)
        new_events = []
        for d_idx, det in enumerate(dets_info):
            if d_idx in matched_det_indices:
                continue
                
            # Create a new track
            track_id = str(uuid.uuid4())
            is_staff = False
            if camera_id in ["CAM_STAFF_01", "CAM_BACK_01"]:
                is_staff = True
                
            new_track = Track(track_id, det['bbox'], frame_idx, timestamp, is_staff, det['hist'])
            
            # Check for Re-ID / Re-entry (compare color histogram with recently exited tracks)
            reidentified = False
            best_reid_match = None
            best_similarity = -1.0
            
            # Check only active and global tracks from ST1008 within 5 minutes
            for old_track in self.global_history:
                # Time gap between old track last timestamp and new track current timestamp
                time_gap = (timestamp - old_track.timestamps[-1]).total_seconds()
                if 0 <= time_gap <= 300:  # 5 minutes
                    similarity = self.compare_color_hists(new_track.color_hist, old_track.color_hist)
                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_reid_match = old_track
                        
            # If similarity is high, Re-ID matches
            if best_similarity > 0.85 and best_reid_match is not None:
                new_track.visitor_id = best_reid_match.visitor_id
                new_track.is_staff = best_reid_match.is_staff
                reidentified = True
                new_events.append({
                    'event_type': 'REENTRY',
                    'track': new_track
                })
                
            curr_tracks[track_id] = new_track
            if not reidentified:
                new_events.append({
                    'event_type': 'NEW_ENTRY',
                    'track': new_track
                })
                
        return curr_tracks, new_events
