from bs4 import BeautifulSoup
from babel.numbers import parse_decimal
import csv

MISSING = ""   # JPX prints "-" for cells with no quote; keep them empty in the CSV
               # so downstream code can tell "no quote" from a genuine zero.


def num(text):
    """Parse a JPX numeric cell, returning MISSING for an empty/'-' cell.

    The original code did text.replace('-', '0'), which also mangled negative
    values ("-0.4567" became "00.4567", i.e. a sign flip) and turned missing
    quotes into real zeros.
    """
    text = text.strip()
    if text in ("", "-", "\u2212", "\u2014"):
        return MISSING
    return float(parse_decimal(text.replace(",", "")))


# Open and read the local HTML file
with open("jpx_options_chain.html", "r", encoding="utf-8") as file:
    html = file.read()

# Parse the HTML content using BeautifulSoup with lxml parser
soup = BeautifulSoup(html, "lxml")

for btn in soup.find_all('button'):
    btn.decompose()  # removes the tag and its content

for span in soup.find_all('span', class_='atm-mark'):
    span.decompose()  # removes the tag and its content


# Find all elements with the class "row-num"
row_num = soup.find_all(class_="row-num")

# Find all elements with the class "greek  show wraps two greek tables one for calls and one for puts"
greek_table_wrapper = soup.find_all(class_=["greek", "show"])

# Extract the expiration date from the specified table
date_table = soup.find(class_="last-tradingday")

expiration_date = date_table.find("dl").find("dd").text  # YYYY/MM/DD
safe_date = expiration_date.replace("/", "-")
print(expiration_date)

print(len(row_num))
print(len(greek_table_wrapper))


# Define the field names (column headers)
fieldnames = ['Expiration Date', 'Calls', 'Last Sale', 'Net', 'Bid', 'Ask', 'Volume', 'IV', 'Delta', 'Gamma',
              'Open Interest', 'Strike', 'Puts', 'Last Sale', 'Net', 'Bid', 'Ask', 'Volume', 'IV', 'Delta', 'Gamma', 'Open Interest']

# need a for loop len(row_num) times
with open("jpx.csv", "w", newline="", encoding="utf-8") as csvfile:
    csvwriter = csv.writer(csvfile)
    # Write header
    csvwriter.writerow(fieldnames)

    for i in range(len(row_num)):
        row_num_tds = row_num[i].find_all("td")
        greek_table = greek_table_wrapper[i]

        # will return two <td> elements call and put greek table inside each
        tb = greek_table.find_all('td', colspan="8")
        callGreeks = tb[0].find_all("td", class_="a-right")
        putGreeks = tb[1].find_all("td", class_="a-right")

        record = [
            expiration_date,
            "c",                       # Calls
            0,                         # Last Sale
            0,                         # Net
            0,                         # Bid
            0,                         # Ask
            num(row_num_tds[2].text),   # Volume
            0,                         # IV
            num(callGreeks[0].text),    # Delta
            num(callGreeks[1].text),    # Gamma
            num(row_num_tds[1].text),   # Open Interest
            num(row_num_tds[8].text),   # Strike
            "p",                       # Puts
            0,                         # Last Sale
            0,                         # Net
            0,                         # Bid
            0,                         # Ask
            num(row_num_tds[14].text),  # Volume
            0,                         # IV
            num(putGreeks[0].text),     # Delta
            num(putGreeks[1].text),     # Gamma
            num(row_num_tds[15].text),  # Open Interest
        ]

        # print(record)

        csvwriter.writerow(record)
