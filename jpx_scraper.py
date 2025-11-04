from bs4 import BeautifulSoup

# Open and read the local HTML file
with open("jpx_options_chain.html", "r", encoding="utf-8") as file:
    html = file.read()

# Parse the HTML content using BeautifulSoup with lxml parser
soup = BeautifulSoup(html, "lxml")


row_num = soup.find_all(class_="row-num")

# Find all elements with the class "greek  show"
# there are two gree tables in the
gree_table_wrapper = soup.find_all(class_=["greek", "show"])


print(len(row_num))
print(len(gree_table_wrapper))
