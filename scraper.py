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

# Get all URLs
urls = [u for u in sheet_input.col_values(1) if u and u.startswith("http")]

print("URLs found:", len(urls))
for i, url in enumerate(urls):
    print(f"{i+1}. {url}")


def extract_name(url):
    query = parse_qs(urlparse(url).query)
    name = query.get("name", ["Unknown"])[0]
    return name.replace("+", " ")


async def scrape_with_timeout(url, timeout_seconds=45):
    """Run scrape with a timeout"""
    try:
        return await asyncio.wait_for(scrape(url), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        name = extract_name(url)
        print(f"⏰ Timeout for {name} after {timeout_seconds} seconds")
        return [name, "Error", f"Timeout {timeout_seconds}ms exceeded"]


async def scrape(url):
    name = extract_name(url)
    
    async with async_playwright() as p:
        # Launch browser with stealth settings
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--start-maximized',
            ]
        )
        
        # Create a realistic browser context
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            extra_http_headers={
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Referer': 'https://www.google.com/',
            }
        )
        
        page = await context.new_page()
        
        try:
            print(f"\n{'='*60}")
            print(f"Processing: {name}")
            print(f"URL: {url}")
            
            # Navigate with shorter timeout and better error handling
            try:
                print("  Loading page...")
                response = await page.goto(url, timeout=30000, wait_until='domcontentloaded')
                print(f"  Response status: {response.status if response else 'No response'}")
            except Exception as e:
                print(f"  ⚠️ Navigation warning: {str(e)}")
            
            # Wait a bit for initial content
            await asyncio.sleep(2)
            
            # Check if we got a valid page
            page_title = await page.title()
            print(f"  Page title: {page_title}")
            
            # Check if we're being blocked or redirected
            current_url = page.url
            print(f"  Current URL: {current_url}")
            
            # Take screenshot of initial page
            screenshot_path = f"debug_1_initial_{name.replace(' ', '_')}.png"
            await page.screenshot(path=screenshot_path)
            print(f"  📸 Initial screenshot saved")
            
            # Look for email in the page
            email_found = None
            
            # Method 1: Check if there's an iframe and look inside it
            frames = page.frames
            print(f"  Found {len(frames)} frames")
            
            for frame_idx, frame in enumerate(frames):
                try:
                    # Try to find email in frame
                    frame_text = await frame.evaluate('() => document.body?.innerText || ""')
                    
                    # Look for email pattern
                    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                    emails = re.findall(email_pattern, frame_text)
                    
                    # Filter valid emails
                    valid_emails = []
                    for email in emails:
                        # Skip obvious non-emails
                        if any(skip in email.lower() for skip in ['.css', '.js', '.png', '.jpg', '.gif', '.svg', 'bootstrap', 'jquery']):
                            continue
                        
                        # Skip placeholders
                        if email == 'firstname.lastname@company.com' or email.endswith('@company.com') or email.endswith('@example.com'):
                            continue
                        
                        # Check if it has valid domain
                        if '@' in email:
                            domain = email.split('@')[1]
                            if '.' in domain and len(domain.split('.')) >= 2 and len(email) < 50:
                                valid_emails.append(email)
                    
                    if valid_emails:
                        print(f"  Found potential emails in frame {frame_idx}: {valid_emails}")
                        
                        # Try to find email matching the name
                        name_parts = name.lower().split()
                        for email in valid_emails:
                            local_part = email.split('@')[0].lower()
                            if any(part in local_part for part in name_parts if len(part) > 2):
                                email_found = email
                                print(f"  ✅ Found name-match email in frame: {email_found}")
                                break
                        
                        # If no name match, take first valid email
                        if not email_found and valid_emails:
                            email_found = valid_emails[0]
                            print(f"  ✅ Found email in frame: {email_found}")
                except Exception as e:
                    print(f"  Error checking frame {frame_idx}: {str(e)[:50]}")
                
                if email_found:
                    break
            
            # Method 2: Check if there's a specific email result element
            if not email_found:
                print("  Looking for email result element...")
                
                # Common selectors for email results
                selectors = [
                    'div[class*="result"]',
                    'div[class*="email"]',
                    'span[class*="email"]',
                    '.email-address',
                    '.finder-result',
                    '.email-result',
                    'div:has-text("@")',
                    'p:has-text("@")',
                ]
                
                for selector in selectors:
                    try:
                        elements = await page.query_selector_all(selector)
                        for element in elements:
                            text = await element.text_content()
                            if text and '@' in text:
                                emails = re.findall(email_pattern, text)
                                for email in emails:
                                    if '.' in email.split('@')[1] and not any(skip in email.lower() for skip in ['.css', '.js', 'bootstrap']):
                                        email_found = email
                                        print(f"  ✅ Found email with selector '{selector}': {email_found}")
                                        break
                            if email_found:
                                break
                    except:
                        continue
                    
                    if email_found:
                        break
            
            # Method 3: Try to interact with the page
            if not email_found:
                print("  Trying to interact with page...")
                
                try:
                    # Look for any search/find buttons
                    buttons = await page.query_selector_all('button')
                    for button in buttons:
                        button_text = await button.text_content()
                        if button_text and any(word in button_text.lower() for word in ['find', 'search', 'check', 'get', 'email']):
                            print(f"  Clicking button: {button_text}")
                            await button.click()
                            await asyncio.sleep(3)
                            
                            # Check for email after click
                            page_text = await page.text_content('body')
                            emails = re.findall(email_pattern, page_text)
                            for email in emails:
                                if '.' in email.split('@')[1] and not any(skip in email.lower() for skip in ['.css', '.js', 'bootstrap']):
                                    email_found = email
                                    print(f"  ✅ Found email after click: {email_found}")
                                    break
                            break
                except Exception as e:
                    print(f"  Error interacting: {str(e)[:50]}")
            
            # Final check of page source
            if not email_found:
                print("  Checking full page source...")
                content = await page.content()
                
                # Save HTML for debugging
                html_path = f"debug_page_{name.replace(' ', '_')}.html"
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"  📄 HTML saved")
                
                # Look for email pattern in page source
                all_emails = re.findall(email_pattern, content)
                print(f"  Found {len(all_emails)} email-like strings in source")
                
                # Filter valid emails
                valid_emails = []
                for email in all_emails:
                    if not any(skip in email.lower() for skip in ['.css', '.js', '.png', '.jpg', '.gif', 'bootstrap', 'jquery', 'font']):
                        if '@' in email and '.' in email.split('@')[1]:
                            valid_emails.append(email)
                
                if valid_emails:
                    print(f"  Valid emails found: {valid_emails}")
                    
                    # Try to find email matching the name
                    name_parts = name.lower().split()
                    for email in valid_emails:
                        local_part = email.split('@')[0].lower()
                        if any(part in local_part for part in name_parts if len(part) > 2):
                            email_found = email
                            print(f"  ✅ Found name-match email in source: {email_found}")
                            break
                    
                    if not email_found and valid_emails:
                        email_found = valid_emails[0]
                        print(f"  ✅ Found email in source: {email_found}")
            
            # Take final screenshot
            final_screenshot = f"debug_2_final_{name.replace(' ', '_')}.png"
            await page.screenshot(path=final_screenshot)
            print(f"  📸 Final screenshot saved")
            
            if email_found:
                print(f"✅ SUCCESS: {email_found}")
                return [name, email_found, "Valid"]
            else:
                print(f"❌ No valid email found for {name}")
                
                # Check if the page has the email finder but it's not working
                if "email" in page_title.lower() or "finder" in page_title.lower():
                    print("  ⚠️ Page is an email finder but no email found")
                
                return [name, "Not Found", "Not Found"]
            
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
            
            # Take error screenshot
            try:
                error_screenshot = f"debug_error_{name.replace(' ', '_')}.png"
                await page.screenshot(path=error_screenshot)
                print(f"  📸 Error screenshot saved")
            except:
                pass
                
            return [name, "Error", str(e)]
            
        finally:
            await page.close()
            await browser.close()


async def run():
    results = []
    
    print("\n" + "="*50)
    print("Starting Mailmeteor Email Scraper...")
    print("="*50 + "\n")
    
    # Process URLs one by one with timeout
    for i, url in enumerate(urls):
        print(f"\n--- Processing {i+1}/{len(urls)} ---")
        result = await scrape_with_timeout(url, timeout_seconds=45)
        results.append(result)
        
        # Add delay between requests
        await asyncio.sleep(5)
    
    if results:
        print(f"\nWriting {len(results)} results to Google Sheet...")
        
        try:
            # Clear and update sheet
            sheet_output.clear()
            sheet_output.append_row(["Name", "Email", "Status"])
            sheet_output.append_rows(results)
            print("✅ Results written to Google Sheet")
        except Exception as e:
            print(f"❌ Error writing to sheet: {str(e)}")
    
    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)
    for result in results:
        status_icon = "✅" if result[1] not in ["Not Found", "Error"] else "❌"
        print(f"{status_icon} {result[0]}: {result[1]}")


if __name__ == "__main__":
    asyncio.run(run())
