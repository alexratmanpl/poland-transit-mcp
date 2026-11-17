"""Walking graph: CSR arrays loaded from SQLite blobs, searched with scipy's C Dijkstra."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from . import telemetry
from .geo import LatLon, fast_dist

WALK_SPEED_MPS = 4.8 / 3.6  # 4.8 km/h
FLAG_BRIDGE = 1
FLAG_TUNNEL = 2
FLAG_STEPS = 4
STEPS_FACTOR = 1.5


@dataclass
class GraphArrays:
    coords: np.ndarray  # int32, lat*1e6, lon*1e6 interleaved
    off: np.ndarray  # uint32, n+1
    adj: np.ndarray  # uint32, m
    length: np.ndarray  # float32, m (metres)
    flags: np.ndarray  # uint8, m

    def blobs(self) -> dict[str, bytes]:
        return {"walk_coords": self.coords.astype("<i4").tobytes(), "walk_off": self.off.astype("<u4").tobytes(),
                "walk_adj": self.adj.astype("<u4").tobytes(), "walk_len": self.length.astype("<f4").tobytes(),
                "walk_flags": self.flags.astype("u1").tobytes()}

    @staticmethod
    def from_blobs(b: dict[str, bytes]) -> "GraphArrays":
        return GraphArrays(np.frombuffer(b["walk_coords"], "<i4"), np.frombuffer(b["walk_off"], "<u4"),
                           np.frombuffer(b["walk_adj"], "<u4"), np.frombuffer(b["walk_len"], "<f4"),
                           np.frombuffer(b["walk_flags"], "u1"))


@dataclass
class Snap:
    node: int
    dist: float


class WalkGraph:
    CELL = 0.002  # grid cell height in degrees (~220 m); width is 1.6× that in longitude

    def __init__(self, a: GraphArrays):
        self.n = len(a.off) - 1
        coords = np.asarray(a.coords)
        if len(coords) != 2 * self.n:
            raise ValueError(f"walking graph is malformed ({len(coords)} coordinate values for {self.n} nodes); rebuild or re-download the city file")
        self.lat = (coords[0::2] / 1e6).astype(np.float32)  # float32: ~0.4 m precision, half the memory
        self.lon = (coords[1::2] / 1e6).astype(np.float32)
        cost = np.asarray(a.length, np.float64) * np.where(np.asarray(a.flags) & FLAG_STEPS, STEPS_FACTOR, 1.0)
        cost = np.maximum(cost, 0.01)  # explicit zero-length edges would read as "no edge"
        self.csr = csr_matrix((cost, np.asarray(a.adj, np.int32), np.asarray(a.off, np.int32)), shape=(self.n, self.n))
        try:  # scipy's constructor skips the index-range and offset-order checks; its compiled Dijkstra trusts them
            self.csr.check_format(full_check=True)
        except ValueError as e:
            raise ValueError(f"walking graph is malformed ({e}); rebuild or re-download the city file") from e
        self.flags = np.array(a.flags, np.uint8)  # own copy, so the SQLite blobs can be freed
        # grid index for snapping: nodes sorted by cell, with the start of each cell
        lat0 = float(self.lat.min()) if self.n else 0.0
        lon0 = float(self.lon.min()) if self.n else 0.0
        self.g0 = (lat0, lon0)
        self.rows = int((float(self.lat.max()) - lat0) / self.CELL) + 2 if self.n else 1
        self.cols = int((float(self.lon.max()) - lon0) / (self.CELL * 1.6)) + 2 if self.n else 1
        cell = self._cell(self.lat, self.lon)
        self.order = np.argsort(cell, kind="stable").astype(np.int32)
        self.cell_start = np.searchsorted(cell[self.order], np.arange(self.rows * self.cols + 1)).astype(np.int32)


    def nbytes(self) -> int:
        """Memory held by the graph's arrays."""
        return (self.lat.nbytes + self.lon.nbytes + self.flags.nbytes + self.order.nbytes + self.cell_start.nbytes
                + self.csr.data.nbytes + self.csr.indices.nbytes + self.csr.indptr.nbytes)
    def _cell(self, lat, lon):
        r = np.clip(((lat - self.g0[0]) / self.CELL).astype(np.int64), 0, self.rows - 1)
        c = np.clip(((lon - self.g0[1]) / (self.CELL * 1.6)).astype(np.int64), 0, self.cols - 1)
        return r * self.cols + c

    @property
    def adj(self) -> np.ndarray:
        return self.csr.indices

    @property
    def off(self) -> np.ndarray:
        return self.csr.indptr

    def snap(self, lat: float, lon: float, max_dist: float = 400) -> Snap | None:
        """Nearest graph node within max_dist metres."""
        r0 = int((lat - self.g0[0]) / self.CELL)
        c0 = int((lon - self.g0[1]) / (self.CELL * 1.6))
        rings = int(max_dist / 200) + 1  # one ring covers at least ~200 m
        kx = math.cos(math.radians(lat)) * 111320.0
        best, best_d = -1, math.inf
        for ring in range(rings + 1):
            idx = []
            for r in range(r0 - ring, r0 + ring + 1):
                if not 0 <= r < self.rows:
                    continue
                for c in range(c0 - ring, c0 + ring + 1):
                    if 0 <= c < self.cols and max(abs(r - r0), abs(c - c0)) == ring:
                        k = r * self.cols + c
                        if self.cell_start[k + 1] > self.cell_start[k]:
                            idx.append(self.order[self.cell_start[k]:self.cell_start[k + 1]])
            if idx:
                cand = np.concatenate(idx)
                d = np.hypot((self.lon[cand] - lon) * kx, (self.lat[cand] - lat) * 110574.0)
                i = int(np.argmin(d))
                if d[i] < best_d:
                    best, best_d = int(cand[i]), float(d[i])
            if best >= 0 and best_d < ring * 200:
                break
        return Snap(best, best_d) if best >= 0 and best_d <= max_dist else None

    def search(self, src: int, limit: float) -> tuple[np.ndarray, np.ndarray]:
        """Bounded single-source Dijkstra: (dist, predecessors); unreached nodes are inf / -9999."""
        with telemetry.span("walk.search", **{"poland_transit.walk.radius_m": round(float(limit))}):
            dist, pred = dijkstra(self.csr, directed=True, indices=src, limit=limit, return_predecessors=True)
        return dist, pred

    @staticmethod
    def path_from(pred: np.ndarray, target: int) -> list[int]:
        out = [target]
        v = int(pred[target])
        while v >= 0:
            out.append(v)
            v = int(pred[v])
        out.reverse()
        return out

    def route(self, a: int, b: int, max_cost: float = 15000) -> tuple[float, list[int]] | None:
        """Shortest path a → b. The search radius grows from ~2× the straight distance up to max_cost."""
        straight = fast_dist(self.lat[a], self.lon[a], self.lat[b], self.lon[b])
        limit = min(max_cost, max(800.0, 2.0 * straight + 400))
        while True:
            dist, pred = self.search(a, limit)
            if math.isfinite(dist[b]):
                return float(dist[b]), self.path_from(pred, b)
            if limit >= max_cost:
                return None
            limit = min(max_cost, limit * 2)

    def coords_of(self, path: list[int]) -> list[LatLon]:
        return [(float(self.lat[i]), float(self.lon[i])) for i in path]

    def describe(self, path: list[int]) -> tuple[float, list[int]]:
        """Real length (m) of a node path and the flags of each edge on it."""
        length, flags = 0.0, []
        off, adj = self.csr.indptr, self.csr.indices
        for u, v in zip(path, path[1:]):
            lo, hi = int(off[u]), int(off[u + 1])
            k = lo + int(np.nonzero(adj[lo:hi] == v)[0][0])
            length += fast_dist(float(self.lat[u]), float(self.lon[u]), float(self.lat[v]), float(self.lon[v]))
            flags.append(int(self.flags[k]))
        return length, flags
