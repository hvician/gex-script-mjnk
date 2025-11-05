from bs4 import BeautifulSoup
from babel.numbers import parse_number
from babel.numbers import parse_decimal
import csv

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
date_table = soup.find(class_=["date-table", "last-tradingday"])

expiration_date = date_table.find("dl").find("dd").text  # YYYY/MM/DD
print(expiration_date)

print(len(row_num))
print(len(greek_table_wrapper))


# Define the field names (column headers)
fieldnames = ['Expiration Date', 'Calls', 'Last Sale', 'Net', 'Bid', 'Ask', 'Volume', 'IV', 'Delta', 'Gamma',
              'Open Interest', 'Strike', 'Puts', 'Last Sale', 'Net', 'Bid', 'Ask', 'Volume', 'IV', 'Delta', 'Gamma', 'Open Interest']

# need a for loop len(row_num) times
with open("jpx_options_chain.csv", "w", newline="", encoding="utf-8") as csvfile:
    csvwriter = csv.DictWriter(csvfile, fieldnames=fieldnames)
    # Write header
    csvwriter.writeheader()

    for i in range(len(row_num)):
        row_num_tds = row_num[i].find_all("td")
        greek_table = greek_table_wrapper[i]

        # will return two <td> elements call and put greek table inside each
        tb = greek_table.find_all('td', colspan="8")
        callGreeks = tb[0].find_all("td", class_="a-right")
        putGreeks = tb[1].find_all("td", class_="a-right")

        record = []
        record.append(expiration_date)
        record.append("c")   # Calls
        record.append(0)  # Last Sale
        record.append(0)  # Net
        record.append(0)  # Bid
        record.append(0)  # Ask
        record.append(parse_number(row_num_tds[2].text.strip().replace(
            '-', '0')))  # Volume
        record.append(0)  # IV
        record.append(float(parse_decimal(
            callGreeks[0].text.strip().replace('-', '0'))))  # Delta
        record.append(float(parse_decimal(
            callGreeks[1].text.strip().replace('-', '0'))))  # Gamma
        record.append(parse_number(row_num_tds[1].text.strip().replace(
            '-', '0')))  # Open Interest
        record.append(parse_number(
            row_num_tds[8].text.strip()))  # Strike
        record.append("p")   # Puts
        record.append(0)  # Last Sale
        record.append(0)  # Net
        record.append(0)  # Bi
        record.append(0)  # Ask
        record.append(
            # Volume
            int(row_num_tds[14].text.strip().replace('-', '0').strip()))
        record.append(0)  # IV
        record.append(float(parse_decimal(
            putGreeks[0].text.strip().replace('-', '0'))))  # Delta
        record.append(float(parse_decimal(
            putGreeks[1].text.strip().replace('-', '0'))))  # Gamma
        record.append(parse_number(row_num_tds[15].text.strip().replace(
            '-', '0')))  # Open Interest

        print(record)

        # csvwriter.writerow([expiration_date, row_num_text, greek_show_text])
