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


def extract_name(url):
    query = parse_qs(urlparse(url).query)
    name = query.get("name", ["Unknown"])[0]
    return name.replace("+", " ")


async def scrape(url, semaphore):
    async with semaphore:
        name = extract_name(url)
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ]
            )
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            page = await context.new_page()
            
            try:
                print(f"\n{'='*50}")
                print(f"Processing: {name}")
                
                # Navigate to page
                await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                
                # Wait for the email finder to load and find the email
                print("  Waiting for email finder to load...")
                
                # Wait for the result to appear (not the placeholder)
                email_found = None
                
                # Method 1: Wait for element that contains the real email
                try:
                    # Wait for the email to appear (this might take several seconds)
                    print("  Looking for real email (not placeholder)...")
                    
                    # Wait up to 30 seconds for the real email
                    for i in range(30):
                        # Check if the placeholder is still there or if we have a real email
                        page_text = await page.text_content('body')
                        
                        # Look for email pattern that's NOT the placeholder
                        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                        emails = re.findall(email_pattern, page_text)
                        
                        # Filter out the placeholder
                        real_emails = []
                        for email in emails:
                            # Skip the generic placeholder
                            if email != "firstname.lastname@company.com" and not email.endswith("@company.com"):
                                # Additional check to ensure it's a real email with proper domain
                                if '.' in email.split('@')[1] and len(email.split('@')[1].split('.')) >= 2:
                                    real_emails.append(email)
                        
                        if real_emails:
                            # Pick the most likely real email
                            for email in real_emails:
                                # Check if email contains parts of the name
                                name_parts = name.lower().split()
                                if any(part in email.lower() for part in name_parts if len(part) > 2):
                                    email_found = email
                                    print(f"  ✅ Found name-match email: {email_found}")
                                    break
                            
                            if not email_found and real_emails:
                                email_found = real_emails[0]
                                print(f"  ✅ Found real email: {email_found}")
                            
                            if email_found:
                                break
                        
                        # Also look for email in specific result elements
                        try:
                            # Look for elements that might contain the result
                            result_elements = await page.query_selector_all('div[class*="result"], div[class*="email"], .email-result')
                            for element in result_elements:
                                text = await element.text_content()
                                if text and '@' in text and text != "firstname.lastname@company.com":
                                    emails = re.findall(email_pattern, text)
                                    if emails and emails[0] != "firstname.lastname@company.com":
                                        email_found = emails[0]
                                        print(f"  ✅ Found email in result element: {email_found}")
                                        break
                            if email_found:
                                break
                        except:
                            pass
                        
                        # Progress indicator
                        if i % 5 == 0:
                            print(f"  Still waiting... ({i+1}/30)")
                        
                        await asyncio.sleep(1)
                        
                except Exception as e:
                    print(f"  ⚠️ Error while waiting for email: {str(e)}")
                
                # Method 2: If still not found, try to click any "Find Email" button if present
                if not email_found:
                    try:
                        # Look for and click any search/find button
                        buttons = await page.query_selector_all('button:has-text("Find"), button:has-text("Search"), button:has-text("Check")')
                        if buttons:
                            print("  Clicking search button...")
                            await buttons[0].click()
                            await asyncio.sleep(3)
                            
                            # Check again for email
                            page_text = await page.text_content('body')
                            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                            emails = re.findall(email_pattern, page_text)
                            real_emails = [e for e in emails if e != "firstname.lastname@company.com" and not e.endswith("@company.com")]
                            if real_emails:
                                email_found = real_emails[0]
                                print(f"  ✅ Found email after clicking: {email_found}")
                    except:
                        pass
                
                if email_found:
                    print(f"✅ SUCCESS: {email_found}")
                    return [name, email_found, "Valid"]
                else:
                    print(f"❌ No real email found for {name}")
                    
                    # Save debug info
                    try:
                        screenshot_path = f"debug_{name.replace(' ', '_')}.png"
                        await page.screenshot(path=screenshot_path)
                        print(f"  📸 Screenshot saved: {screenshot_path}")
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
    semaphore = asyncio.Semaphore(1)  # Process 1 URL at a time to be safe
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
            # Clear everything
            sheet_output.clear()
            
            # Add headers
            sheet_output.append_row(["Name", "Email", "Status"])
            
            # Add results
            for result in processed_results:
                sheet_output.append_row(result)
                
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
        email_display = result[1][:30] + "..." if len(result[1]) > 30 else result[1]
        print(f"{status_icon} {result[0]:20} -> {email_display}")


if __name__ == "__main__":
    asyncio.run(run())
