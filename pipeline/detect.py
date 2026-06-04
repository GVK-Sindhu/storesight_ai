
import os
import cv2
import numpy as np
import torch
import uuid
from datetime import datetime, timedelta

# Try importing ultralytics for YOLOv8
try:
    from ultralytics import YOLO
    HAS_YOLO = True
except ImportError:
    HAS_YOLO = False

class PersonDetector:
    def __init__(self, use_yolo=True):
        self.use_yolo = use_yolo and HAS_YOLO
        self.model = None
        
        if self.use_yolo:
            print("Initializing YOLOv8 Detector...")
            try:
                # Load YOLOv8 Nano model (downloads if not present)
                self.model = YOLO("yolov8n.pt")
            except Exception as e:
                print(f"Failed to load YOLOv8, falling back to OpenCV HOG: {e}")
                self.use_yolo = False
                
        if not self.use_yolo:
            print("Initializing OpenCV HOG People Detector...")
            self.hog = cv2.HOGDescriptor()
            self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            
        # Staff uniform color profile (HSV range for Purplle staff uniform)
        # Purplle staff wear a distinct purple/violet uniform.
        # HSV range for purple: Hue 125-155, Saturation 40-255, Value 40-255.
        self.staff_color_lower = np.array([125, 40, 40])
        self.staff_color_upper = np.array([160, 255, 255])
        
    def detect(self, frame):
        """
        Detects people in a frame.
        Returns a list of bounding boxes: [x_min, y_min, x_max, y_max, confidence]
        """
        detections = []
        if self.use_yolo:
            results = self.model(frame, verbose=False)
            for r in results:
                boxes = r.boxes
                for box in boxes:
                    # Class 0 is 'person' in COCO dataset
                    if int(box.cls[0]) == 0:
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        conf = float(box.conf[0])
                        detections.append([int(x1), int(y1), int(x2), int(y2), conf])
        else:
            # OpenCV HOG detector fallback optimized for CPU speed
            h, w = frame.shape[:2]
            scale_w = 640.0 / w
            scale_h = 360.0 / h
            small_frame = cv2.resize(frame, (640, 360))
            boxes, weights = self.hog.detectMultiScale(small_frame, winStride=(16, 16), padding=(8, 8), scale=1.1)
            for (x, y, w_box, h_box), weight in zip(boxes, weights):
                x1 = int(x / scale_w)
                y1 = int(y / scale_h)
                x2 = int((x + w_box) / scale_w)
                y2 = int((y + h_box) / scale_h)
                detections.append([x1, y1, x2, y2, float(weight)])
                
        return detections

    def is_wearing_staff_uniform(self, frame, bbox):
        """
        Analyzes the bounding box area to see if the person is wearing the staff uniform.
        Looks at the lower half of the bounding box (representing shirt/torso).
        """
        x1, y1, x2, y2 = bbox[:4]
        h, w, _ = frame.shape
        
        # Ensure bounding box is within frame boundaries
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(x2)), min(h, int(y2))
        
        if (x2 - x1) <= 0 or (y2 - y1) <= 0:
            return False
            
        # Crop the torso region (middle 30% to 70% vertically, 20% to 80% horizontally)
        box_h = y2 - y1
        box_w = x2 - x1
        torso_y1 = int(y1 + 0.25 * box_h)
        torso_y2 = int(y1 + 0.65 * box_h)
        torso_x1 = int(x1 + 0.2 * box_w)
        torso_x2 = int(x1 + 0.8 * box_w)
        
        torso = frame[torso_y1:torso_y2, torso_x1:torso_x2]
        if torso.size == 0:
            return False
            
        # Convert to HSV and threshold for purple color
        hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.staff_color_lower, self.staff_color_upper)
        purple_ratio = np.sum(mask > 0) / mask.size
        
        # If more than 15% of the torso matches the purple color profile, it's staff
        return purple_ratio > 0.15
