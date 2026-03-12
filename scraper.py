import asyncio
import json
import gspread
from urllib.parse import urlparse, parse_qs
from oauth2client.service_account import ServiceAccountCredentials
from playwright.async_api import async_playwright

SPREADSHEET_NAME = "EmailScraper"

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
client = gspread.authorize(creds)

sheet = client.open(SPREADSHEET_NAME)

sheet_input = sheet.sheet1
sheet_output = sheet.worksheet("Sheet2")

urls = [u for u in sheet_input.col_values(1) if u.startswith("http")]

print("URLs:", len(urls))


def extract_name(url):
    query = parse_qs(urlparse(url).query)
    name = query.get("name", ["Unknown"])[0]
    return name.replace("+", " ")


async def scrape(context, url):

    name = extract_name(url)
    page = await context.new_page()

    email_found = None

    async def capture_response(response):
        nonlocal email_found

        try:
            if "email-finder" in response.url:

                data = await response.json()

                if "email" in data and data["email"]:
                    email_found = data["email"]

        except:
            pass

    page.on("response", capture_response)

    try:

        print("Processing:", name)

        await page.goto(url, timeout=60000)

        # wait for lookup to run
        for _ in range(20):

            if email_found:
                print("FOUND:", email_found)
                return [name, email_found, "Valid"]

            await asyncio.sleep(2)

        return [name, "Not Found", "Not Found"]

    except Exception as e:

        return [name, "Error", str(e)]

    finally:

        await page.close()


async def run():

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context()

        tasks = [scrape(context, u) for u in urls]

        results = await asyncio.gather(*tasks)

        await browser.close()

    if results:
        sheet_output.append_rows(results)

    print("Finished:", len(results))


asyncio.run(run())
