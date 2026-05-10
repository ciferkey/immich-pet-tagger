"""Animal detector using YOLOv8n. Lazy-loads on first call."""

import logging
from PIL import Image

log = logging.getLogger("detector")

# COCO class IDs (0-indexed) for animals
ANIMAL_CLASS_IDS = {
    14,  # bird
    15,  # cat
    16,  # dog
    17,  # horse
    18,  # sheep
    19,  # cow
    20,  # elephant
    21,  # bear
    22,  # zebra
    23,  # giraffe
}

_yolo = None


def get_yolo():
    global _yolo
    if _yolo is None:
        from ultralytics import YOLO
        log.info("Loading YOLOv8n model...")
        _yolo = YOLO("yolov8n.pt")
        log.info("YOLO loaded")
    return _yolo


def detect_animals(img: Image.Image) -> list[tuple[float, float, float, float]]:
    """Returns (x1, y1, x2, y2) normalized bboxes for detected animals, sorted by confidence."""
    model = get_yolo()
    results = model(img, verbose=False)[0]
    boxes = []
    for box in results.boxes:
        cls = int(box.cls[0])
        if cls not in ANIMAL_CLASS_IDS:
            continue
        conf = float(box.conf[0])
        x1, y1, x2, y2 = box.xyxyn[0].tolist()
        boxes.append((conf, x1, y1, x2, y2))
    boxes.sort(reverse=True)
    return [(x1, y1, x2, y2) for _, x1, y1, x2, y2 in boxes]
