"""Fetch the JPX Nikkei 225 mini options chain and write it to jpx.csv.

Used to require manually opening https://svc.qri.jp/jpx/english/nkopmini/1
in a browser, copying the id="priceTable" div out of dev tools, and pasting
it into jpx_options_chain.html. That's no longer necessary: JPX's edge
server just 400s any request with no Referer header (or one outside its own
domain) - send the right header and the page comes back fully rendered, no
JS execution needed.

The site has several expiration pages at /jpx/english/nkopmini/<path>, and
they aren't sequential months of one series - "" and "1" are JPX's two
near-dated Nikkei 225 mini expirations (a weekly and the standard one); "2"
is the next weekly, etc. This fetches "" and "1" and writes both into one
jpx.csv, each row tagged with its own expiration date - jpx_gex.py already
groups by expiry and offers a selector for exactly this.
"""

import csv

import requests
from bs4 import BeautifulSoup
from babel.numbers import parse_decimal

BASE_URL = "https://svc.qri.jp/jpx/english/nkopmini/"
# The two near-dated expirations the GEX pipeline wants. "2"-"5" exist too
# (next weekly, further-out months) - add paths here to pull more.
EXPIRY_PATHS = ["", "1"]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    # JPX's edge server 400s any request with no Referer (or one outside its
    # own domain), with an error page telling you to access via the Japan
    # Exchange Group website - this is exactly that entry point.
    "Referer": "https://www.jpx.co.jp/english/",
}

FIELDNAMES = ["Expiration Date", "Calls", "Last Sale", "Net", "Bid", "Ask", "Volume", "IV",
             "Delta", "Gamma", "Open Interest", "Strike", "Puts", "Last Sale", "Net", "Bid",
             "Ask", "Volume", "IV", "Delta", "Gamma", "Open Interest"]

MISSING = ""   # JPX prints "-" for cells with no quote; keep them empty in the CSV
               # so downstream code can tell "no quote" from a genuine zero.


def num(text):
    """Parse a JPX numeric cell, returning MISSING for an empty/'-' cell.

    A naive text.replace('-', '0') would also mangle negative values
    ("-0.4567" becoming "00.4567", i.e. a sign flip) and turn missing quotes
    into real zeros, so this checks for the dash explicitly instead.
    """
    text = text.strip()
    if text in ("", "-", "−", "—"):
        return MISSING
    return float(parse_decimal(text.replace(",", "")))


def fetch_html(path, timeout=15):
    """Fetch one expiration page's HTML."""
    response = requests.get(BASE_URL + path, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text


def parse_chain(html):
    """Parse one fetched page into a list of CSV rows (one call+put per strike)."""
    soup = BeautifulSoup(html, "lxml")
    # Scope to the price table itself: the live page is the full JPX page
    # (nav, footer, etc.), not just the hand-pasted div the old workflow
    # used, so stray same-named classes elsewhere on the page need guarding
    # against.
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
        row_num_tds = row.find_all("td")
        # Two <td> elements inside the greek wrapper: call and put tables.
        tb = greek_table.find_all("td", colspan="8")
        call_greeks = tb[0].find_all("td", class_="a-right")
        put_greeks = tb[1].find_all("td", class_="a-right")

        records.append([
            expiration_date,
            "c",                        # Calls
            0,                          # Last Sale
            0,                          # Net
            0,                          # Bid
            0,                          # Ask
            num(row_num_tds[2].text),   # Volume
            0,                          # IV
            num(call_greeks[0].text),   # Delta
            num(call_greeks[1].text),   # Gamma
            num(row_num_tds[1].text),   # Open Interest
            num(row_num_tds[8].text),   # Strike
            "p",                        # Puts
            0,                          # Last Sale
            0,                          # Net
            0,                          # Bid
            0,                          # Ask
            num(row_num_tds[14].text),  # Volume
            0,                          # IV
            num(put_greeks[0].text),    # Delta
            num(put_greeks[1].text),    # Gamma
            num(row_num_tds[15].text),  # Open Interest
        ])

    return expiration_date, records


def main():
    all_records = []
    for path in EXPIRY_PATHS:
        html = fetch_html(path)
        # Keep a saved copy for debugging - same role the hand-pasted file
        # used to play, just produced by the script now. One file per path
        # so both expirations' raw HTML stay inspectable.
        suffix = path or "base"
        with open(f"jpx_options_chain_{suffix}.html", "w", encoding="utf-8") as f:
            f.write(html)

        expiration_date, records = parse_chain(html)
        print(f"nkopmini/{path or '(base)'}: expiry {expiration_date}, "
              f"{len(records)} strikes")
        all_records.extend(records)

    if not all_records:
        raise SystemExit("No option data fetched from any expiration page.")

    with open("jpx.csv", "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(FIELDNAMES)
        writer.writerows(all_records)

    print(f"\nWrote {len(all_records)} total rows across "
          f"{len(EXPIRY_PATHS)} expirations to jpx.csv")


if __name__ == "__main__":
    main()
