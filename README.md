# gex-script-py

Gamma exposure (GEX) analysis for Nikkei 225 mini options listed on the Japan
Exchange Group (JPX).

The pipeline has two steps:

1. **`jpx_scraper.py`** fetches the live option chain from JPX and writes it to
   `jpx.csv`.
2. **`jpx_gex.py`** reads `jpx.csv`, computes dealer gamma exposure per strike,
   and plots it along with the gamma flip level and max pain.

## What it shows

- **Net GEX by strike**: dealer gamma exposure in yen per 1% move in the
  underlying. Positive bars are green and negative bars are red. The usual
  dealer convention applies: dealers are long calls and short puts against
  customer flow, so call gamma adds and put gamma subtracts. Spot, gamma flip
  and max pain are drawn as dashed lines.
- **Gamma flip**: the spot level where total dealer gamma crosses zero. It is
  found by re-evaluating gamma across a ±15% grid of hypothetical spot
  prices, not by summing today's gamma cumulatively across strikes.
- **Max pain**: the settlement strike that minimizes the total in-the-money
  value owed to option holders.
- **Volume and open interest** per strike in a second panel.

The chart has an expiry selector (each expiration on its own, or all
combined) and a hover crosshair that shows live values.

## Why gamma is recomputed

JPX publishes gamma rounded to 4 decimal places. With the Nikkei around
65,000, true gamma is on the order of 1e-4, so that rounding collapses the
whole chain onto a few values (0.0000, 0.0001, 0.0002) and the profile
disappears.

So by default `jpx_gex.py` ignores the published gamma. It backs out implied
volatility from the published delta, which has plenty of resolution at 4
decimals, fits a smooth smile, and evaluates Black-Scholes gamma from that.
To use the exchange's own gamma column instead, pass `--gamma file`.

If you don't pass `--spot`, spot is inferred as the strike where call delta
and put delta both cross 0.5.

## Installation

```
pip install requests beautifulsoup4 lxml babel pandas numpy scipy matplotlib
```

## Usage

```
python jpx_scraper.py              # fetch the chain -> jpx.csv
python jpx_gex.py                  # summary + interactive chart
```

`jpx_gex.py` options:

| Option | Default | Meaning |
| --- | --- | --- |
| `csv` | `jpx.csv` | Input chain CSV |
| `--spot` | inferred | Underlying price |
| `--asof` | today | Valuation date (`YYYY-MM-DD`), used for time to expiry |
| `--multiplier` | `100` | Contract multiplier in yen per index point |
| `--gamma {bs,file}` | `bs` | Rebuild gamma from delta, or use the published column |
| `--no-plot` | off | Print the text summary only |

> **Multiplier:** the default of 100 matches the Nikkei 225 **mini** options
> that the scraper pulls. For a chain of standard Nikkei 225 options, pass
> `--multiplier 1000`.

The console summary lists total call, put and net GEX, the put/call
open-interest ratio, the gamma flip, max pain, and the ten strikes with the
largest absolute GEX.

## Data source

The scraper pulls from `https://svc.qri.jp/jpx/english/nkopmini/<path>`. By
default it fetches the two near-dated expirations: `""` (weekly) and `"1"`
(standard). Both go into one `jpx.csv`, and each row is tagged with its
expiration date. To pull further-out expirations, add more paths (`"2"` to
`"5"`) to `EXPIRY_PATHS` in `jpx_scraper.py`.

JPX's server rejects requests that have no `Referer` header from its own
domain, so the scraper sends `Referer: https://www.jpx.co.jp/english/`. The
raw HTML of each page is saved as `jpx_options_chain_<path>.html` for
debugging. These files and `jpx.csv` are git-ignored.

## Other files

- **`jpx-GEXbot6.py`**: an older interactive GEX tool written for SPX
  (CBOE "quotedata" CSV exports). It asks for the file path and spot price at
  the prompt and needs PyQt5 and tkinter. It is not part of the JPX pipeline.
