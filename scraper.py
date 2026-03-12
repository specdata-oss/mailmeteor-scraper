import re
import asyncio
import gspread
from urllib.parse import urlparse, parse_qs
from oauth2client.service_account import ServiceAccountCredentials
from playwright.async_api import async_playwright

SPREADSHEET_NAME = "EmailScraper"

# -------------------------
# GOOGLE SHEETS CONNECTION
# -------------------------
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
client = gspread.authorize(creds)

spreadsheet = client.open(SPREADSHEET_NAME)

sheet_input = spreadsheet.sheet1
sheet_output = spreadsheet.worksheet("Sheet2")

urls = sheet_input.col_values(1)

# -------------------------
# HELPERS
# -------------------------
def extract_name(url):
    query = parse_qs(urlparse(url).query)
    name = query.get("name", ["Unknown"])[0]
    return name.replace("+", " ")

def extract_email(text):
    email_regex = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    matches = re.findall(email_regex, text)

    for email in matches:
        if "bootstrap" not in email and "css" not in email:
            return email
    return None


# -------------------------
# MAIN SCRAPER
# -------------------------
async def scrape():

    async with async_playwright() as p:

        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        for url in urls:

            name = extract_name(url)

            try:
                print(f"Processing {name}")

                await page.goto(url, timeout=60000)

                # wait for search result
                await page.wait_for_timeout(6000)

                text = await page.inner_text("body")

                email = extract_email(text)

                if email:
                    status = "Valid"
                elif "No results found" in text:
                    email = "Not Found"
                    status = "Not Found"
                else:
                    email = "Not Found"
                    status = "Unknown"

                sheet_output.append_row([name, email, status])

            except Exception as e:

                sheet_output.append_row([name, "Error", str(e)])

        await browser.close()


asyncio.run(scrape())
