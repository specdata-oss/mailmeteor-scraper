import re
import asyncio
import gspread
from urllib.parse import urlparse, parse_qs
from oauth2client.service_account import ServiceAccountCredentials
from playwright.async_api import async_playwright

SPREADSHEET_NAME = "EmailScraper"

CONCURRENT_PAGES = 3
CHECK_INTERVAL = 1
MAX_CHECKS = 60


# ---------------- GOOGLE SHEETS ----------------
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
client = gspread.authorize(creds)

spreadsheet = client.open(SPREADSHEET_NAME)
sheet_input = spreadsheet.sheet1
sheet_output = spreadsheet.worksheet("Sheet2")

urls = [u.strip() for u in sheet_input.col_values(1) if u.strip().startswith("http")]

print("URLs found:", len(urls))


# ---------------- HELPERS ----------------
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


# ---------------- SCRAPE PAGE ----------------
async def scrape_page(context, url):

    name = extract_name(url)
    page = await context.new_page()

    try:
        print("Processing:", name)

        await page.goto(url, timeout=60000)

        # wait for page UI
        await page.wait_for_selector("text=Email Finder", timeout=20000)

        for _ in range(MAX_CHECKS):

            text = await page.inner_text("body")

            email = extract_email(text)

            if email:
                print("FOUND:", email)
                return [name, email, "Valid"]

            if "No results found" in text:
                return [name, "Not Found", "Not Found"]

            if "Searching" in text:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            await asyncio.sleep(CHECK_INTERVAL)

        return [name, "Not Found", "Timeout"]

    except Exception as e:
        print("Error:", e)
        return [name, "Error", str(e)]

    finally:
        await page.close()


# ---------------- MAIN ----------------
async def run_scraper():

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )

        semaphore = asyncio.Semaphore(CONCURRENT_PAGES)

        async def worker(url):
            async with semaphore:
                result = await scrape_page(context, url)
                results.append(result)

        tasks = [worker(url) for url in urls]

        await asyncio.gather(*tasks)

        await browser.close()

    if results:
        sheet_output.append_rows(results)

    print("Finished. Rows written:", len(results))


asyncio.run(run_scraper())
