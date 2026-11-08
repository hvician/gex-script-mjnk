"""Gamma exposure profile for JPX (Nikkei 225) option chains.

Fetches the live Nikkei 225 mini option chain from JPX straight into a
DataFrame and plots dealer gamma exposure, the gamma flip level and max pain.
Pass --csv to work from a previously saved chain instead of fetching.

The exchange publishes gamma rounded to 4 decimals.  For an underlying near
65,000 the true gamma is O(1e-4), so that rounding collapses the whole chain
onto {0.0, 0.0001, 0.0002} and destroys the profile.  By default this script
therefore ignores the published gamma and rebuilds it from Black-Scholes:
implied vol is backed out of the published delta (4 decimals is plenty of
resolution there), smoothed into a smile, and gamma evaluated off that.
Pass --gamma file to use the published values instead.
"""

import argparse
import sys
from datetime import date, datetime

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from matplotlib.widgets import RadioButtons
from scipy.stats import norm

# The fetched chain is Nikkei 225 mini options, quoted per index point with a
# 100 yen multiplier (standard Nikkei 225 options use 1,000).
DEFAULT_MULTIPLIER = 100

BASE_URL = "https://svc.qri.jp/jpx/english/nkopmini/"
# Expiration pages at BASE_URL + path aren't sequential months of one series:
# "" and "1" are JPX's two near-dated expirations (a weekly and the standard
# one), "2" is the next weekly, etc.  "2"-"5" exist - add paths to pull more.
EXPIRY_PATHS = ["", "1"]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    # JPX's edge server 400s any request with no Referer (or one outside its
    # own domain), with an error page telling you to access via the Japan
    # Exchange Group website - this is exactly that entry point.
    "Referer": "https://www.jpx.co.jp/english/",
}

CHAIN_COLUMNS = ["Expiry", "Strike",
                 "Call_Volume", "Call_Delta", "Call_Gamma", "Call_OI",
                 "Put_Volume", "Put_Delta", "Put_Gamma", "Put_OI"]

DATE_FORMATS = ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%a %b %d %Y", "%d-%b-%Y")

# Delta band used to back out implied vol.  Near the money delta is almost flat
# in vol, so a 4-decimal delta pins it down very poorly; below ~0.02 the quotes
# sit on the exchange's own rounding floor.  Only the band between is usable.
FIT_DELTA_LO, FIT_DELTA_HI = 0.02, 0.30

SIGMA_MIN, SIGMA_MAX = 0.05, 2.0


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def num(text):
    """Parse a JPX numeric cell, returning NaN for an empty/'-' cell.

    A naive text.replace('-', '0') would also mangle negative values
    ("-0.4567" becoming "00.4567", i.e. a sign flip) and turn missing quotes
    into real zeros, so this checks for the dash explicitly instead.
    """
    text = text.strip()
    if text in ("", "-", "−", "—"):
        return np.nan
    return float(text.replace(",", ""))


def fetch_html(path, timeout=15, base_url=BASE_URL):
    """Fetch one expiration page's HTML."""
    response = requests.get(base_url + path, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text


def parse_chain(html):
    """Parse one expiration page into a DataFrame, one row per strike."""
    soup = BeautifulSoup(html, "lxml")
    # Scope to the price table itself: the live page is the full JPX page
    # (nav, footer, etc.), so stray same-named classes elsewhere on the page
    # need guarding against.
    table = soup.find(id="priceTable")
    if table is None:
        raise ValueError("couldn't find #priceTable in the fetched page")

    for btn in table.find_all("button"):
        btn.decompose()
    for span in table.find_all("span", class_="atm-mark"):
        span.decompose()

    row_num = table.find_all(class_="row-num")
    greek_table_wrapper = table.find_all(class_=["greek", "show"])
    date_table = table.find(class_="last-tradingday")
    expiration_date = date_table.find("dl").find("dd").text  # YYYY/MM/DD

    records = []
    for row, greek_table in zip(row_num, greek_table_wrapper):
        tds = row.find_all("td")
        # Two <td> elements inside the greek wrapper: call and put tables.
        tb = greek_table.find_all("td", colspan="8")
        call_greeks = tb[0].find_all("td", class_="a-right")
        put_greeks = tb[1].find_all("td", class_="a-right")

        records.append({
            "Expiry": expiration_date,
            "Strike": num(tds[8].text),
            "Call_Volume": num(tds[2].text),
            "Call_Delta": num(call_greeks[0].text),
            "Call_Gamma": num(call_greeks[1].text),
            "Call_OI": num(tds[1].text),
            "Put_Volume": num(tds[14].text),
            "Put_Delta": num(put_greeks[0].text),
            "Put_Gamma": num(put_greeks[1].text),
            "Put_OI": num(tds[15].text),
        })

    return pd.DataFrame(records, columns=CHAIN_COLUMNS)


def fetch_chain(paths=EXPIRY_PATHS, save_html=False):
    """Fetch and parse every expiration page into one raw chain DataFrame."""
    frames = []
    for path in paths:
        html = fetch_html(path)
        if save_html:
            with open(f"jpx_options_chain_{path or 'base'}.html", "w", encoding="utf-8") as f:
                f.write(html)
        frame = parse_chain(html)
        expiry = frame["Expiry"].iloc[0] if len(frame) else "?"
        print(f"nkopmini/{path or '(base)'}: expiry {expiry}, {len(frame)} strikes")
        frames.append(frame)

    chain = pd.concat(frames, ignore_index=True)
    if chain.empty:
        raise SystemExit("No option data fetched from any expiration page.")
    return chain


# Column names in the old jpx_scraper.py CSV layout (pandas suffixes the
# repeated put-side headers with ".1").
LEGACY_COLUMNS = {
    "Expiration Date": "Expiry",
    "Strike": "Strike",
    "Volume": "Call_Volume",
    "Delta": "Call_Delta",
    "Gamma": "Call_Gamma",
    "Open Interest": "Call_OI",
    "Volume.1": "Put_Volume",
    "Delta.1": "Put_Delta",
    "Gamma.1": "Put_Gamma",
    "Open Interest.1": "Put_OI",
}


def read_chain_csv(path):
    """Read a saved chain: either --save-csv output or the old scraper CSV."""
    raw = pd.read_csv(path)
    if "Expiration Date" in raw.columns:
        raw = raw.rename(columns=LEGACY_COLUMNS)
    missing = [c for c in CHAIN_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"{path}: missing expected columns {missing}")
    return raw[CHAIN_COLUMNS]


# --------------------------------------------------------------------------
# cleaning
# --------------------------------------------------------------------------

def parse_expiry(value):
    """Parse one expiry cell, returning a date or None."""
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def prepare_chain(raw, asof=None):
    """Clean a raw chain into one tidy row per (expiry, strike).

    A published greek of exactly 0 is the exchange's rounding floor (or, in
    old scraper CSVs, a "-" written as 0), so zero deltas and gammas are
    treated as missing data rather than real zeros and become NaN here.
    """
    df = raw[CHAIN_COLUMNS].copy()
    df["Expiry"] = df["Expiry"].astype(str)

    for col in CHAIN_COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Missing ("-") cells arrive as NaN; zero greeks are unknown too, not zero.
    for col in ("Call_Delta", "Call_Gamma", "Put_Delta", "Put_Gamma"):
        df[col] = df[col].replace(0.0, np.nan)

    # Put delta is published as a positive magnitude, but a scraper that keeps
    # the sign would hand us negatives; work with magnitudes either way.
    df["Put_Delta"] = df["Put_Delta"].abs()

    for col in ("Call_OI", "Put_OI", "Call_Volume", "Put_Volume"):
        df[col] = df[col].fillna(0.0)

    df = df.dropna(subset=["Strike"])
    df["ExpiryDate"] = df["Expiry"].map(parse_expiry)
    if df["ExpiryDate"].isna().any():
        bad = df.loc[df["ExpiryDate"].isna(), "Expiry"].unique()[:3]
        raise ValueError(f"unrecognised expiry format(s) {list(bad)}")

    asof = asof or date.today()
    days = df["ExpiryDate"].map(lambda d: (d - asof).days)
    # Floor at a few hours so a same-day expiry does not blow up sigma*sqrt(T).
    df["T"] = np.maximum(days / 365.0, 0.5 / 365.0)

    df = df.groupby(["Expiry", "Strike"], as_index=False).agg("first")
    return df.sort_values(["Expiry", "Strike"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# spot
# --------------------------------------------------------------------------

def infer_spot(df):
    """Estimate spot as the strike where call delta and |put delta| cross 0.5.

    Put deltas are published as positive magnitudes, so near the forward
    call_delta + |put_delta| ~ 1 and both sides cross 0.5 at the same strike.
    """
    estimates = []
    for col, ascending in (("Call_Delta", False), ("Put_Delta", True)):
        side = df[["Strike", col]].dropna().sort_values("Strike")
        if len(side) < 2:
            continue
        strikes = side["Strike"].to_numpy(float)
        deltas = side[col].to_numpy(float)
        if not ascending:  # call delta falls with strike; np.interp needs ascending x
            strikes, deltas = strikes[::-1], deltas[::-1]
        if deltas.min() <= 0.5 <= deltas.max():
            estimates.append(float(np.interp(0.5, deltas, strikes)))

    if not estimates:
        raise ValueError("cannot infer spot from the chain; pass --spot")
    return float(np.mean(estimates))


# --------------------------------------------------------------------------
# implied vol and gamma
# --------------------------------------------------------------------------

def total_vol_from_delta(delta, x, is_call):
    """Solve sigma*sqrt(T) from a published delta.

    With r = q = 0, d1 = x/v + v/2 where x = ln(S/K) and v = sigma*sqrt(T),
    so v solves v^2 - 2*d1*v + 2x = 0.  Delta is not monotone in vol away from
    the money, so there can be two positive roots; the smaller is the realistic
    one (the larger implies several hundred percent vol).  Returns NaN when no
    root exists.
    """
    delta = np.asarray(delta, dtype=float)
    x = np.asarray(x, dtype=float)

    d1 = np.where(is_call, norm.ppf(delta), -norm.ppf(delta))
    disc = d1 * d1 - 2.0 * x
    with np.errstate(invalid="ignore"):
        root = np.sqrt(np.where(disc >= 0, disc, np.nan))

    lo, hi = d1 - root, d1 + root
    v = np.where(lo > 0, lo, np.where(hi > 0, hi, np.nan))
    return v


def implied_vol_points(group, spot):
    """Per-strike implied vols backed out of the usable published deltas."""
    T = float(group["T"].iloc[0])
    sqrt_T = np.sqrt(T)
    strikes = group["Strike"].to_numpy(float)
    x = np.log(spot / strikes)

    frames = []
    for delta_col, is_call in (("Call_Delta", True), ("Put_Delta", False)):
        delta = group[delta_col].to_numpy(float)
        # Only out-of-the-money quotes: in the money, delta carries almost no
        # vol information and the quotes are stale.
        otm = strikes > spot if is_call else strikes < spot
        usable = otm & (delta >= FIT_DELTA_LO) & (delta <= FIT_DELTA_HI)
        if not usable.any():
            continue
        v = total_vol_from_delta(delta[usable], x[usable], is_call)
        frames.append(pd.DataFrame({"Strike": strikes[usable], "Sigma": v / sqrt_T}))

    if not frames:
        return pd.DataFrame(columns=["Strike", "Sigma"])

    points = pd.concat(frames, ignore_index=True).dropna()
    return points[(points["Sigma"] > SIGMA_MIN) & (points["Sigma"] < SIGMA_MAX)]


def fit_smile(points, spot, strikes):
    """Fit sigma as a quadratic in log-moneyness, trimming outliers."""
    if len(points) < 3:
        return None

    k = np.log(points["Strike"].to_numpy(float) / spot)
    sigma = points["Sigma"].to_numpy(float)

    coef = None
    for _ in range(3):
        if len(k) < 3:
            break
        coef = np.polyfit(k, sigma, 2)
        resid = sigma - np.polyval(coef, k)
        scale = 1.4826 * np.median(np.abs(resid - np.median(resid)))
        keep = np.abs(resid) <= 3.0 * max(scale, 1e-6)
        if keep.all():
            break
        k, sigma = k[keep], sigma[keep]

    if coef is None:
        return None
    fitted = np.polyval(coef, np.log(np.asarray(strikes, float) / spot))
    return np.clip(fitted, SIGMA_MIN, SIGMA_MAX)


def bs_gamma(spot, strikes, sigma, T):
    """Black-Scholes gamma (identical for calls and puts), r = q = 0."""
    spot = np.asarray(spot, dtype=float)
    strikes = np.asarray(strikes, dtype=float)
    v = np.asarray(sigma, dtype=float) * np.sqrt(T)
    d1 = (np.log(spot / strikes) + 0.5 * v * v) / v
    return norm.pdf(d1) / (spot * v)


def attach_gamma(df, spot, source="bs"):
    """Add a Gamma column (shared by calls and puts) and the vol used for it."""
    out = df.copy()
    out["Sigma"] = np.nan
    out["Gamma"] = np.nan

    for expiry, group in out.groupby("Expiry", sort=False):
        idx = group.index
        if source == "file":
            # Published gamma is one number per side; average the two where both
            # exist, since BS gamma is the same for a call and a put.
            published = group[["Call_Gamma", "Put_Gamma"]].mean(axis=1)
            out.loc[idx, "Gamma"] = published.fillna(0.0)
            continue

        points = implied_vol_points(group, spot)
        sigma = fit_smile(points, spot, group["Strike"].to_numpy(float))
        if sigma is None:
            print(f"warning: too few usable deltas for {expiry}; "
                  f"falling back to published gamma", file=sys.stderr)
            out.loc[idx, "Gamma"] = group[["Call_Gamma", "Put_Gamma"]].mean(axis=1).fillna(0.0)
            continue

        out.loc[idx, "Sigma"] = sigma
        out.loc[idx, "Gamma"] = bs_gamma(spot, group["Strike"].to_numpy(float),
                                         sigma, float(group["T"].iloc[0]))
    return out


# --------------------------------------------------------------------------
# exposure
# --------------------------------------------------------------------------

def compute_gex(df, spot, multiplier=DEFAULT_MULTIPLIER):
    """Dealer gamma exposure in currency per 1% move in the underlying.

    Convention: dealers are long calls and short puts against customer flow,
    so call gamma adds and put gamma subtracts.
    """
    out = df.copy()
    scale = multiplier * spot * spot * 0.01

    out["Call_GEX"] = out["Gamma"] * out["Call_OI"] * scale
    out["Put_GEX"] = -out["Gamma"] * out["Put_OI"] * scale
    out["Net_GEX"] = out["Call_GEX"] + out["Put_GEX"]

    out["Total_Volume"] = out["Call_Volume"] + out["Put_Volume"]
    out["Total_OI"] = out["Call_OI"] + out["Put_OI"]
    return out


def gamma_flip(df, spot, multiplier=DEFAULT_MULTIPLIER, span=0.15, steps=301):
    """Spot level at which total dealer gamma exposure crosses zero.

    Gamma is re-evaluated at each candidate spot holding the smile fixed by
    strike, which is what "gamma flip" actually means.  Summing gamma across
    strikes at today's spot, as a cumulative total, answers a different and
    largely meaningless question.
    """
    usable = df.dropna(subset=["Sigma"])
    if usable.empty:
        return None

    strikes = usable["Strike"].to_numpy(float)
    sigma = usable["Sigma"].to_numpy(float)
    T = usable["T"].to_numpy(float)
    net_oi = usable["Call_OI"].to_numpy(float) - usable["Put_OI"].to_numpy(float)

    grid = np.linspace(spot * (1 - span), spot * (1 + span), steps)
    gamma = bs_gamma(grid[:, None], strikes[None, :], sigma[None, :], T[None, :])
    totals = (gamma * net_oi).sum(axis=1) * multiplier * grid * grid * 0.01

    sign_change = np.nonzero(np.diff(np.sign(totals)))[0]
    if sign_change.size == 0:
        return None

    # Several crossings are possible; the one that matters is nearest spot.
    i = sign_change[np.argmin(np.abs(grid[sign_change] - spot))]
    lo, hi = totals[i], totals[i + 1]
    if hi == lo:
        return float(grid[i])
    return float(grid[i] + (grid[i + 1] - grid[i]) * (-lo) / (hi - lo))


def max_pain(df):
    """Strike minimising total in-the-money value owed to option holders.

    Vectorised as a single strikes x strikes matrix instead of the original
    nested row loop.
    """
    by_strike = df.groupby("Strike", as_index=True)[["Call_OI", "Put_OI"]].sum()
    if by_strike.empty:
        return None

    strikes = by_strike.index.to_numpy(float)
    call_oi = by_strike["Call_OI"].to_numpy(float)
    put_oi = by_strike["Put_OI"].to_numpy(float)
    if call_oi.sum() + put_oi.sum() == 0:
        return None

    settle = strikes[:, None]  # candidate settlement prices
    pain = (np.maximum(settle - strikes[None, :], 0.0) * call_oi
            + np.maximum(strikes[None, :] - settle, 0.0) * put_oi).sum(axis=1)
    return float(strikes[int(np.argmin(pain))])


# --------------------------------------------------------------------------
# plotting
# --------------------------------------------------------------------------

# How many of the largest |net GEX| strikes get a labelled tick on the
# strike axis and an entry in the footer.
MAJOR_LEVELS = 12


def major_levels(by_strike, count=MAJOR_LEVELS):
    """The ``count`` strikes with the largest absolute net GEX, largest first."""
    order = by_strike["Net_GEX"].abs().sort_values(ascending=False).index
    return by_strike.loc[order].head(count)


def _strike_spacing(strikes):
    if len(strikes) < 2:
        return 1.0
    spacing = np.median(np.diff(np.sort(np.unique(strikes))))
    return float(spacing) if spacing > 0 else 1.0


def _bar_height(strikes):
    return _strike_spacing(strikes) * 0.8


def _symmetric_limit(values):
    limit = float(np.max(np.abs(values))) if len(values) else 0.0
    return limit * 1.1 if limit > 0 else 1.0


# Shared by the axis tick formatters below and the crosshair's live value
# labels, so the hover readout always matches what the axis itself shows.
def _fmt_number(value):
    return f"{value:,.0f}"


def _fmt_gex(value):
    return f"{value / 1e9:+.1f}B"


def _draw_colored_footer(fig, rows):
    """Draw the footer box with some runs of text in colour.

    matplotlib can't colour part of one Text, so the white base text has the
    coloured runs blanked out and each colour gets its own overlay with
    everything else blanked. The footer is monospace, so the overlays land
    exactly in the gaps.
    """
    def render(keep):
        return "\n".join("".join(text if keep(color) else " " * len(text)
                                 for text, color in row) for row in rows)

    style = dict(fontsize=15, family="monospace", va="bottom", ha="left")
    fig.text(0.01, 0.01, render(lambda c: c is None), color="white",
             bbox=dict(facecolor="black", alpha=0.8, edgecolor="white"), **style)
    for color in {c for row in rows for _, c in row if c is not None}:
        fig.text(0.01, 0.01, render(lambda c, color=color: c == color),
                 color=color, fontweight="bold", **style)


def draw(fig, ax_gex, ax_vol, df, spot, label, multiplier):
    ax_gex.clear()
    ax_vol.clear()
    for text in list(fig.texts):
        text.remove()  # the original kept stacking a new footer on every redraw

    if df.empty:
        ax_gex.set_title(f"No data for {label}", color="white", fontsize=22)
        fig.canvas.draw_idle()
        return

    by_strike = df.groupby("Strike", as_index=False).agg(
        Net_GEX=("Net_GEX", "sum"),
        Total_Volume=("Total_Volume", "sum"),
        Total_OI=("Total_OI", "sum"),
    )
    strikes = by_strike["Strike"].to_numpy(float)
    net = by_strike["Net_GEX"].to_numpy(float)
    height = _bar_height(strikes)

    for ax in (ax_gex, ax_vol):
        ax.set_facecolor("#1A1A1A")

    positive = net >= 0
    ax_gex.barh(strikes[positive], net[positive], color="#4ade80", alpha=0.75,
                height=height, label="Positive gamma")
    ax_gex.barh(strikes[~positive], net[~positive], color="#f87171", alpha=0.75,
                height=height, label="Negative gamma")
    ax_gex.axvline(0, color="white", alpha=0.3)

    x_limit = _symmetric_limit(net)
    levels = [("Spot", spot, "#60a5fa"),
              ("Gamma flip", gamma_flip(df, spot, multiplier), "#fbbf24"),
              ("Max pain", max_pain(df), "#ec4899")]

    for name, level, color in levels:
        if level is None:
            continue
        ax_gex.axhline(level, color=color, linestyle="--", linewidth=2,
                       label=f"{name}: {level:,.0f}")
        ax_vol.axhline(level, color=color, linestyle="--", linewidth=2, alpha=0.5)

    ax_gex.set_xlim(-x_limit, x_limit)
    ax_gex.set_xlabel("Net gamma exposure (¥ per 1% move)", fontsize=18, color="white", labelpad=12)
    ax_gex.set_ylabel("Strike", fontsize=18, color="white", labelpad=12)
    ax_gex.set_title(f"Nikkei 225 gamma exposure — {label}", fontsize=22,
                     color="white", pad=18)
    # Only label the major GEX strikes; the crosshair still reads out any strike.
    major = major_levels(by_strike)
    major_ticks = np.sort(major["Strike"].to_numpy(float))
    ax_gex.yaxis.set_major_locator(ticker.FixedLocator(major_ticks))
    ax_vol.yaxis.set_major_locator(ticker.FixedLocator(major_ticks))
    strike_formatter = ticker.FuncFormatter(lambda v, _: _fmt_number(v))
    ax_gex.yaxis.set_major_formatter(strike_formatter)
    ax_vol.yaxis.set_major_formatter(strike_formatter)
    ax_gex.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: _fmt_gex(v)))
    ax_vol.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: _fmt_number(v)))

    use_volume = by_strike["Total_Volume"].sum() > 0
    bars = by_strike["Total_Volume"] if use_volume else by_strike["Total_OI"]
    bar_label = "Total volume" if use_volume else "Total open interest"
    ax_vol.barh(strikes, bars.to_numpy(float), color="#60d5fa", alpha=0.75, height=height)
    ax_vol.set_xlabel(bar_label, fontsize=18, color="white", labelpad=12)
    # The expiry is already in the GEX title; repeating it here overflows
    # the narrow right panel past the figure edge.
    ax_vol.set_title(bar_label, fontsize=22, color="white", pad=18)
    ax_vol.set_ylim(ax_gex.get_ylim())

    for ax in (ax_gex, ax_vol):
        ax.grid(True, alpha=0.2, color="white")
        ax.tick_params(colors="white", labelsize=15, pad=8)
        for tick_label in ax.get_xticklabels() + ax.get_yticklabels():
            tick_label.set_family("monospace")
        for spine in ax.spines.values():
            spine.set_color("white")
            spine.set_alpha(0.5)

    legend = ax_gex.legend(loc="upper right", framealpha=0.85,
                           facecolor="black", edgecolor="white", fontsize=15)
    for text in legend.get_texts():
        text.set_color("white")

    total = net.sum()
    positive_major = major[major["Net_GEX"] >= 0]
    negative_major = major[major["Net_GEX"] < 0]

    def level_rows(name, levels, color, per_line=6):
        # Each row is a list of (text, colour) runs; None means plain white.
        entries = [f"{strike:,.0f} {_fmt_gex(gex)}"
                   for strike, gex in zip(levels["Strike"], levels["Net_GEX"])]
        if not entries:
            return [[(f"{name:<15}", None), ("none", None)]]
        rows = []
        for i in range(0, len(entries), per_line):
            row = [(f"{name if i == 0 else '':<15}", None)]
            for j, entry in enumerate(entries[i:i + per_line]):
                row += [("   ", None)] * (j > 0) + [(entry, color)]
            rows.append(row)
        return rows

    green, red = "#4ade80", "#f87171"
    footer_rows = [
        [("Net GEX: ", None),
         (f"{'+' if total >= 0 else '-'}¥{abs(total) / 1e9:,.2f}B", green if total >= 0 else red),
         (f" per 1%   |   {bar_label}: {bars.sum():,.0f} contracts", None)],
        *level_rows("Positive GEX:", positive_major, green),
        *level_rows("Negative GEX:", negative_major, red),
    ]
    _draw_colored_footer(fig, footer_rows)
    fig.canvas.draw_idle()


CROSSHAIR_STYLE = dict(color="white", linewidth=1, linestyle=":", alpha=0.7)
CROSSHAIR_LABEL_BBOX = dict(boxstyle="round,pad=0.25", facecolor="white",
                            edgecolor="none", alpha=0.9)


def attach_crosshair(fig, ax_gex, ax_vol):
    """Wire up a hover crosshair with live value labels on both axes.

    matplotlib.widgets.MultiCursor draws synced lines but has no hook for
    labels, so this reimplements that part directly: a horizontal line
    tracking strike is mirrored onto both panels (they share the same
    y-range), a vertical line tracks whichever panel's x-value (gamma
    exposure or volume) is under the mouse, and each gets a small badge
    showing the exact number rather than making you read it off the grid.

    Returns the event-handler connection ids; draw() clears both axes on
    every redraw (expiry switch via the radio buttons), which strips out
    whatever artists this attached last time, so the caller must call this
    again after every draw() and disconnect the ids this returns first.
    """
    axes = (ax_gex, ax_vol)
    value_fmt = {ax_gex: _fmt_gex, ax_vol: _fmt_number}

    vlines, hlines, strike_label, value_labels = {}, {}, None, {}
    for ax in axes:
        vlines[ax] = ax.axvline(ax.get_xlim()[0], visible=False, **CROSSHAIR_STYLE)
        hlines[ax] = ax.axhline(ax.get_ylim()[0], visible=False, **CROSSHAIR_STYLE)
        # Value badge just under each panel's own x-axis, in data coordinates
        # for x (tracks the cursor) and axes-fraction for y (stays put).
        value_labels[ax] = ax.text(0.5, -0.012, "", transform=ax.get_xaxis_transform(),
                                   ha="center", va="top", fontsize=20, fontweight="bold",
                                   color="black", bbox=CROSSHAIR_LABEL_BBOX,
                                   visible=False, clip_on=False)

    # One strike badge (not one per panel) since both panels show the same
    # strike; putting it on the wide left panel keeps it clear of the
    # narrow gap between the two.
    strike_label = ax_gex.text(-0.012, 0.5, "", transform=ax_gex.get_yaxis_transform(),
                               ha="right", va="center", fontsize=20, fontweight="bold",
                               color="black", bbox=CROSSHAIR_LABEL_BBOX,
                               visible=False, clip_on=False)

    def hide_all():
        for ax in axes:
            vlines[ax].set_visible(False)
            hlines[ax].set_visible(False)
            value_labels[ax].set_visible(False)
        strike_label.set_visible(False)

    def on_move(event):
        hovered = event.inaxes if event.inaxes in axes else None
        if hovered is None or event.xdata is None or event.ydata is None:
            hide_all()
            fig.canvas.draw_idle()
            return

        for ax in axes:
            hlines[ax].set_ydata((event.ydata, event.ydata))
            hlines[ax].set_visible(True)
            is_hovered = ax is hovered
            vlines[ax].set_visible(is_hovered)
            value_labels[ax].set_visible(is_hovered)
            if is_hovered:
                vlines[ax].set_xdata((event.xdata, event.xdata))
                value_labels[ax].set_position((event.xdata, -0.012))
                value_labels[ax].set_text(value_fmt[ax](event.xdata))

        strike_label.set_visible(True)
        strike_label.set_position((-0.012, event.ydata))
        strike_label.set_text(_fmt_number(event.ydata))
        fig.canvas.draw_idle()

    def on_leave(_event):
        hide_all()
        fig.canvas.draw_idle()

    return [
        fig.canvas.mpl_connect("motion_notify_event", on_move),
        fig.canvas.mpl_connect("figure_leave_event", on_leave),
    ]


def show(df, spot, multiplier):
    plt.style.use("dark_background")
    plt.rcParams["font.family"] = "monospace"
    fig, (ax_gex, ax_vol) = plt.subplots(
        1, 2, figsize=(18, 22), gridspec_kw={"width_ratios": [1, 0.45]})
    fig.patch.set_facecolor("#1A1A1A")

    expiries = sorted(df["Expiry"].unique())
    labels = (["All expiries"] + expiries) if len(expiries) > 1 else expiries

    # draw() clears both axes on every redraw, which also wipes out any
    # crosshair artists already sitting on them, so it has to be
    # reattached after each draw() rather than once up front.
    crosshair_cids = []

    def render(label):
        subset = df if label == "All expiries" else df[df["Expiry"] == label]
        draw(fig, ax_gex, ax_vol, subset, spot, label, multiplier)
        for cid in crosshair_cids:
            fig.canvas.mpl_disconnect(cid)
        crosshair_cids[:] = attach_crosshair(fig, ax_gex, ax_vol)

    render(labels[0])
    fig.tight_layout(rect=(0.0, 0.075, 1.0, 1.0))  # room for the 4-line footer

    radio = None
    if len(labels) > 1:
        # tight_layout has just sized the left margin to fit the strike
        # tick labels and axis title; widen it by the selector's width so
        # the selector sits beside them rather than on top of them.
        radio_x, radio_width, gap = 0.01, 0.12, 0.01
        fig.subplots_adjust(left=radio_x + radio_width + gap + fig.subplotpars.left)
        ax_radio = fig.add_axes((radio_x, 0.4, radio_width, 0.2), facecolor="#2A2A2A")
        radio = RadioButtons(ax_radio, labels)  # keep a reference or it is GC'd
        for text in radio.labels:
            text.set_fontsize(15)
            text.set_color("white")
        radio.on_clicked(render)

    plt.show()
    return radio


# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", help="read a saved chain CSV instead of fetching from JPX")
    parser.add_argument("--save-csv", metavar="PATH", help="write the fetched chain to a CSV")
    parser.add_argument("--save-html", action="store_true",
                        help="keep each fetched page as jpx_options_chain_<path>.html")
    parser.add_argument("--spot", type=float, help="underlying price (default: inferred from deltas)")
    parser.add_argument("--asof", type=date.fromisoformat, default=date.today(),
                        help="valuation date, YYYY-MM-DD (default: today)")
    parser.add_argument("--multiplier", type=float, default=DEFAULT_MULTIPLIER,
                        help=f"contract multiplier (default: {DEFAULT_MULTIPLIER})")
    parser.add_argument("--gamma", choices=("bs", "file"), default="bs",
                        help="rebuild gamma from delta (bs) or use the published column (file)")
    parser.add_argument("--no-plot", action="store_true", help="print the summary only")
    args = parser.parse_args(argv)

    raw = read_chain_csv(args.csv) if args.csv else fetch_chain(save_html=args.save_html)
    if args.save_csv:
        raw.to_csv(args.save_csv, index=False)
        print(f"Wrote {len(raw)} rows to {args.save_csv}")

    chain = prepare_chain(raw, asof=args.asof)
    spot = args.spot if args.spot is not None else infer_spot(chain)
    if args.spot is None:
        print(f"Spot inferred from deltas: {spot:,.0f}")

    chain = attach_gamma(chain, spot, source=args.gamma)
    chain = compute_gex(chain, spot, multiplier=args.multiplier)

    print(f"\n{len(chain)} strikes across {chain['Expiry'].nunique()} expiries"
          f"  |  gamma source: {args.gamma}")
    print(f"Call OI {chain['Call_OI'].sum():,.0f}   Put OI {chain['Put_OI'].sum():,.0f}"
          f"   P/C OI ratio {chain['Put_OI'].sum() / max(chain['Call_OI'].sum(), 1):.2f}")
    print(f"Call GEX  ¥{chain['Call_GEX'].sum() / 1e9:>10,.2f}B per 1%")
    print(f"Put  GEX  ¥{chain['Put_GEX'].sum() / 1e9:>10,.2f}B per 1%")
    print(f"Net  GEX  ¥{chain['Net_GEX'].sum() / 1e9:>10,.2f}B per 1%")

    flip = gamma_flip(chain, spot, multiplier=args.multiplier)
    pain = max_pain(chain)
    print(f"Gamma flip: {flip:,.0f}" if flip else "Gamma flip: not found in +/-15% of spot")
    print(f"Max pain:   {pain:,.0f}" if pain else "Max pain:   n/a")

    top = chain.reindex(chain["Net_GEX"].abs().sort_values(ascending=False).index).head(10)
    print("\nLargest absolute GEX strikes:")
    for _, row in top.iterrows():
        print(f"  {row['Strike']:>8,.0f}  ¥{row['Net_GEX'] / 1e9:>7,.2f}B"
              f"   callOI {row['Call_OI']:>6,.0f}  putOI {row['Put_OI']:>6,.0f}")

    if not args.no_plot:
        show(chain, spot, args.multiplier)
    return 0


if __name__ == "__main__":
    sys.exit(main())
