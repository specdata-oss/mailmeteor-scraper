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


def extract_name(url):
    query = parse_qs(urlparse(url).query)
    name = query.get("name", ["Unknown"])[0]
    return name.replace("+", " ")


async def scrape(url):
    name = extract_name(url)
    
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled']
        )
        
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        page = await context.new_page()
        
        try:
            print(f"\n{'='*50}")
            print(f"Processing: {name}")
            
            # Navigate to page
            await page.goto(url, timeout=60000)
            
            # Wait for the page to load
            await page.wait_for_load_state('networkidle')
            
            # Look for iframes that might contain the email finder
            frames = page.frames
            print(f"  Found {len(frames)} frames")
            
            email_found = None
            
            # First, try to find and click any "Find email" button if present
            try:
                # Look for the main email finder interface
                find_button = await page.wait_for_selector('button:has-text("Find email"), button:has-text("Search"), button:has-text("Check")', timeout=5000)
                if find_button:
                    print("  Clicking find button...")
                    await find_button.click()
                    await asyncio.sleep(3)
            except:
                print("  No find button found, page might auto-search")
            
            # Wait for results (the email might appear in a specific element)
            print("  Waiting for email result...")
            
            # Method 1: Look for the email in a result element
            for attempt in range(20):  # Try for 20 seconds
                # Check all frames for email
                for frame in frames:
                    try:
                        # Look for elements that might contain the result
                        result_elements = await frame.query_selector_all('.email-result, .result, [class*="email"], [class*="result"], .finder-result, div:has-text("@")')
                        
                        for element in result_elements:
                            text = await element.text_content()
                            if text and '@' in text:
                                # Extract email from text
                                email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                                emails = re.findall(email_pattern, text)
                                
                                for email in emails:
                                    # Filter out CSS/JS files and placeholders
                                    if ('.css' not in email and '.js' not in email and 
                                        '.png' not in email and '.jpg' not in email and
                                        'bootstrap' not in email.lower() and
                                        not email.endswith(('.css', '.js', '.png', '.jpg', '.gif')) and
                                        email != 'firstname.lastname@company.com'):
                                        
                                        # Check if it's a real email (has proper domain)
                                        if '.' in email.split('@')[1] and len(email.split('@')[1].split('.')) >= 2:
                                            email_found = email
                                            print(f"  ✅ Found email: {email_found}")
                                            break
                            
                            if email_found:
                                break
                        
                        if email_found:
                            break
                            
                    except:
                        pass
                
                if email_found:
                    break
                    
                print(f"  Waiting... ({attempt+1}/20)")
                await asyncio.sleep(1)
            
            # Method 2: If still not found, look for email in the page URL or network responses
            if not email_found:
                print("  Checking network responses...")
                
                # Set up response capture
                responses = []
                
                def handle_response(response):
                    if 'email' in response.url.lower() or 'finder' in response.url.lower():
                        responses.append(response.url)
                
                page.on('response', handle_response)
                
                # Wait a bit for any API calls
                await asyncio.sleep(5)
                
                # Check if any response contains email data
                for response_url in responses:
                    print(f"  Found relevant endpoint: {response_url}")
            
            # Method 3: Check the page source for email patterns but filter out assets
            if not email_found:
                print("  Checking page source for emails...")
                content = await page.content()
                
                # Look for email pattern
                email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                all_emails = re.findall(email_pattern, content)
                
                # Filter out asset files
                valid_emails = []
                for email in all_emails:
                    # Skip if it looks like a file
                    if any(ext in email.lower() for ext in ['.css', '.js', '.png', '.jpg', '.gif', '.svg', '.ico', 'bootstrap']):
                        continue
                    
                    # Skip placeholders
                    if email == 'firstname.lastname@company.com' or email.endswith('@company.com'):
                        continue
                    
                    # Check if it has a valid domain structure
                    if '@' in email:
                        domain = email.split('@')[1]
                        if '.' in domain and len(domain.split('.')) >= 2 and len(email) < 50:
                            valid_emails.append(email)
                
                if valid_emails:
                    # Try to find an email that matches the person's name
                    name_parts = name.lower().split()
                    for email in valid_emails:
                        local_part = email.split('@')[0].lower()
                        # Check if any part of the name is in the email
                        if any(part in local_part for part in name_parts if len(part) > 2):
                            email_found = email
                            print(f"  ✅ Found name-match email: {email_found}")
                            break
                    
                    # If no name match, take the first valid email
                    if not email_found and valid_emails:
                        email_found = valid_emails[0]
                        print(f"  ✅ Found email in source: {email_found}")
            
            # Take screenshot for debugging if no email found
            if not email_found:
                screenshot_path = f"debug_{name.replace(' ', '_')}.png"
                await page.screenshot(path=screenshot_path)
                print(f"  📸 No email found, screenshot saved: {screenshot_path}")
                
                # Also save page content for debugging
                html_path = f"debug_{name.replace(' ', '_')}.html"
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(await page.content())
                print(f"  📄 HTML saved: {html_path}")
            
            if email_found:
                print(f"✅ SUCCESS: {email_found}")
                return [name, email_found, "Valid"]
            else:
                print(f"❌ No valid email found")
                return [name, "Not Found", "Not Found"]
            
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
            return [name, "Error", str(e)]
            
        finally:
            await page.close()
            await browser.close()


async def run():
    results = []
    
    print("\n" + "="*50)
    print("Starting Mailmeteor Email Scraper...")
    print("="*50 + "\n")
    
    # Process URLs one by one
    for i, url in enumerate(urls):
        print(f"\n--- Processing {i+1}/{len(urls)} ---")
        result = await scrape(url)
        results.append(result)
        
        # Add delay between requests
        await asyncio.sleep(3)
    
    if results:
        print(f"\nWriting {len(results)} results to Google Sheet...")
        
        # Update sheet
        try:
            # Clear existing data but keep headers
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
