import requests
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials

scope = [
'https://spreadsheets.google.com/feeds',
'https://www.googleapis.com/auth/drive'
]

creds = ServiceAccountCredentials.from_json_keyfile_name('creds.json', scope)
client = gspread.authorize(creds)

spreadsheet = client.open("EmailScraper")

sheet_input = spreadsheet.worksheet("Sheet1")
sheet_output = spreadsheet.worksheet("Sheet2")

urls = sheet_input.col_values(1)[1:]

for url in urls:

    if not url:
        continue

    try:

        response = requests.get(url, timeout=15)
        html = response.text

        email_match = re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', html)

        name_match = re.search(r'name=([^&]+)', url)

        if name_match:
            name = name_match.group(1).replace("+"," ")
        else:
            name = "Unknown"

        if email_match:
            email = email_match.group(0)
            status = "Valid"
        else:
            email = "Not Found"
            status = "Not Found"

        sheet_output.append_row([name,email,status])

    except Exception as e:

        sheet_output.append_row(["Error","Error","Error"])
