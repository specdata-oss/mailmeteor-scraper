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

print("URLs found:", len(urls))
for i, url in enumerate(urls[:5]):  # Print first 5 URLs for debugging
    print(f"URL {i+1}: {url}")


def extract_name(url):
    query = parse_qs(urlparse(url).query)
    name = query.get("name", ["Unknown"])[0]
    return name.replace("+", " ")


async def scrape(context, url):
    name = extract_name(url)
    page = await context.new_page()
    
    email_found = None
    api_responses = []  # Store API responses for debugging

    async def capture_response(response):
        nonlocal email_found
        
        # Log all API responses for debugging
        url = response.url
        print(f"  Response from: {url}")
        
        # Look for email in any JSON response
        try:
            if "application/json" in response.headers.get("content-type", ""):
                data = await response.json()
                api_responses.append({"url": url, "data": data})
                
                # Check various possible email fields
                if isinstance(data, dict):
                    # Common email field names
                    for field in ["email", "Email", "EMAIL", "data", "result", "email_address"]:
                        if field in data and data[field] and "@" in str(data[field]):
                            email_found = data[field]
                            print(f"  ✅ Found email in {field}: {email_found}")
                            return
                    
                    # Check nested objects
                    if "data" in data and isinstance(data["data"], dict):
                        for field in ["email", "Email"]:
                            if field in data["data"] and data["data"][field]:
                                email_found = data["data"][field]
                                print(f"  ✅ Found email in data.{field}: {email_found}")
                                return
        except:
            pass

    page.on("response", capture_response)

    try:
        print(f"\nProcessing: {name}")
        print(f"URL: {url}")
        
        # Navigate to the page
        await page.goto(url, timeout=60000)
        
        # Wait for the page to load and API calls to complete
        print("  Waiting for API responses...")
        
        # Wait up to 30 seconds for email to be found
        for i in range(15):  # 15 * 2 = 30 seconds
            if email_found:
                break
            
            # Also check the page content for email (in case it's in the DOM)
            try:
                # Look for email in the page text
                page_text = await page.text_content('body')
                if page_text and "@" in page_text:
                    # Simple email extraction from text
                    import re
                    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                    emails = re.findall(email_pattern, page_text)
                    if emails:
                        email_found = emails[0]
                        print(f"  ✅ Found email in page text: {email_found}")
                        break
            except:
                pass
            
            await asyncio.sleep(2)
            print(f"  Waiting... ({i+1}/15)")
        
        if email_found:
            print(f"✅ FOUND: {email_found}")
            return [name, email_found, "Valid"]
        else:
            print(f"❌ No email found for {name}")
            # Print debug info about API responses
            if api_responses:
                print(f"  API responses received: {len(api_responses)}")
                for resp in api_responses:
                    print(f"  - {resp['url'][:50]}...")
            else:
                print("  No API responses captured")
            
            # Check if email is visible in the page
            try:
                await page.screenshot(path=f"debug_{name.replace(' ', '_')}.png")
                print(f"  Screenshot saved: debug_{name.replace(' ', '_')}.png")
            except:
                pass
            
            return [name, "Not Found", "Not Found"]

    except Exception as e:
        print(f"❌ ERROR for {name}: {str(e)}")
        return [name, "Error", str(e)]

    finally:
        await page.close()


async def run():
    results = []
    
    print("\n" + "="*50)
    print("Starting scraper...")
    print("="*50 + "\n")

    async with async_playwright() as p:
        # Launch with headless=False temporarily to see what's happening
        browser = await p.chromium.launch(headless=True)
        
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        # Process URLs one by one for better debugging
        for i, url in enumerate(urls):
            print(f"\n--- Processing {i+1}/{len(urls)} ---")
            result = await scrape(context, url)
            results.append(result)
            # Add a small delay between requests to avoid rate limiting
            await asyncio.sleep(2)
        
        await browser.close()

    if results:
        print(f"\nWriting {len(results)} results to Google Sheet...")
        sheet_output.clear()  # Clear existing data
        # Add headers
        sheet_output.append_row(["Name", "Email", "Status"])
        # Add results
        sheet_output.append_rows(results)
        print("✅ Results written to Google Sheet")

    print(f"\nFinished processing {len(results)} URLs")
    
    # Print summary
    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)
    for result in results:
        print(f"{result[0]:20} -> {result[1]}")


if __name__ == "__main__":
    asyncio.run(run())
