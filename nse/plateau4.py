"""
Protocol-v4.2 plateau gate, mechanically: every numeric PARAMS entry of a candidate is moved to its neighbours and
re-scored on the 2016-2026 data (no lookahead check; v4lab).

Neighbours: about -20 % and +20 % (integers rounded, at least one step away; a value between 0.9 and 1, e.g. a
0.985 stop, moves through its distance from 1).
  * new or moved parameters (absolute rule): each neighbour must beat the incumbent's objective and keep at least
    60 % of the candidate's margin over it;
  * parameters inherited unchanged from the incumbent (same name, same value - e.g. the EMA timer's fast/slow):
    each neighbour must beat the INCUMBENT AT THE SAME NEIGHBOUR VALUE (the change must help wherever the inherited
    core sits; the core's own fragility is the incumbent's, not the change's).
The use_rsi / use_ema / use_vwap switches are not parameters.

Usage: python plateau4.py CANDIDATE [--incumbent v4/r0_ema_timer.py]
"""
import argparse, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v4lab

KEEP = 0.60


def neighbours(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return []
    if isinstance(v, float) and 0.9 < v < 1.0:
        return [round(1.0 - (1.0 - v) * f, 6) for f in (0.8, 1.2)]
    out = []
    for f in (0.8, 1.2):
        nv = round(v * f, 6)
        if isinstance(v, int):
            nv = int(round(nv))
            if nv == v:
                nv = v - 1 if f < 1 else v + 1
            nv = max(1, nv)
        out.append(nv)
    return out


def plateau(cand_path, inc_path, md=None):
    md = md if md is not None else v4lab.data()
    cand, inc = v4lab.load(cand_path), v4lab.load(inc_path)
    base = v4lab.score(cand, md)["objective"]
    inc_base = v4lab.score(inc, md)["objective"]
    margin = base - inc_base
    rows = []
    for k, v in cand.PARAMS.items():
        if k.startswith("use_"):
            continue
        inherited = k in getattr(inc, "PARAMS", {}) and inc.PARAMS[k] == v
        for nv in neighbours(v):
            obj = v4lab.score(cand, md, **{k: nv})["objective"]
            if inherited:
                ref = v4lab.score(inc, md, **{k: nv})["objective"]
                ok = obj > ref
            else:
                ref = inc_base
                ok = obj > inc_base and obj >= inc_base + KEEP * margin
            rows.append(dict(param=k, value=nv, objective=obj, rule="relative" if inherited else "absolute",
                             reference=round(ref, 4), ok=bool(ok)))
    return base, inc_base, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate")
    ap.add_argument("--incumbent", default=os.path.join(HERE, "v4", "r0_ema_timer.py"))
    args = ap.parse_args()
    base, inc_base, rows = plateau(args.candidate, args.incumbent)
    print(f"candidate objective {base}, incumbent {inc_base}, margin {base - inc_base:.4f}; absolute floor "
          f"{inc_base + KEEP * (base - inc_base):.4f}")
    print(f"{'param':<16}{'value':>10}{'objective':>11}{'rule':>10}{'reference':>11}  ok")
    for r in rows:
        print(f"{r['param']:<16}{r['value']!s:>10}{r['objective']:>11}{r['rule']:>10}{r['reference']:>11}  {r['ok']}")
    print(f"plateau_ok {all(r['ok'] for r in rows)}; min neighbour objective {min((r['objective'] for r in rows), default=None)}")


if __name__ == "__main__":
    main()
