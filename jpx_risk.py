"""Daily fear gauges for Nikkei 225 options: 30-day IV and put skew.

Each run fetches the JPX chain (or reads a saved one with --csv), measures

  * 30d IV  - constant-maturity 30-day implied vol, interpolated in total
              variance between the expiries either side of 30 days (a
              home-made analogue of the Nikkei VI), and
  * skew    - IV at 95% moneyness minus IV at 105% on the first expiry with
              at least a week to run, in vol points; rising skew means more
              demand for crash protection,

stores one row per date in a history CSV (re-running on the same date
replaces that row), prints today against the previous row, and charts the
whole history with day-over-day changes.

Data comes from the regular Nikkei 225 options pages (nkopm), not the mini
pages jpx_gex.py uses.  The mini pages list a narrower band of strikes, and
after a sharp fall the index can sit at the bottom of it, leaving no OTM put
quotes to measure skew from.  The regular options list a much wider range.
Both contracts are on the same index, so their implied vols match.

Caveats: implied vol is backed out of published deltas by jpx_gex.py, and
the smile is fitted only on OTM quotes with delta 0.02-0.30, so ATM IV is
interpolated between the wings.  Its level can differ from the official
Nikkei VI by a point or two; day-over-day changes are what to watch.  Run at
the same time each day (ideally after the close) to keep the series
comparable.
"""

import argparse
import os
import sys
from datetime import date

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from jpx_gex import (fetch_html, fit_smile, implied_vol_points, infer_spot,
                     parse_chain, prepare_chain, read_chain_csv)

# Regular Nikkei 225 options: a much wider strike range than the mini pages
# (nkopmini), so OTM puts are available for skew even after a sharp fall.
RISK_URL = "https://svc.qri.jp/jpx/english/nkopm/"
# Expiry pages to pull: a 30-day constant-maturity IV needs expiries on both
# sides of 30 days.  Pages JPX doesn't currently list are skipped.
RISK_PATHS = ["", "1", "2"]

HISTORY_COLUMNS = ["date", "spot", "iv_30d", "skew", "skew_expiry", "iv_two_sided"]

TARGET_DAYS = 30
MIN_DAYS_IV = 2      # expiries closer than this are too noisy for IV
MIN_DAYS_SKEW = 7    # wings of a near-dated expiry are unreliable
MIN_WING_POINTS = 3  # IV points needed on each side for a trustworthy smile

UP_COLOR, DOWN_COLOR = "#E05555", "#4CAF50"   # up = more fear = red


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def fetch_risk_chain(paths=RISK_PATHS):
    """Fetch each expiry page, skipping pages that fail rather than aborting."""
    frames = []
    for path in paths:
        try:
            frame = parse_chain(fetch_html(path, base_url=RISK_URL))
        except Exception as exc:  # a missing far page shouldn't kill the run
            print(f"warning: nkopm/{path or '(base)'} skipped: {exc}", file=sys.stderr)
            continue
        expiry = frame["Expiry"].iloc[0] if len(frame) else "?"
        print(f"nkopm/{path or '(base)'}: expiry {expiry}, {len(frame)} strikes")
        frames.append(frame)
    if not frames:
        raise SystemExit("No option data fetched from any expiration page.")
    return pd.concat(frames, ignore_index=True)


def expiry_vols(chain, spot):
    """ATM, 95% and 105% implied vol per expiry from the fitted smile."""
    probe = np.array([1.0, 0.95, 1.05]) * spot
    rows = []
    for expiry, group in chain.groupby("Expiry", sort=False):
        points = implied_vol_points(group, spot)
        fitted = fit_smile(points, spot, probe)
        if fitted is None:
            # Weekly pages often list only a handful of strikes.
            continue
        T = float(group["T"].iloc[0])
        rows.append({"Expiry": expiry, "T": T, "days": T * 365.0,
                     "iv_atm": fitted[0], "iv_95": fitted[1], "iv_105": fitted[2],
                     "n_put": int((points["Strike"] < spot).sum()),
                     "n_call": int((points["Strike"] > spot).sum())})
    vols = pd.DataFrame(rows, columns=["Expiry", "T", "days", "iv_atm", "iv_95", "iv_105",
                                       "n_put", "n_call"])
    vols["two_sided"] = ((vols["n_put"] >= MIN_WING_POINTS)
                         & (vols["n_call"] >= MIN_WING_POINTS))
    return vols.sort_values("T").reset_index(drop=True)


def iv_30d(vols):
    """30-day IV, interpolating total variance sigma^2*T linearly in T.

    Returns (iv, two_sided).  two_sided is False when an expiry it relied on
    had OTM quotes on one wing only, so its ATM vol is extrapolated.
    """
    usable = vols[vols["days"] >= MIN_DAYS_IV]
    if usable.empty:
        return None, False
    T = usable["T"].to_numpy(float)
    var = usable["iv_atm"].to_numpy(float) ** 2 * T
    sided = usable["two_sided"].to_numpy(bool)
    target = TARGET_DAYS / 365.0
    if target <= T[0]:
        return float(usable["iv_atm"].iloc[0]), bool(sided[0])
    if target >= T[-1]:
        return float(usable["iv_atm"].iloc[-1]), bool(sided[-1])
    i = int(np.searchsorted(T, target))
    iv = float(np.sqrt(np.interp(target, T, var) / target))
    return iv, bool(sided[i - 1] and sided[i])


def skew_expiry(vols):
    """Expiry row for skew: the first with a week to run and quotes on both wings.

    Put skew needs real OTM put quotes.  Extrapolating the put wing from calls
    alone would be meaningless, so this returns None when no expiry qualifies.
    """
    ok = vols[(vols["days"] >= MIN_DAYS_SKEW) & vols["two_sided"]]
    return ok.iloc[0] if not ok.empty else None


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------

def load_history(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    hist = pd.read_csv(path)
    hist["date"] = hist["date"].astype(str)
    return hist.sort_values("date").reset_index(drop=True)


def upsert_history(hist, row):
    hist = hist[hist["date"] != row["date"]]
    new = pd.DataFrame([row], columns=HISTORY_COLUMNS)
    hist = new if hist.empty else pd.concat([hist, new], ignore_index=True)
    return hist.sort_values("date").reset_index(drop=True)


def _change_line(name, value, prev, unit, scale=1.0):
    text = f"{name:<8}{value * scale:>7.2f}{unit}"
    if prev is None or pd.isna(prev):
        return text + "   (no previous row)"
    delta = (value - prev) * scale
    arrow = "▲" if delta > 0 else "▼" if delta < 0 else "="
    return text + f"   prev {prev * scale:>6.2f}{unit}   {delta:+6.2f} {arrow}"


# --------------------------------------------------------------------------
# chart
# --------------------------------------------------------------------------

def plot_history(hist):
    if hist.empty:
        print("History is empty; nothing to chart.")
        return
    plt.style.use("dark_background")
    plt.rcParams["font.family"] = "monospace"
    plt.rcParams["font.size"] = 12
    plt.rcParams["xtick.labelsize"] = 11
    plt.rcParams["ytick.labelsize"] = 11

    x = np.arange(len(hist))
    iv = hist["iv_30d"].to_numpy(float) * 100
    skew = hist["skew"].to_numpy(float) * 100
    d_iv = np.diff(iv, prepend=np.nan)
    d_skew = np.diff(skew, prepend=np.nan)

    fig, axes = plt.subplots(4, 1, sharex=True, figsize=(14, 11),
                             gridspec_kw={"height_ratios": [2, 1, 2, 1]})
    fig.patch.set_facecolor("#1A1A1A")
    panels = [
        (iv, "30d IV (%)", "line", "#5DADE2"),
        (d_iv, "Δ 30d IV (vp)", "bar", None),
        (skew, "Put skew 95-105 (vp)", "line", "#F5B041"),
        (d_skew, "Δ skew (vp)", "bar", None),
    ]
    for ax, (values, title, kind, color) in zip(axes, panels):
        ax.set_facecolor("#1A1A1A")
        ax.grid(True, color="#333333", linewidth=0.6)
        ax.set_ylabel(title)
        if kind == "line":
            if np.isnan(values).all():
                msg = ("no put skew yet (JPX listed no OTM puts)" if values is skew
                       else "no data yet")
                ax.text(0.5, 0.5, msg, transform=ax.transAxes, ha="center",
                        va="center", color="#AAAAAA")
                ax.set_yticks([])
                continue
            size = 30 if len(hist) > 1 else 120  # make a lone first point visible
            ax.plot(x, values, color=color, linewidth=1.8)
            ax.scatter(x, values, color=color, s=size, zorder=3)
            if values is iv:  # hollow marker = ATM extrapolated from one wing
                one = ~hist["iv_two_sided"].astype(str).eq("True").to_numpy()
                ax.scatter(x[one], values[one], s=size, zorder=4, linewidths=1.8,
                           facecolor="#1A1A1A", edgecolor=color)
                if one.any():
                    ax.text(0.01, 0.95, "○ = one-sided fit (call wing only)",
                            transform=ax.transAxes, va="top", fontsize=10,
                            color="#AAAAAA")
            for xi, v in zip(x, values):
                if not np.isnan(v):
                    ax.annotate(f"{v:.1f}", (xi, v), textcoords="offset points",
                                xytext=(0, 7), ha="center", fontsize=10, color="#CCCCCC")
        else:
            colors = [UP_COLOR if v > 0 else DOWN_COLOR for v in np.nan_to_num(values)]
            ax.bar(x, np.nan_to_num(values), color=colors, width=0.6)
            ax.axhline(0, color="#888888", linewidth=0.8)
            if np.isnan(values).all():
                ax.set_yticks([])
                ax.text(0.5, 0.5, "changes need at least 2 days of data",
                        transform=ax.transAxes, ha="center", va="center", color="#AAAAAA")

    step = max(1, len(hist) // 20)
    axes[-1].set_xticks(x[::step])
    axes[-1].set_xticklabels(hist["date"].iloc[::step], rotation=45, ha="right")
    fig.suptitle(f"Nikkei 225 options - fear gauges  ({hist['date'].iloc[0]} → "
                 f"{hist['date'].iloc[-1]}, {len(hist)} days)", fontsize=16)
    fig.tight_layout()
    plt.show()


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", help="read a saved chain CSV instead of fetching from JPX")
    parser.add_argument("--asof", type=date.fromisoformat, default=date.today(),
                        help="valuation date and history key, YYYY-MM-DD (default: today)")
    parser.add_argument("--spot", type=float, help="underlying price (default: inferred from deltas)")
    parser.add_argument("--history", default="jpx_risk_history.csv",
                        help="history CSV (default: jpx_risk_history.csv)")
    parser.add_argument("--no-history", action="store_true", help="don't read or write history")
    parser.add_argument("--no-plot", action="store_true", help="skip the history chart")
    parser.add_argument("--chart-only", action="store_true",
                        help="chart the history CSV without fetching anything")
    args = parser.parse_args(argv)

    if args.chart_only:
        plot_history(load_history(args.history))
        return 0

    if args.csv:
        raw = read_chain_csv(args.csv)
    else:
        raw = fetch_risk_chain()
        os.makedirs("history", exist_ok=True)
        archive = os.path.join("history", f"chain_{args.asof.isoformat()}.csv")
        raw.to_csv(archive, index=False)
        print(f"Archived raw chain to {archive}")

    chain = prepare_chain(raw, asof=args.asof)
    spot = args.spot if args.spot is not None else infer_spot(chain)

    vols = expiry_vols(chain, spot)
    if vols.empty:
        raise SystemExit("Could not fit a smile on any expiry.")
    iv, two_sided = iv_30d(vols)
    front = skew_expiry(vols)
    skew = float(front["iv_95"] - front["iv_105"]) if front is not None else None

    print(f"\nAs of {args.asof}   spot {spot:,.0f}")
    print(f"{'Expiry':<12}{'days':>6}{'ATM':>8}{'95%':>8}{'105%':>8}  OTM points put/call")
    for _, r in vols.iterrows():
        put95 = f"{r['iv_95']:>8.1%}" if r["n_put"] >= MIN_WING_POINTS else f"{'-':>8}"
        print(f"{r['Expiry']:<12}{r['days']:>6.0f}{r['iv_atm']:>8.1%}{put95}"
              f"{r['iv_105']:>8.1%}  {r['n_put']:>3} / {r['n_call']}")
    if not vols["two_sided"].any():
        print("note: JPX lists no OTM put strikes today, so put skew can't be measured\n"
              "      and ATM IV is extrapolated from the call wing only (less reliable).")
    print()

    row = {"date": args.asof.isoformat(), "spot": round(spot, 1),
           "iv_30d": round(iv, 5) if iv is not None else np.nan,
           "skew": round(skew, 5) if skew is not None else np.nan,
           "skew_expiry": front["Expiry"] if front is not None else "",
           "iv_two_sided": two_sided}

    hist = None
    prev = {}
    if not args.no_history:
        hist = load_history(args.history)
        earlier = hist[hist["date"] < row["date"]]
        if not earlier.empty:
            prev = earlier.iloc[-1].to_dict()
            print(f"Previous row: {prev['date']}")

    if iv is not None:
        flag = "" if two_sided else "   [one-sided fit]"
        print(_change_line("30d IV", iv, prev.get("iv_30d"), "%", 100) + flag)
    else:
        print("30d IV  n/a (no usable expiry)")
    if skew is not None:
        print(_change_line("Skew", skew, prev.get("skew"), " vp", 100)
              + f"   [{front['Expiry']}]")
    else:
        print("Skew    n/a (no expiry with OTM put quotes)")

    if hist is not None:
        hist = upsert_history(hist, row)
        hist.to_csv(args.history, index=False)
        print(f"\nHistory: {len(hist)} rows in {args.history}")
        if not args.no_plot:
            plot_history(hist)
    return 0


if __name__ == "__main__":
    sys.exit(main())
