"""3D model of the whole wave maker, from geometry.json.

Writes three files to model/:

  tank_model.glb     glTF 2.0, one named node per part. Blender: File > Import > glTF 2.0.
  tank_model.obj     the same parts as Wavefront OBJ (+ .mtl), Y up, metres.
  tank_viewer.html   opens in a browser (three.js from a CDN); sliders for the still-water
                     depth and the disc position, and a switch for which end row 10 is at.

The model frame is x along the channel axis (0 at the channel mouth, + toward the
reservoir), y across it, z up from the tank floor. glTF and OBJ are Y-up, so they get
(x, z, -y). Parts that were estimated rather than measured (motors, frame posts) are
approximate and say so in their names.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from . import air
from .geometry import Geometry

MATERIALS = {
    # name: (rgb hex, opacity, metallic, roughness)
    "floor": ("#8e959c", 1.0, 0.1, 0.8),
    "glass": ("#bfe3ee", 0.16, 0.0, 0.05),
    "frame": ("#86b6ef", 1.0, 0.2, 0.6),
    "steel": ("#c9ccd1", 0.55, 0.25, 0.45),
    "plate": ("#a9adb3", 1.0, 0.25, 0.5),
    "side_plate": ("#a9adb3", 0.3, 0.25, 0.5),
    "disc": ("#26262a", 1.0, 0.2, 0.6),
    "motor": ("#1b1b1d", 1.0, 0.3, 0.5),
    "water": ("#2a78d6", 0.32, 0.0, 0.1),
    "pipe_water": ("#1c5cab", 0.75, 0.0, 0.1),
    "reservoir": ("#3987e5", 1.0, 0.1, 0.6),
    "gate": ("#52514e", 1.0, 0.2, 0.6),
    "lab_floor": ("#e4e3de", 1.0, 0.0, 0.9),
}


# ---------------------------------------------------------------------------
# Parts: a plain description shared by the exporters and the browser viewer

def _box(name, mat, x0, x1, y0, y1, z0, z1):
    return {"name": name, "kind": "box", "mat": mat,
            "p": [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2], "s": [x1 - x0, y1 - y0, z1 - z0]}


def _cyl(name, mat, x, y, z0, r, h, r_in=0.0):
    return {"name": name, "kind": "tube" if r_in else "cyl", "mat": mat, "p": [x, y, z0], "r": r, "ri": r_in, "h": h}


def tank_parts(g: Geometry) -> List[Dict]:
    """Everything except the piston bank and the water: floor, glass, frame, reservoir."""
    t = 0.02                                    # glass
    HL, HW, L, b = g.head_length, g.head_width, g.channel_length, g.channel_width
    rim, RL, RW, RH = g.channel_depth, g.reservoir_length, g.reservoir_width, g.reservoir_height
    lab = -g.floor_above_lab
    P = []
    P.append(_box("head floor", "floor", -HL - t, 0, -HW / 2 - t, HW / 2 + t, -0.04, 0))
    P.append(_box("channel floor", "floor", 0, L, -b / 2 - t, b / 2 + t, -0.04, 0))
    P.append(_box("head back wall (glass)", "glass", -HL - t, -HL, -HW / 2 - t, HW / 2 + t, 0, g.head_depth))
    for s, nm in ((-1, "right"), (1, "left")):
        P.append(_box("head %s wall (glass)" % nm, "glass", -HL, 0, min(s * HW / 2, s * (HW / 2 + t)),
                      max(s * HW / 2, s * (HW / 2 + t)), 0, g.head_depth))
        y_in, y_out = s * b / 2, s * HW / 2
        P.append(_box("head front wall %s of the mouth (glass)" % nm, "glass", 0, t, min(y_in, y_out), max(y_in, y_out),
                      0, g.head_depth))
        P.append(_box("channel %s wall (glass)" % nm, "glass", 0, L, min(s * b / 2, s * (b / 2 + t)),
                      max(s * b / 2, s * (b / 2 + t)), 0, rim))
        # frame: top rail, and the beams William counted (28 Oct) between 16 windows of the assumed width,
        # laid from the head-end corner; the corner beams are taken like the head's until measured
        yr = s * (b / 2 + t + 0.03)
        P.append(_box("channel %s top rail (frame, approx.)" % nm, "frame", 0, L, yr - 0.03, yr + 0.03, rim, rim + 0.08))
        csw = g.raw["channel"].get("side_wall", {})
        cwin = csw.get("window_width_in", 39.25) * 0.0254
        cbeams = [w * 0.0254 for w in csw.get("beams_between_in", [])]
        cinner = 2.375 * 0.0254
        cface = 5.0 * 0.0254
        spans = [("corner beam at the head", cinner - cface, cinner)]
        x = cinner
        for k in range(len(cbeams) + 1):
            x += cwin
            if k < len(cbeams):
                spans.append(("beam %d (%.0f in)" % (k + 1, cbeams[k] / 0.0254), x, x + cbeams[k]))
                x += cbeams[k]
        spans.append(("corner beam at the reservoir", x, x + cface))
        for nm2, xa, xb in spans:
            P.append(_box("channel %s %s" % (nm, nm2), "frame", xa, xb, yr - 0.03, yr + 0.03, lab, rim))
    # the head's frame: steel beams on the back wall's measured bay (William, 28 Oct), the same bay down
    # the long sides (a photo count there). A beam straddles the glass; its outer flange and web sit
    # outside the water face.
    bw = g.raw["head"].get("back_wall", {})
    IN = 0.0254
    face = bw.get("beam_face_in", 5.0) * IN
    out = (bw.get("beam_flange_in", 2.375) + bw.get("beam_web_in", 0.25)) * IN
    bay = (bw.get("window_clear_in", 35.0) * IN) + face
    nb = int(bw.get("beams", 4))
    yc = HW / 2 + out - face / 2                     # outermost beam centre
    for j, y in enumerate(np.linspace(-yc, yc, nb)):
        P.append(_box("head back beam %d" % j, "frame", -HL - t - 0.10, -HL - t, y - face / 2, y + face / 2,
                      lab, g.head_depth))
    # long sides (William, 28 Oct): windows of the measured width with the measured beams between them,
    # laid from the back corner; the corner beams are taken like the back wall's until measured
    sw = g.raw["head"].get("side_wall", {})
    win = sw.get("window_width_in", 39.25) * IN
    between = [b * IN for b in sw.get("beams_between_in", [5, 4, 5, 4, 5, 4, 5])]
    inner = bw.get("beam_flange_in", 2.375) * IN           # corner beam's inner flange, inside the water face
    spans = [("corner beam back", -HL + inner - face, -HL + inner)]
    x = -HL + inner
    for k in range(len(between) + 1):
        x += win
        if k < len(between):
            spans.append(("beam %d (%.0f in)" % (k + 1, between[k] / IN), x, x + between[k]))
            x += between[k]
    spans.append(("corner beam front", x, x + face))
    for nm, xa, xb in spans:
        for s in (-1, 1):
            y = s * (HW / 2 + t + 0.05)
            P.append(_box("head side %s %s" % ("left" if s > 0 else "right", nm), "frame", xa, xb, y - 0.05, y + 0.05,
                          lab, g.head_depth))
    P.append(_box("head under-frame (approx.)", "frame", -HL, 0, -HW / 2, HW / 2, lab + 0.05, lab + 0.12))
    # reservoir: blue column behind the gate
    w = 0.03
    P.append(_box("reservoir floor", "reservoir", L, L + RL, -RW / 2 - w, RW / 2 + w, -0.04, 0))
    P.append(_box("reservoir far wall", "reservoir", L + RL, L + RL + w, -RW / 2 - w, RW / 2 + w, 0, RH))
    for s, nm in ((-1, "right"), (1, "left")):
        P.append(_box("reservoir %s wall" % nm, "reservoir", L, L + RL, min(s * RW / 2, s * (RW / 2 + w)),
                      max(s * RW / 2, s * (RW / 2 + w)), 0, RH))
    P.append(_box("reservoir gate (closed: a reflecting wall)", "gate", L - 0.03, L, -RW / 2, RW / 2, 0, RH))
    P.append(_box("reservoir legs (approx.)", "frame", L, L + RL, -RW / 2, RW / 2, lab, -0.04))
    P.append(_box("channel stand (approx.)", "frame", 0, L, -b / 2, b / 2, lab + 0.05, lab + 0.10))
    P.append(_box("lab floor", "lab_floor", -HL - 1.5, L + RL + 1.5, -HW / 2 - 1.5, HW / 2 + 1.5, lab - 0.02, lab))
    return P


def bank_parts(g: Geometry, depth: float, disc_mm: float) -> List[Dict]:
    """The 30 pipes, their discs and motors, the water under each disc, and the bank's side plates."""
    P = []
    ro, ri = g.cylinder_od / 2, g.cylinder_bore / 2
    zd_park = g.disc_height(g.park_mm)
    zd = g.disc_height(disc_mm)
    for p in g.pistons():
        P.append(_cyl("pipe %d (row %d)" % (p.number, p.column), "steel", p.x, p.y, p.bottom, ro, g.pipe_top - p.bottom, ri))
        P.append(_cyl("disc %d" % p.number, "disc", p.x, p.y, zd - 0.01, ri * 0.995, 0.02))
        P.append(_box("top plate %d" % p.number, "plate", p.x - g.pitch_along / 2 + 0.002, p.x + g.pitch_along / 2 - 0.002,
                      p.y - g.pitch_across / 2 + 0.002, p.y + g.pitch_across / 2 - 0.002, g.pipe_top, g.pipe_top + 0.012))
        P.append(_box("LinMot motor %d (approx.)" % p.number, "motor", p.x - 0.03, p.x + 0.03, p.y - 0.045, p.y + 0.045,
                      g.pipe_top + 0.012, g.pipe_top + 0.36))
        st = air.rest_state(zd_park, zd, p.bottom, depth)
        if st["water_column_m"] > 0.002:
            P.append(_cyl("water in pipe %d" % p.number, "pipe_water", p.x, p.y, p.bottom, ri * 0.99, st["water_column_m"]))
    if g.duct_side_walls:
        x0, x1 = g.bank_x_range
        for s, nm in ((-1, "right"), (1, "left")):
            y = s * g.bank_width / 2
            P.append(_box("bank %s side plates" % nm, "side_plate", x0, x1, min(y, y + s * 0.012), max(y, y + s * 0.012), 0,
                          g.channel_depth))
    return P


def water_parts(g: Geometry, depth: float) -> List[Dict]:
    return [_box("water in the head", "water", -g.head_length, 0, -g.head_width / 2, g.head_width / 2, 0, depth),
            _box("water in the channel", "water", 0, g.channel_length, -g.channel_width / 2, g.channel_width / 2, 0, depth)]


# ---------------------------------------------------------------------------
# Meshes (model frame, z up)

def _mesh_box(p, s):
    cx, cy, cz = p
    hx, hy, hz = s[0] / 2, s[1] / 2, s[2] / 2
    faces = [((1, 0, 0), [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)]),
             ((-1, 0, 0), [(-1, 1, -1), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1)]),
             ((0, 1, 0), [(1, 1, -1), (-1, 1, -1), (-1, 1, 1), (1, 1, 1)]),
             ((0, -1, 0), [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)]),
             ((0, 0, 1), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
             ((0, 0, -1), [(-1, 1, -1), (1, 1, -1), (1, -1, -1), (-1, -1, -1)])]
    V, N, I = [], [], []
    for n, quad in faces:
        k = len(V)
        for a, b_, c in quad:
            V.append((cx + a * hx, cy + b_ * hy, cz + c * hz))
            N.append(n)
        I += [k, k + 1, k + 2, k, k + 2, k + 3]
    return V, N, I


def _mesh_cyl(p, r, h, r_in=0.0, seg=32):
    x0, y0, z0 = p
    V, N, I = [], [], []
    th = [2 * math.pi * i / seg for i in range(seg)]

    def ring(rad, z, normal_sign, outward=True):
        k = len(V)
        for a in th:
            V.append((x0 + rad * math.cos(a), y0 + rad * math.sin(a), z))
            N.append((normal_sign * math.cos(a), normal_sign * math.sin(a), 0.0) if outward else (0, 0, normal_sign))
        return k

    def side(rad, sgn):
        a, b_ = ring(rad, z0, sgn), ring(rad, z0 + h, sgn)
        for i in range(seg):
            j = (i + 1) % seg
            if sgn > 0:
                I.extend([a + i, a + j, b_ + j, a + i, b_ + j, b_ + i])
            else:
                I.extend([a + i, b_ + j, a + j, a + i, b_ + i, b_ + j])

    side(r, 1)
    if r_in:
        side(r_in, -1)
        for z, nz in ((z0, -1), (z0 + h, 1)):           # annular ends
            o, n_ = ring(r, z, nz, False), ring(r_in, z, nz, False)
            for i in range(seg):
                j = (i + 1) % seg
                if nz > 0:
                    I.extend([o + i, o + j, n_ + j, o + i, n_ + j, n_ + i])
                else:
                    I.extend([o + i, n_ + j, o + j, o + i, n_ + i, n_ + j])
    else:
        for z, nz in ((z0, -1), (z0 + h, 1)):
            c = len(V)
            V.append((x0, y0, z))
            N.append((0, 0, nz))
            k = ring(r, z, nz, False)
            for i in range(seg):
                j = (i + 1) % seg
                I.extend([c, k + i, k + j] if nz > 0 else [c, k + j, k + i])
    return V, N, I


def mesh(part):
    if part["kind"] == "box":
        return _mesh_box(part["p"], part["s"])
    return _mesh_cyl(part["p"], part["r"], part["h"], part.get("ri", 0.0))


def _yup(v):
    x, y, z = v
    return (x, z, -y)


def _rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def write_glb(parts: List[Dict], path: Path):
    mats = list(MATERIALS)
    gl = {"asset": {"version": "2.0", "generator": "tank-model wavemodel/model3d.py"},
          "scene": 0, "scenes": [{"name": "UNC wave tank", "nodes": []}], "nodes": [], "meshes": [],
          "materials": [], "accessors": [], "bufferViews": [], "buffers": []}
    for m in mats:
        col, op, met, rough = MATERIALS[m]
        mat = {"name": m, "pbrMetallicRoughness": {"baseColorFactor": _rgb(col) + [op], "metallicFactor": met,
                                                    "roughnessFactor": rough}, "doubleSided": True}
        if op < 1:
            mat["alphaMode"] = "BLEND"
        gl["materials"].append(mat)
    blob = bytearray()

    def view(data: bytes, target):
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(data)
        gl["bufferViews"].append({"buffer": 0, "byteOffset": off, "byteLength": len(data), "target": target})
        return len(gl["bufferViews"]) - 1

    for part in parts:
        V, N, I = mesh(part)
        V = np.array([_yup(v) for v in V], np.float32)
        N = np.array([_yup(n) for n in N], np.float32)
        I = np.array(I, np.uint32)
        vp = view(V.tobytes(), 34962)
        vn = view(N.tobytes(), 34962)
        vi = view(I.tobytes(), 34963)
        a0 = len(gl["accessors"])
        gl["accessors"] += [
            {"bufferView": vp, "componentType": 5126, "count": len(V), "type": "VEC3",
             "min": V.min(axis=0).tolist(), "max": V.max(axis=0).tolist()},
            {"bufferView": vn, "componentType": 5126, "count": len(N), "type": "VEC3"},
            {"bufferView": vi, "componentType": 5125, "count": len(I), "type": "SCALAR"}]
        gl["meshes"].append({"name": part["name"], "primitives": [
            {"attributes": {"POSITION": a0, "NORMAL": a0 + 1}, "indices": a0 + 2, "material": mats.index(part["mat"])}]})
        gl["nodes"].append({"name": part["name"], "mesh": len(gl["meshes"]) - 1})
        gl["scenes"][0]["nodes"].append(len(gl["nodes"]) - 1)
    while len(blob) % 4:
        blob.append(0)
    gl["buffers"].append({"byteLength": len(blob)})
    js = json.dumps(gl, separators=(",", ":")).encode()
    while len(js) % 4:
        js += b" "
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob)))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A))
        fh.write(js)
        fh.write(struct.pack("<II", len(blob), 0x004E4942))
        fh.write(bytes(blob))


def write_obj(parts: List[Dict], path: Path):
    mtl = path.with_suffix(".mtl")
    with open(mtl, "w") as fh:
        for m, (col, op, met, rough) in MATERIALS.items():
            r, g_, b = _rgb(col)
            fh.write("newmtl %s\nKd %.4f %.4f %.4f\nd %.3f\nNs %.0f\n\n" % (m, r, g_, b, op, 900 * (1 - rough)))
    vi = ni = 1
    with open(path, "w") as fh:
        fh.write("# UNC wave tank, metres, Y up (Blender's OBJ defaults import it upright)\nmtllib %s\n" % mtl.name)
        for part in parts:
            V, N, I = mesh(part)
            fh.write("o %s\nusemtl %s\n" % (part["name"].replace(" ", "_"), part["mat"]))
            for v in V:
                fh.write("v %.5f %.5f %.5f\n" % _yup(v))
            for n in N:
                fh.write("vn %.4f %.4f %.4f\n" % _yup(n))
            for k in range(0, len(I), 3):
                a, b, c = (I[k] + vi, I[k + 1] + vi, I[k + 2] + vi)
                fh.write("f %d//%d %d//%d %d//%d\n" % (a, a - vi + ni, b, b - vi + ni, c, c - vi + ni))
            vi += len(V)
            ni += len(N)


# ---------------------------------------------------------------------------

def viewer_data(g: Geometry, depth: float, disc_mm: float) -> Dict:
    pistons = []
    flipped = {"channel": "head", "head": "channel"}[g.row_10_end]
    g_alt = replace(g, row_10_end=flipped)
    for p in g.pistons():
        pistons.append({"n": p.number, "row": p.column, "lane": p.row, "y": p.y, "bottom": p.bottom,
                        "x": {g.row_10_end: p.x, flipped: g_alt.column_x(p.column)}})
    return {
        "tank": tank_parts(g), "materials": MATERIALS, "pistons": pistons,
        "g": {"head_length": g.head_length, "head_width": g.head_width, "channel_length": g.channel_length,
              "channel_width": g.channel_width, "rim": g.channel_depth, "reservoir_length": g.reservoir_length,
              "reservoir_width": g.reservoir_width, "reservoir_height": g.reservoir_height,
              "floor_above_lab": g.floor_above_lab, "pipe_top": g.pipe_top, "od": g.cylinder_od,
              "bore": g.cylinder_bore, "pitch_along": g.pitch_along, "pitch_across": g.pitch_across,
              "disc_zero": g.disc_zero, "park_mm": g.park_mm, "row_10_end": g.row_10_end,
              "bank_x0": g.bank_x_range[0], "bank_x1": g.bank_x_range[1], "bank_width": g.bank_width,
              "side_walls": g.duct_side_walls},
        "start": {"depth": depth, "disc_mm": disc_mm},
    }


def export(g: Geometry, depth: float, out: Path, disc_mm: float = 370.0) -> List[Path]:
    out.mkdir(parents=True, exist_ok=True)
    parts = tank_parts(g) + bank_parts(g, depth, disc_mm) + water_parts(g, depth)
    glb, obj = out / "tank_model.glb", out / "tank_model.obj"
    write_glb(parts, glb)
    write_obj(parts, obj)
    html = out / "tank_viewer.html"
    tpl = (Path(__file__).parent / "viewer_template.html").read_text()
    html.write_text(tpl.replace("/*DATA*/null", json.dumps(viewer_data(g, depth, disc_mm), separators=(",", ":"))))
    return [glb, obj, obj.with_suffix(".mtl"), html]
