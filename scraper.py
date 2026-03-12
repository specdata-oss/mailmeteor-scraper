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

# Get all URLs and filter out empty rows
urls = [u for u in sheet_input.col_values(1) if u and u.startswith("http")]

print("URLs found:", len(urls))
for i, url in enumerate(urls[:5]):
    print(f"URL {i+1}: {url}")


def extract_name(url):
    query = parse_qs(urlparse(url).query)
    name = query.get("name", ["Unknown"])[0]
    return name.replace("+", " ")


async def scrape(url, semaphore):
    async with semaphore:  # Limit concurrent requests
        name = extract_name(url)
        
        async with async_playwright() as p:
            # Launch browser with more realistic settings
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--start-maximized',
                ]
            )
            
            # Create context with realistic viewport and user agent
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                extra_http_headers={
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                }
            )
            
            page = await context.new_page()
            
            try:
                print(f"\n{'='*50}")
                print(f"Processing: {name}")
                print(f"URL: {url}")
                
                # Navigate with longer timeout but better error handling
                try:
                    response = await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                    if not response:
                        print(f"  ⚠️ No response received")
                except Exception as e:
                    print(f"  ⚠️ Navigation warning: {str(e)}")
                
                # Wait a bit for initial load
                await asyncio.sleep(3)
                
                # Try to wait for content to load
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)
                except:
                    print("  ⚠️ Network idle timeout, continuing anyway...")
                
                # Additional wait for dynamic content
                await asyncio.sleep(2)
                
                email_found = None
                
                # Method 1: Look for email in specific elements
                print("  Looking for email in page elements...")
                
                # Common selectors where email might appear
                selectors = [
                    'div[class*="email"]',
                    'span[class*="email"]',
                    'p[class*="email"]',
                    'div[class*="result"]',
                    'div[class*="found"]',
                    '.email-address',
                    '#email-result',
                    '[data-testid="email"]',
                    'div.email-display',
                    'span.email-value',
                    '.finder-result',
                    '.email-finder-result',
                ]
                
                for selector in selectors:
                    try:
                        elements = await page.query_selector_all(selector)
                        for element in elements:
                            text = await element.text_content()
                            if text and '@' in text:
                                text = text.strip()
                                email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                                emails = re.findall(email_pattern, text)
                                if emails:
                                    email_found = emails[0]
                                    print(f"  ✅ Found via selector '{selector}': {email_found}")
                                    break
                        if email_found:
                            break
                    except:
                        continue
                
                # Method 2: Look for email in page text
                if not email_found:
                    print("  Searching page text for email...")
                    try:
                        page_text = await page.text_content('body')
                        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                        emails = re.findall(email_pattern, page_text)
                        
                        # Filter valid emails
                        valid_emails = []
                        for email in emails:
                            if not any(ext in email.lower() for ext in ['.png', '.jpg', '.gif', '.svg', '.css', '.js', '.ico']):
                                if '.' in email.split('@')[1] and len(email) < 50:  # Reasonable email length
                                    valid_emails.append(email)
                        
                        if valid_emails:
                            # Prefer emails that look like real names
                            for email in valid_emails:
                                if any(name_part.lower() in email.lower() for name_part in name.split()):
                                    email_found = email
                                    print(f"  ✅ Found name-match email: {email_found}")
                                    break
                            
                            if not email_found:
                                email_found = valid_emails[0]
                                print(f"  ✅ Found email in text: {email_found}")
                    except Exception as e:
                        print(f"  ⚠️ Error searching page text: {str(e)}")
                
                # Method 3: Look for email near "valid" or "status" text
                if not email_found:
                    print("  Looking for email near status indicators...")
                    try:
                        # Look for elements containing "valid"
                        valid_elements = await page.query_selector_all('*:has-text("valid")')
                        for element in valid_elements:
                            text = await element.text_content()
                            if text and '@' in text:
                                emails = re.findall(email_pattern, text)
                                if emails:
                                    email_found = emails[0]
                                    print(f"  ✅ Found email near 'valid' text: {email_found}")
                                    break
                    except:
                        pass
                
                # Method 4: Check for email in the URL (sometimes email is in the page source as a variable)
                if not email_found:
                    print("  Checking page source for email patterns...")
                    try:
                        content = await page.content()
                        # Look for email in JavaScript variables
                        patterns = [
                            r'"email"\s*:\s*"([^"]+@[^"]+)"',
                            r"'email'\s*:\s*'([^']+@[^']+)'",
                            r'email[=:]\s*["\']([^"\']+@[^"\']+)["\']',
                        ]
                        for pattern in patterns:
                            matches = re.findall(pattern, content)
                            if matches:
                                email_found = matches[0]
                                print(f"  ✅ Found email in page source: {email_found}")
                                break
                    except:
                        pass
                
                if email_found:
                    print(f"✅ SUCCESS: {email_found}")
                    return [name, email_found, "Valid"]
                else:
                    print(f"❌ No email found for {name}")
                    
                    # Save debug info
                    try:
                        # Screenshot
                        screenshot_path = f"debug_{name.replace(' ', '_')}.png"
                        await page.screenshot(path=screenshot_path)
                        print(f"  📸 Screenshot saved: {screenshot_path}")
                        
                        # HTML
                        html_path = f"debug_{name.replace(' ', '_')}.html"
                        html_content = await page.content()
                        with open(html_path, 'w', encoding='utf-8') as f:
                            f.write(html_content)
                        print(f"  📄 HTML saved: {html_path}")
                    except:
                        pass
                    
                    return [name, "Not Found", "Not Found"]
                    
            except Exception as e:
                print(f"❌ ERROR for {name}: {str(e)}")
                return [name, "Error", str(e)]
                
            finally:
                await page.close()
                await browser.close()


async def run():
    results = []
    
    print("\n" + "="*50)
    print("Starting scraper...")
    print("="*50 + "\n")
    
    # Process URLs with concurrency limit
    semaphore = asyncio.Semaphore(2)  # Process 2 URLs at a time
    tasks = [scrape(url, semaphore) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out any exceptions
    processed_results = []
    for r in results:
        if isinstance(r, Exception):
            print(f"Task failed with error: {r}")
        else:
            processed_results.append(r)
    
    if processed_results:
        print(f"\nWriting {len(processed_results)} results to Google Sheet...")
        
        # Clear and update sheet
        try:
            sheet_output.clear()
            sheet_output.append_row(["Name", "Email", "Status"])
            sheet_output.append_rows(processed_results)
            print("✅ Results written to Google Sheet")
        except Exception as e:
            print(f"❌ Error writing to sheet: {str(e)}")
    
    print(f"\nFinished processing {len(processed_results)} URLs")
    
    # Print summary
    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)
    for result in processed_results:
        status_icon = "✅" if result[1] not in ["Not Found", "Error"] else "❌"
        print(f"{status_icon} {result[0]:20} -> {result[1]}")


if __name__ == "__main__":
    asyncio.run(run())
