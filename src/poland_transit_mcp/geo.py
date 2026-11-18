"""Small geometry helpers. Coordinates are (lat, lon) everywhere."""
from __future__ import annotations

import math
from array import array

R = 6371008.8
RAD = math.pi / 180

LatLon = tuple[float, float]


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat = (lat2 - lat1) * RAD
    d_lon = (lon2 - lon1) * RAD
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1 * RAD) * math.cos(lat2 * RAD) * math.sin(d_lon / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def fast_dist(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Planar distance in metres, fine at city scale."""
    x = (lon2 - lon1) * RAD * math.cos((lat1 + lat2) / 2 * RAD)
    y = (lat2 - lat1) * RAD
    return math.sqrt(x * x + y * y) * R


def pt(lat: float, lon: float) -> list[float]:
    """[lat, lon] rounded to 5 decimals (~1 m), the output format of every tool."""
    return [round(float(lat), 5), round(float(lon), 5)]


def simplify(points: list[LatLon], tol_m: float) -> list[LatLon]:
    """Douglas–Peucker simplification, tolerance in metres."""
    n = len(points)
    if n <= 2 or tol_m <= 0:
        return list(points)
    kx = math.cos(points[0][0] * RAD) * R * RAD
    ky = R * RAD
    xs = [p[1] * kx for p in points]
    ys = [p[0] * ky for p in points]
    keep = bytearray(n)
    keep[0] = keep[n - 1] = 1
    stack = [(0, n - 1)]
    tol2 = tol_m * tol_m
    while stack:
        a, b = stack.pop()
        dx, dy = xs[b] - xs[a], ys[b] - ys[a]
        len2 = dx * dx + dy * dy
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            t = ((xs[i] - xs[a]) * dx + (ys[i] - ys[a]) * dy) / len2 if len2 > 0 else 0.0
            t = 0.0 if t < 0 else 1.0 if t > 1 else t
            ex, ey = xs[a] + t * dx - xs[i], ys[a] + t * dy - ys[i]
            d = ex * ex + ey * ey
            if d > best:
                best, idx = d, i
        if best > tol2 and idx > 0:
            keep[idx] = 1
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k in zip(points, keep) if k]


def encode_parts(parts: list[list[LatLon]]) -> bytes:
    """Int32 header [n_parts, len1..lenN] + int32 lat/lon *1e5 pairs."""
    out = array("i", [len(parts)] + [len(p) for p in parts])
    for p in parts:
        for lat, lon in p:
            out.append(round(lat * 1e5))
            out.append(round(lon * 1e5))
    return out.tobytes()


def decode_parts(buf: bytes) -> list[list[LatLon]]:
    a = array("i")
    a.frombytes(buf)
    n = a[0]
    parts, o = [], 1 + n
    for i in range(n):
        ln = a[1 + i]
        parts.append([(a[o + 2 * j] / 1e5, a[o + 2 * j + 1] / 1e5) for j in range(ln)])
        o += 2 * ln
    return parts


def bbox_of(points: list[LatLon]) -> tuple[float, float, float, float]:
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return min(lats), min(lons), max(lats), max(lons)


def point_in_rings(lat: float, lon: float, rings: list[list[LatLon]]) -> bool:
    """Even-odd rule over all rings, so holes work."""
    inside = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            yi, xi = ring[i]
            yj, xj = ring[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def centroid(ring: list[LatLon]) -> LatLon:
    a = cx = cy = 0.0
    j = len(ring) - 1
    for i in range(len(ring)):
        f = ring[j][1] * ring[i][0] - ring[i][1] * ring[j][0]
        a += f
        cx += (ring[j][1] + ring[i][1]) * f
        cy += (ring[j][0] + ring[i][0]) * f
        j = i
    if abs(a) < 1e-12:
        return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
    return cy / (3 * a), cx / (3 * a)


def ring_area(r: list[LatLon]) -> float:
    """Approximate ring area in m²."""
    if len(r) < 3:
        return 0.0
    k = math.cos(r[0][0] * RAD) * 111320 * 111320
    a = 0.0
    j = len(r) - 1
    for i in range(len(r)):
        a += (r[j][1] - r[i][1]) * (r[j][0] + r[i][0])
        j = i
    return abs(a / 2) * k


def _touches(p: LatLon, q: LatLon, box) -> bool:
    """Whether the segment p-q touches the box (a, b, c, d) (Liang-Barsky): also when both ends are outside it."""
    a, b, c, d = box
    t0, t1 = 0.0, 1.0
    for dp, lo, hi, p0 in ((q[0] - p[0], a, c, p[0]), (q[1] - p[1], b, d, p[1])):
        if dp == 0:
            if not lo <= p0 <= hi:
                return False
            continue
        ta, tb = (lo - p0) / dp, (hi - p0) / dp
        t0, t1 = max(t0, min(ta, tb)), min(t1, max(ta, tb))
        if t0 > t1:
            return False
    return True


def clip_line(points: list[LatLon], box) -> list[list[LatLon]]:
    """The runs of a line that touch the box (a, b, c, d): every segment that crosses it, so each run reaches the
    edge with its first point beyond it."""
    runs, cur = [], []
    for p, q in zip(points, points[1:]):
        if _touches(p, q, box):
            if not cur:
                cur = [p]
            cur.append(q)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def clip_ring(ring: list[LatLon], box) -> list[LatLon]:
    """A polygon ring cut to the box (Sutherland-Hodgman); empty when nothing of it is inside."""
    a, b, c, d = box
    edges = ((0, a, True), (0, c, False), (1, b, True), (1, d, False))  # (axis, value, keep values above it)
    out = list(ring)
    for axis, v, above in edges:
        if not out:
            break
        src, out = out, []
        for i, cur in enumerate(src):
            prev = src[i - 1]
            cin = cur[axis] >= v if above else cur[axis] <= v
            pin = prev[axis] >= v if above else prev[axis] <= v
            if cin != pin:
                t = (v - prev[axis]) / (cur[axis] - prev[axis])
                out.append((prev[0] + (cur[0] - prev[0]) * t, prev[1] + (cur[1] - prev[1]) * t))
            if cin:
                out.append(cur)
    return out
