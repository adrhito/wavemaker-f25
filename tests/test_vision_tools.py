"""The marker sheet prints what the camera looks for, and a clip measures end to end."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import vision_markers  # noqa: E402
import vision_measure  # noqa: E402
from vision.calibration import MARKER_DICTIONARY, find_markers  # noqa: E402
from vision.synthetic import SyntheticTank  # noqa: E402


def test_sheet_cells_are_the_markers_the_detector_knows():
    dictionary = cv2.aruco.getPredefinedDictionary(MARKER_DICTIONARY)
    for marker in (0, 5, 11):
        cells = np.array(vision_markers.marker_cells(marker))
        assert cells.shape == (6, 6)
        assert cells[0].all() and cells[-1].all() and cells[:, 0].all() and cells[:, -1].all()
        drawn = np.where(np.kron(cells, np.ones((40, 40))), 0, 255).astype(np.uint8)
        page = np.full((400, 400), 255, dtype=np.uint8)
        page[80:320, 80:320] = drawn
        assert list(find_markers(page)) == [marker]
        assert np.array_equal(cells, cv2.aruco.generateImageMarker(dictionary, marker, 6) < 128)


def test_sheet_is_a_pdf_with_a_page_per_six_markers(tmp_path):
    vision_markers.write_sheet(tmp_path / "one.pdf")
    vision_markers.write_sheet(tmp_path / "two.pdf", first=6, count=8)
    one, two = (tmp_path / "one.pdf").read_bytes(), (tmp_path / "two.pdf").read_bytes()
    assert one.startswith(b"%PDF-1.4") and one.rstrip().endswith(b"%%EOF")
    assert one.count(b"/Type /Page ") == 1 and two.count(b"/Type /Page ") == 2
    assert b"(marker 5)" in one and b"(marker 13)" in two and b"(marker 6)" not in one
    # The table at the end must point at each object, or strict readers refuse it.
    table = int(one.rsplit(b"startxref", 1)[1].split()[0])
    assert one[table:table + 4] == b"xref"
    for line in one[table:].splitlines()[2:2 + 5]:
        offset = int(line.split()[0])
        if offset:
            assert one[offset:offset + 12].split()[1:3] == [b"0", b"obj"]


def _write_clip(path, tank, rate, seconds):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), rate,
                             tank.image_size)
    if not writer.isOpened():
        pytest.skip("this OpenCV build cannot write an mp4 to test with")
    for number in range(int(seconds * rate)):
        # Still water for the first second, then the wave's own clock.
        writer.write(tank.frame(max(number / rate - 1.0, 0.0)))
    writer.release()


def test_a_recorded_clip_is_measured_and_its_evidence_saved(tmp_path):
    tank = SyntheticTank()
    clip = tmp_path / "trial.mp4"
    _write_clip(clip, tank, 20.0, 1.0 + tank.crest_time_s(1400.0) + 1.0)
    tank.rig.save(tmp_path / "rig.json")

    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "vision_measure.py"), str(clip),
         "--rig", str(tmp_path / "rig.json"), "--depth-mm", "150"],
        capture_output=True, text=True, check=True)
    record = json.loads(done.stdout)
    assert record["crest_seen"] is True
    assert record["crest_rise_mm"] == pytest.approx(tank.crest_mm, abs=1.0)
    assert record["speed_mm_s"] == pytest.approx(tank.speed_mm_s, rel=0.02)
    assert record["fwhm_mm"] == pytest.approx(tank.fwhm_mm, rel=0.04)
    assert record["still_depth_mm"] == pytest.approx(150.0, abs=0.5)
    assert done.stderr == ""

    saved = np.load(str(tmp_path / "trial.surface.npz"))
    assert saved["height_mm"].shape == (saved["times_s"].size, saved["x_mm"].size)
    assert cv2.imread(str(tmp_path / "trial.crest.png")).shape[:2] == (540, 960)


def test_a_clip_with_no_still_water_at_the_start_is_refused(tmp_path):
    tank = SyntheticTank()
    clip = tmp_path / "short.mp4"
    _write_clip(clip, tank, 20.0, 0.5)
    with pytest.raises(RuntimeError, match="shorter than the still water"):
        vision_measure.measure_clip(clip, tank.rig, still_seconds=2.0)
