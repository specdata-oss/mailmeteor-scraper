import asyncio
import json
import gspread
import re
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
for i, url in enumerate(urls[:5]):
    print(f"URL {i+1}: {url}")


def extract_name(url):
    query = parse_qs(urlparse(url).query)
    name = query.get("name", ["Unknown"])[0]
    return name.replace("+", " ")


async def scrape(context, url):
    name = extract_name(url)
    page = await context.new_page()
    
    try:
        print(f"\nProcessing: {name}")
        print(f"URL: {url}")
        
        # Navigate to the page
        await page.goto(url, timeout=60000)
        
        # Wait for the page to load completely
        await page.wait_for_load_state("networkidle")
        
        # Wait a bit for any dynamic content to load
        await asyncio.sleep(5)
        
        # Method 1: Look for email in specific elements
        email_found = None
        
        # Try to find email by common selectors
        selectors = [
            'div[class*="email"]',
            'span[class*="email"]',
            'p[class*="email"]',
            'div[class*="result"]',
            'div[class*="found"]',
            '.email-address',
            '#email-result',
            '[data-testid="email"]',
            'div:has-text("@")',
            'span:has-text("@")',
            'p:has-text("@")',
        ]
        
        for selector in selectors:
            try:
                elements = await page.query_selector_all(selector)
                for element in elements:
                    text = await element.text_content()
                    if text and '@' in text:
                        # Clean up the text and extract email
                        text = text.strip()
                        # Look for email pattern
                        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                        emails = re.findall(email_pattern, text)
                        if emails:
                            email_found = emails[0]
                            print(f"  ✅ Found email via selector '{selector}': {email_found}")
                            break
                if email_found:
                    break
            except:
                continue
        
        # Method 2: If not found, search entire page text
        if not email_found:
            print("  Searching entire page text...")
            page_text = await page.text_content('body')
            
            # Look for email pattern
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            emails = re.findall(email_pattern, page_text)
            
            if emails:
                # Filter out common false positives
                valid_emails = []
                for email in emails:
                    # Check if it's likely a real email (not an image filename, etc.)
                    if not any(ext in email.lower() for ext in ['.png', '.jpg', '.gif', '.svg', '.css', '.js']):
                        if '.' in email.split('@')[1]:  # Domain has a dot
                            valid_emails.append(email)
                
                if valid_emails:
                    email_found = valid_emails[0]
                    print(f"  ✅ Found email in page text: {email_found}")
        
        # Method 3: Look for the email in the specific result area
        if not email_found:
            print("  Looking for email in result area...")
            try:
                # Try to find the result container (often appears after search)
                result_container = await page.query_selector('div:has-text("valid")')
                if result_container:
                    parent = await result_container.query_selector('xpath=..')
                    if parent:
                        text = await parent.text_content()
                        emails = re.findall(email_pattern, text)
                        if emails:
                            email_found = emails[0]
                            print(f"  ✅ Found email near 'valid' text: {email_found}")
            except:
                pass
        
        # Method 4: Take screenshot for debugging
        if not email_found:
            screenshot_path = f"debug_{name.replace(' ', '_')}.png"
            await page.screenshot(path=screenshot_path)
            print(f"  📸 Screenshot saved: {screenshot_path}")
            
            # Also save page HTML for debugging
            html_path = f"debug_{name.replace(' ', '_')}.html"
            html_content = await page.content()
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f"  📄 HTML saved: {html_path}")
        
        if email_found:
            print(f"✅ SUCCESS: {email_found}")
            return [name, email_found, "Valid"]
        else:
            print(f"❌ No email found for {name}")
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
        # Launch browser
        browser = await p.chromium.launch(headless=True)
        
        # Create context with realistic settings
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        # Process URLs one by one
        for i, url in enumerate(urls):
            print(f"\n--- Processing {i+1}/{len(urls)} ---")
            result = await scrape(context, url)
            results.append(result)
            # Add delay between requests
            await asyncio.sleep(3)
        
        await browser.close()

    if results:
        print(f"\nWriting {len(results)} results to Google Sheet...")
        
        # Clear existing data but keep headers
        try:
            sheet_output.clear()
            sheet_output.append_row(["Name", "Email", "Status"])
        except:
            # If sheet is empty, just add headers
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
        status_icon = "✅" if result[1] != "Not Found" and result[1] != "Error" else "❌"
        print(f"{status_icon} {result[0]:20} -> {result[1]}")


if __name__ == "__main__":
    asyncio.run(run())
