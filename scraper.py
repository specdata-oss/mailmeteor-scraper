import re
import asyncio
import gspread
from urllib.parse import urlparse, parse_qs
from oauth2client.service_account import ServiceAccountCredentials
from playwright.async_api import async_playwright

SPREADSHEET_NAME = "EmailScraper"

CONCURRENT_PAGES = 10
BATCH_WRITE_SIZE = 50


# -------------------------
# GOOGLE SHEETS
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


urls = [
    u.strip()
    for u in sheet_input.col_values(1)
    if u.strip().startswith("http")
]


# -------------------------
# HELPERS
# -------------------------
def extract_name(url):

    try:
        query = parse_qs(urlparse(url).query)
        name = query.get("name", ["Unknown"])[0]
        return name.replace("+", " ")
    except:
        return "Unknown"


def extract_email(text):

    regex = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"

    matches = re.findall(regex, text)

    blacklist = ["bootstrap", ".css", ".js"]

    for email in matches:
        if not any(b in email for b in blacklist):
            return email

    return None


# -------------------------
# SCRAPE ONE PAGE
# -------------------------
async def scrape_page(context, url):

    name = extract_name(url)

    page = await context.new_page()

    try:

        await page.goto(url, timeout=60000)

        try:
            await page.wait_for_selector("text=Status:", timeout=15000)
        except:
            pass

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

        print(name, email)

        return [name, email, status]

    except Exception as e:

        print("Error:", e)

        return [name, "Error", str(e)]

    finally:
        await page.close()


# -------------------------
# MAIN SCRAPER
# -------------------------
async def run_scraper():

    results_buffer = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context()

        semaphore = asyncio.Semaphore(CONCURRENT_PAGES)

        async def worker(url):

            async with semaphore:

                result = await scrape_page(context, url)

                results_buffer.append(result)

                if len(results_buffer) >= BATCH_WRITE_SIZE:

                    sheet_output.append_rows(results_buffer)

                    print("Saved", len(results_buffer), "rows")

                    results_buffer.clear()

        tasks = [worker(url) for url in urls]

        await asyncio.gather(*tasks)

        if results_buffer:

            sheet_output.append_rows(results_buffer)

        await browser.close()


asyncio.run(run_scraper())
