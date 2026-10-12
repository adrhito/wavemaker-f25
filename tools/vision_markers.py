"""Write the sheet of markers that gets printed and taped to the tank wall.

    python tools/vision_markers.py                 markers 0-5 to vision/markers.pdf
    python tools/vision_markers.py --first 6       the next six, for a second view

Each marker sits in a cell with tick marks at the middle of its four edges.
Cut along the dashed lines, tape the cell to the outside of the glass, and
measure to the ticks: they line up with the marker's centre, which is the only
part of it the camera uses.  The print does not have to be to scale.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from vision.calibration import MARKER_DICTIONARY  # noqa: E402


POINTS_PER_MM = 72.0 / 25.4
PAGE_MM = (215.9, 279.4)
MARKER_MM = 60.0
CELL_MM = (95.0, 85.0)
ACROSS, DOWN = 2, 3
TICK_MM = 5.0


def marker_cells(marker):
    """The marker as rows of True (black) and False (white), border included."""
    dictionary = cv2.aruco.getPredefinedDictionary(MARKER_DICTIONARY)
    image = cv2.aruco.generateImageMarker(dictionary, marker, 6)
    return [[bool(value < 128) for value in row] for row in image]


def _page(markers):
    """Drawing instructions for one page, in PDF's own language."""
    def pt(mm):
        return "{0:.3f}".format(mm * POINTS_PER_MM)

    left = (PAGE_MM[0] - ACROSS * CELL_MM[0]) / 2.0
    top = PAGE_MM[1] - 10.0
    out = []
    for place, marker in enumerate(markers):
        cell_x = left + (place % ACROSS) * CELL_MM[0]
        cell_y = top - (place // ACROSS + 1) * CELL_MM[1]
        middle_x = cell_x + CELL_MM[0] / 2.0
        middle_y = cell_y + CELL_MM[1] / 2.0

        out.append("0.6 G 0.4 w [4 3] 0 d {0} {1} {2} {3} re S [] 0 d".format(
            pt(cell_x), pt(cell_y), pt(CELL_MM[0]), pt(CELL_MM[1])))
        out.append("0 G 0.6 w")
        for x1, y1, x2, y2 in (
                (middle_x, cell_y, middle_x, cell_y + TICK_MM),
                (middle_x, cell_y + CELL_MM[1], middle_x, cell_y + CELL_MM[1] - TICK_MM),
                (cell_x, middle_y, cell_x + TICK_MM, middle_y),
                (cell_x + CELL_MM[0], middle_y, cell_x + CELL_MM[0] - TICK_MM, middle_y)):
            out.append("{0} {1} m {2} {3} l S".format(pt(x1), pt(y1), pt(x2), pt(y2)))

        # Black squares a hair oversize, so neighbours overlap instead of
        # leaving the white seams some printers draw between touching shapes.
        module = MARKER_MM / 6.0
        out.append("0 g")
        for row, cells in enumerate(marker_cells(marker)):
            for column, black in enumerate(cells):
                if black:
                    out.append("{0} {1} {2} {2} re f".format(
                        pt(middle_x - MARKER_MM / 2.0 + column * module - 0.02),
                        pt(middle_y + MARKER_MM / 2.0 - (row + 1) * module - 0.02),
                        pt(module + 0.04)))
        out.append("BT /F1 9 Tf {0} {1} Td (marker {2}) Tj ET".format(
            pt(cell_x + 3.0), pt(cell_y + 3.0), marker))

    out.append("0 g BT /F1 9 Tf {0} {1} Td (Measure each marker to its tick marks: "
               "distance along the tank, and height above the inside floor.) Tj ET"
               .format(pt(left), pt(8.0)))
    return "\n".join(out).encode("ascii")


def write_sheet(path, first=0, count=6):
    """Write ``count`` markers starting at ``first``, six to a page."""
    markers = list(range(first, first + count))
    pages = [markers[start:start + ACROSS * DOWN]
             for start in range(0, len(markers), ACROSS * DOWN)]
    kids = " ".join("{0} 0 R".format(4 + 2 * n) for n in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [{0}] /Count {1} >>".format(kids, len(pages)).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for number, page in enumerate(pages):
        drawing = _page(page)
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {0:.2f} {1:.2f}] "
            "/Resources << /Font << /F1 3 0 R >> >> /Contents {2} 0 R >>".format(
                PAGE_MM[0] * POINTS_PER_MM, PAGE_MM[1] * POINTS_PER_MM,
                5 + 2 * number).encode("ascii"))
        objects.append("<< /Length {0} >>\nstream\n".format(len(drawing)).encode("ascii")
                       + drawing + b"\nendstream")

    body = b"%PDF-1.4\n"
    offsets = []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body += "{0} 0 obj\n".format(number).encode("ascii") + content + b"\nendobj\n"
    table = len(body)
    body += "xref\n0 {0}\n0000000000 65535 f \n".format(len(objects) + 1).encode("ascii")
    for offset in offsets:
        body += "{0:010d} 00000 n \n".format(offset).encode("ascii")
    body += ("trailer\n<< /Size {0} /Root 1 0 R >>\nstartxref\n{1}\n%%EOF\n"
             .format(len(objects) + 1, table).encode("ascii"))
    Path(path).write_bytes(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--first", type=int, default=0, help="number of the first marker")
    parser.add_argument("--count", type=int, default=6, help="how many markers")
    parser.add_argument("--out", default=str(
        Path(__file__).resolve().parent.parent / "vision" / "markers.pdf"))
    args = parser.parse_args()
    write_sheet(args.out, args.first, args.count)
    print("Wrote " + args.out)


if __name__ == "__main__":
    main()
