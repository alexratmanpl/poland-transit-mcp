//! RAPTOR search for poland-transit-mcp, as a Python extension.
//!
//! A line-for-line port of `raptor()` in `src/poland_transit_mcp/raptor.py`: same rounds, same
//! pruning, same answers. The Python side builds the day's tables once (`Tables`, `Transfers`) and
//! calls `Tables.search` for each query. The search releases the GIL, so searches on different
//! threads run in parallel. `Tables.earliest` runs the same search with no target and returns the
//! earliest arrival at every stop (`raptor_all()` in Python), for trips that continue from many stops.
//!
//! Layout (all little-endian, passed as `bytes`):
//! - patterns: `pat_off` (i64, n+1) indexes `stops`/`pick`/`drop`; `ntrips` (i32, n);
//!   `time_off` (i64, n+1) indexes `arr`/`dep`, position-major: trip t at position i of pattern p
//!   is `arr[time_off[p] + i * ntrips[p] + t]`.
//! - stop → patterns: `sr_off` (i64, stops+1) indexes `sr_pat`/`sr_pos` (i32).
//! - foot transfers: `off` (i64, stops+1) indexes `to`/`secs` (i32).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

const INF: i64 = 1 << 40;

fn i32s(name: &str, b: &[u8]) -> PyResult<Vec<i32>> {
    if b.len() % 4 != 0 {
        return Err(PyValueError::new_err(format!("{name}: length {} is not a multiple of 4", b.len())));
    }
    Ok(b.chunks_exact(4).map(|c| i32::from_le_bytes(c.try_into().unwrap())).collect())
}

fn i64s(name: &str, b: &[u8]) -> PyResult<Vec<i64>> {
    if b.len() % 8 != 0 {
        return Err(PyValueError::new_err(format!("{name}: length {} is not a multiple of 8", b.len())));
    }
    Ok(b.chunks_exact(8).map(|c| i64::from_le_bytes(c.try_into().unwrap())).collect())
}

fn check(ok: bool, what: &str) -> PyResult<()> {
    if ok {
        Ok(())
    } else {
        Err(PyValueError::new_err(format!("inconsistent tables: {what}")))
    }
}

/// Foot transfers between stops, for one city.
#[pyclass(frozen, module = "poland_transit_engine")]
struct Transfers {
    off: Vec<i64>,
    to: Vec<i32>,
    secs: Vec<i32>,
}

#[pymethods]
impl Transfers {
    #[new]
    fn new(off: &[u8], to: &[u8], secs: &[u8]) -> PyResult<Self> {
        let t = Transfers { off: i64s("off", off)?, to: i32s("to", to)?, secs: i32s("secs", secs)? };
        check(!t.off.is_empty() && t.to.len() == t.secs.len() && *t.off.last().unwrap() as usize == t.to.len(), "transfers")?;
        let n = t.off.len() - 1;
        check(t.to.iter().all(|&s| s >= 0 && (s as usize) < n), "transfer target out of range")?;
        Ok(t)
    }

    fn nbytes(&self) -> usize {
        self.off.len() * 8 + (self.to.len() + self.secs.len()) * 4
    }
}

/// One service day's RAPTOR tables in one direction.
#[pyclass(frozen, module = "poland_transit_engine")]
struct Tables {
    n_stops: usize,
    pat_off: Vec<i64>,
    stops: Vec<i32>,
    pick: Vec<u8>,
    drop: Vec<u8>,
    ntrips: Vec<i32>,
    time_off: Vec<i64>,
    arr: Vec<i32>,
    dep: Vec<i32>,
    sr_off: Vec<i64>,
    sr_pat: Vec<i32>,
    sr_pos: Vec<i32>,
}

type Leg = (u8, i64, i64, i64, i64);
type Journey = (usize, i64, usize, Vec<Leg>);

#[pymethods]
impl Tables {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        n_stops: usize, pat_off: &[u8], stops: &[u8], pick: &[u8], drop: &[u8], ntrips: &[u8],
        time_off: &[u8], arr: &[u8], dep: &[u8], sr_off: &[u8], sr_pat: &[u8], sr_pos: &[u8],
    ) -> PyResult<Self> {
        let t = Tables {
            n_stops,
            pat_off: i64s("pat_off", pat_off)?,
            stops: i32s("stops", stops)?,
            pick: pick.to_vec(),
            drop: drop.to_vec(),
            ntrips: i32s("ntrips", ntrips)?,
            time_off: i64s("time_off", time_off)?,
            arr: i32s("arr", arr)?,
            dep: i32s("dep", dep)?,
            sr_off: i64s("sr_off", sr_off)?,
            sr_pat: i32s("sr_pat", sr_pat)?,
            sr_pos: i32s("sr_pos", sr_pos)?,
        };
        let n = t.ntrips.len();
        check(t.pat_off.len() == n + 1 && t.time_off.len() == n + 1, "pattern offsets")?;
        check(*t.pat_off.last().unwrap() as usize == t.stops.len(), "pattern stops")?;
        check(t.pick.len() == t.stops.len() && t.drop.len() == t.stops.len(), "pick/drop flags")?;
        check(t.arr.len() == t.dep.len() && *t.time_off.last().unwrap() as usize == t.arr.len(), "times")?;
        for p in 0..n {
            let len = (t.pat_off[p + 1] - t.pat_off[p]) as usize;
            check((t.time_off[p + 1] - t.time_off[p]) as usize == len * t.ntrips[p] as usize, "times per pattern")?;
        }
        check(t.stops.iter().all(|&s| s >= 0 && (s as usize) < n_stops), "stop out of range")?;
        check(t.sr_off.len() == n_stops + 1 && *t.sr_off.last().unwrap() as usize == t.sr_pat.len(), "stop routes")?;
        check(t.sr_pat.len() == t.sr_pos.len(), "stop routes")?;
        check(t.sr_pat.iter().zip(&t.sr_pos).all(|(&p, &i)| p >= 0 && (p as usize) < n && i >= 0
            && (i as i64) < t.pat_off[p as usize + 1] - t.pat_off[p as usize]), "stop route out of range")?;
        Ok(t)
    }

    fn nbytes(&self) -> usize {
        (self.pat_off.len() + self.time_off.len() + self.sr_off.len()) * 8
            + (self.stops.len() + self.ntrips.len() + self.arr.len() + self.dep.len() + self.sr_pat.len() + self.sr_pos.len()) * 4
            + self.pick.len() + self.drop.len()
    }

    /// Earliest-arrival journeys, one per improving round, as (round, arrival, egress stop, legs).
    /// A leg is (0, pattern, trip, board position, alight position) or (1, from stop, to stop, secs, 0).
    #[pyo3(signature = (transfers, access, egress, departure, max_rounds, min_change=60))]
    fn search(
        &self, py: Python<'_>, transfers: &Transfers, access: Vec<(usize, i64)>, egress: Vec<(usize, i64)>,
        departure: i64, max_rounds: usize, min_change: i64,
    ) -> PyResult<Vec<Journey>> {
        check(access.iter().chain(&egress).all(|&(s, _)| s < self.n_stops), "access/egress stop out of range")?;
        check(transfers.off.len() >= self.n_stops + 1, "transfers cover fewer stops than the tables")?;
        Ok(py.detach(|| self.raptor(transfers, &access, &egress, departure, max_rounds, min_change).0))
    }

    /// Earliest arrival at every stop, by transit or on foot, with at most max_rounds vehicles:
    /// the same search with no target, so nothing is pruned. Little-endian i64 per stop, INF (2^40)
    /// where unreached.
    #[pyo3(signature = (transfers, access, departure, max_rounds, min_change=60))]
    fn earliest<'py>(
        &self, py: Python<'py>, transfers: &Transfers, access: Vec<(usize, i64)>, departure: i64,
        max_rounds: usize, min_change: i64,
    ) -> PyResult<Bound<'py, PyBytes>> {
        check(access.iter().all(|&(s, _)| s < self.n_stops), "access stop out of range")?;
        check(transfers.off.len() >= self.n_stops + 1, "transfers cover fewer stops than the tables")?;
        let best = py.detach(|| self.raptor(transfers, &access, &[], departure, max_rounds, min_change).1);
        let raw: Vec<u8> = best.iter().flat_map(|v| v.to_le_bytes()).collect();
        Ok(PyBytes::new(py, &raw))
    }
}

impl Tables {
    /// The search: (journeys to egress, earliest arrival at every stop).
    fn raptor(&self, tr: &Transfers, access: &[(usize, i64)], egress: &[(usize, i64)], departure: i64,
              k_n: usize, min_change: i64) -> (Vec<Journey>, Vec<i64>) {
        let s_n = self.n_stops;
        // per round: arrival incl. foot transfers, arrival by transit, transit parent, walk parent
        let mut tau = vec![vec![INF; s_n]; k_n + 1];
        let mut tau_t = vec![vec![INF; s_n]; k_n + 1];
        let mut p_transit = vec![vec![(-1i32, -1i32, -1i32, -1i32); s_n]; k_n + 1];
        let mut p_walk = vec![vec![-1i32; s_n]; k_n + 1];
        let mut best = vec![INF; s_n];
        let mut best_t = vec![INF; s_n];
        let mut is_marked = vec![false; s_n];
        let mut marked: Vec<usize> = Vec::new();
        for &(s, w) in access {
            let t = departure + w;
            if t < tau[0][s] {
                tau[0][s] = t;
                best[s] = best[s].min(t);
                if !is_marked[s] {
                    is_marked[s] = true;
                    marked.push(s);
                }
            }
        }
        let mut target_best = INF;
        let mut journeys = Vec::new();
        let n_pat = self.ntrips.len();
        let mut queue_pos = vec![-1i32; n_pat];
        let mut queue: Vec<usize> = Vec::new();

        for k in 1..=k_n {
            if marked.is_empty() {
                break;
            }
            queue.clear();
            for &s in &marked {
                for j in self.sr_off[s] as usize..self.sr_off[s + 1] as usize {
                    let p = self.sr_pat[j] as usize;
                    let pos = self.sr_pos[j];
                    if queue_pos[p] < 0 {
                        queue_pos[p] = pos;
                        queue.push(p);
                    } else if pos < queue_pos[p] {
                        queue_pos[p] = pos;
                    }
                }
            }
            for &s in &marked {
                is_marked[s] = false;
            }
            marked.clear();
            let slack = if k > 1 { min_change } else { 0 };
            for &p in &queue {
                let start = queue_pos[p] as usize;
                queue_pos[p] = -1;
                let off = self.pat_off[p] as usize;
                let len = self.pat_off[p + 1] as usize - off;
                let n = self.ntrips[p] as usize;
                let toff = self.time_off[p] as usize;
                let mut trip: i64 = -1;
                let mut board: i64 = -1;
                for i in start..len {
                    let s = self.stops[off + i] as usize;
                    if trip >= 0 && self.drop[off + i] != 0 {
                        let a = self.arr[toff + i * n + trip as usize] as i64;
                        if a < best_t[s] && a < target_best {
                            tau_t[k][s] = a;
                            best_t[s] = a;
                            p_transit[k][s] = (p as i32, trip as i32, board as i32, i as i32);
                            if a < best[s] {
                                best[s] = a;
                            }
                            if a < tau[k][s] {
                                tau[k][s] = a;
                                p_walk[k][s] = -1;
                            }
                            if !is_marked[s] {
                                is_marked[s] = true;
                                marked.push(s);
                            }
                        }
                    }
                    if self.pick[off + i] != 0 {
                        let pv = tau[k - 1][s];
                        if pv < INF {
                            let need = pv + slack;
                            let col = &self.dep[toff + i * n..toff + (i + 1) * n];
                            if trip < 0 || need <= col[trip as usize] as i64 {
                                let hi = if trip < 0 { n } else { trip as usize + 1 };
                                let t2 = col[..hi].partition_point(|&x| (x as i64) < need); // bisect_left
                                if t2 < hi && (trip < 0 || (t2 as i64) < trip) {
                                    trip = t2 as i64;
                                    board = i as i64;
                                }
                            }
                        }
                    }
                }
            }
            // foot transfers from stops reached by transit in this round
            let reached = marked.clone();
            for &s in &reached {
                let a0 = tau_t[k][s];
                if a0 >= INF {
                    continue;
                }
                for j in tr.off[s] as usize..tr.off[s + 1] as usize {
                    let s2 = tr.to[j] as usize;
                    let a = a0 + tr.secs[j] as i64;
                    if a < tau[k][s2] && a < best[s2] && a < target_best {
                        tau[k][s2] = a;
                        best[s2] = a;
                        p_walk[k][s2] = s as i32;
                        if !is_marked[s2] {
                            is_marked[s2] = true;
                            marked.push(s2);
                        }
                    }
                }
            }
            let (mut best_e, mut best_arr) = (usize::MAX, INF);
            for &(s, w) in egress {
                let a = tau_t[k][s];
                if a < INF && a + w < best_arr {
                    best_arr = a + w;
                    best_e = s;
                }
            }
            if best_e != usize::MAX && best_arr < target_best {
                target_best = best_arr;
                let mut legs: Vec<Leg> = Vec::new();
                let mut s = best_e;
                let mut rnd = k;
                let mut via_transit = true; // egress always leaves from a transit arrival
                while rnd > 0 {
                    if !via_transit && p_walk[rnd][s] >= 0 {
                        let frm = p_walk[rnd][s] as usize;
                        legs.push((1, frm as i64, s as i64, tau[rnd][s] - tau_t[rnd][frm], 0));
                        s = frm;
                    }
                    let (p, trip, board, alight) = p_transit[rnd][s];
                    legs.push((0, p as i64, trip as i64, board as i64, alight as i64));
                    s = self.stops[self.pat_off[p as usize] as usize + board as usize] as usize;
                    rnd -= 1;
                    via_transit = false;
                }
                legs.reverse();
                journeys.push((k, best_arr, best_e, legs));
            }
        }
        (journeys, best)
    }
}

#[pymodule]
fn poland_transit_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Tables>()?;
    m.add_class::<Transfers>()?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
