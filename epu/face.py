"""Face recognition, on this PC, with no cloud and no key.

OpenCV does the work: a Haar cascade finds the face, an LBPH recogniser learns the
owner's face. Both ship inside opencv-python-headless, run on the CPU, and need no
internet and no account.

Flow:
    enrol    -> a few photos of the owner, saved as the label "owner";
    verify   -> a photo from the browser/phone, matched against the enrolled label;
    report   -> the distance and the decision. The distance is a measurement, never
                 a guess, and a photo with no face is never called a match.

Everything the office stores is a grayscale face crop (200x200) — no full photos are
kept, so a screenshot of the office leaks nothing about the owner's face.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

FACE_SIZE = 200
MAX_IMAGES = 24
DEFAULT_THRESHOLD = 70.0            # LBPH distance: <= threshold means "the owner"
SCALE = 1.1
NEIGHBOURS = 5
MIN_FACE = 60

# The face model ships with the office, not with a package: one file, one place.
MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "face"
CASCADE_NAME = "haarcascade_frontalface_default.xml"


class FaceUnavailable(RuntimeError):
    pass


def _cascade() -> cv2.CascadeClassifier:
    """The face model: the office's own copy first, the package's copy as fallback."""
    local = MODEL_DIR / CASCADE_NAME
    candidates = [local] if local.exists() else [
        Path(cv2.data.haarcascades) / CASCADE_NAME,
        local,
    ]
    for path in candidates:
        if path.exists():
            classifier = cv2.CascadeClassifier(str(path))
            if not classifier.empty():
                return classifier
    raise FaceUnavailable(
        f"face model missing ({local}). Download {CASCADE_NAME} from "
        "opencv/opencv 4.x data/haarcascades into models/face/")


def decode(image_bytes) -> np.ndarray:
    """jpeg/png bytes (or a browser data URL) -> BGR image."""
    import base64

    if isinstance(image_bytes, str):
        payload = image_bytes.split(",", 1)[1] if "," in image_bytes else image_bytes
        image_bytes = base64.b64decode(payload)
    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise FaceUnavailable("not an image the office can read")
    return image


def find_face(image: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    """The biggest face in a photo -> (grayscale 200x200 crop, box). None when no face."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    faces = _cascade().detectMultiScale(gray, scaleFactor=SCALE, minNeighbors=NEIGHBOURS,
                                        minSize=(MIN_FACE, MIN_FACE))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
    crop = cv2.resize(gray[y:y + h, x:x + w], (FACE_SIZE, FACE_SIZE))
    return crop, (int(x), int(y), int(w), int(h))


def _preview(crop: np.ndarray) -> ImagePreview:  # type: ignore[name-defined]
    return crop


@dataclass
class FaceID:
    root: Path
    threshold: float = DEFAULT_THRESHOLD
    state: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._load()

    # ---------------------------------------------------------------- state
    @property
    def samples_dir(self) -> Path:
        return self.root / "samples"

    @property
    def model_file(self) -> Path:
        return self.root / "model.xml"

    @property
    def state_file(self) -> Path:
        return self.root / "face-id.json"

    def _load(self) -> None:
        if self.state_file.exists():
            try:
                self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.state = {}

    def _save(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- enrol
    def enrol(self, image_bytes: bytes) -> dict:
        """Add one photo of the owner. A photo with no face is rejected, not stored."""
        image = decode(image_bytes)
        found = find_face(image)
        if found is None:
            return {"ok": False, "error": "no face found in that photo"}
        crop, _box = found
        self.samples_dir.mkdir(parents=True, exist_ok=True)
        name = f"owner-{int(len(list(self.samples_dir.glob('owner-*.png')))):02d}.png"
        cv2.imwrite(str(self.samples_dir / name), crop)
        if len(list(self.samples_dir.glob("owner-*.png"))) > MAX_IMAGES:
            oldest = sorted(self.samples_dir.glob("owner-*.png"))[0]
            oldest.unlink()
        result = self.train()
        if result.get("ok"):
            self.state = {"enrolled": True, "samples": len(
                list(self.samples_dir.glob("owner-*.png"))), "threshold": self.threshold}
            self._save()
        return {"ok": result.get("ok", False), "samples": self.state.get("samples", 0),
                "error": result.get("error")}

    def train(self) -> dict:
        files = sorted(self.samples_dir.glob("owner-*.png"))
        if not files:
            return {"ok": False, "error": "no samples to learn from"}
        images, labels = [], []
        for path in files:
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is not None:
                images.append(image)
                labels.append(1)                       # label 1 = the owner
        if not images:
            return {"ok": False, "error": "no readable samples"}
        import cv2 as _cv2

        model = _cv2.face.LBPHFaceRecognizer_create()
        model.train(images, np.asarray(labels, dtype=np.int32))
        model.write(str(self.model_file))
        self.state = {"enrolled": True, "samples": len(images),
                      "threshold": self.threshold, "model": str(self.model_file)}
        self._save()
        return {"ok": True, "samples": len(images)}

    def enrolled(self) -> bool:
        return bool(self.state.get("enrolled")) and self.model_file.exists()

    # ---------------------------------------------------------------- verify
    def verify(self, image_bytes: bytes) -> dict:
        """Match a photo. Returns a decision, the measured distance, and the box."""
        if not self.enrolled():
            return {"ok": False, "error": "no face enrolled yet", "distance": None}
        image = decode(image_bytes)
        found = find_face(image)
        if found is None:
            return {"ok": False, "error": "no face found in that photo", "distance": None}
        crop, box = found
        import cv2 as _cv2

        model = _cv2.face.LBPHFaceRecognizer_create()
        model.read(str(self.model_file))
        label, distance = model.predict(crop)
        decision = bool(label == 1 and distance <= self.threshold)
        return {"ok": decision, "distance": round(float(distance), 2),
                "threshold": self.threshold, "box": list(box),
                "samples": self.state.get("samples", 0)}

    def forget(self) -> dict:
        for path in list(self.samples_dir.glob("*.png")) + [self.model_file, self.state_file]:
            try:
                path.unlink()
            except OSError:
                pass
        self.state = {}
        return {"ok": True, "forgotten": True}

    def wizard(self, image_bytes: bytes) -> dict:
        """One call for the enrolment page: enrol, then report where we stand."""
        if not self.enrolled() and not self.samples_dir.exists():
            result = self.enrol(image_bytes)
            if not result.get("ok"):
                return result
            self.state = {"enrolled": True, "samples": 1, "threshold": self.threshold}
            self._save()
            return {"ok": True, "samples": 1, "need_more": True,
                    "message": "one photo in, sir — take 4 more from different angles"}
        result = self.enrol(image_bytes)
        if not result.get("ok"):
            return result
        return {"ok": True, "samples": result["samples"],
                "ready": self.enrolled(), "need_more": result["samples"] < 4}
