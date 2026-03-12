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
print("\nFirst 3 URLs:")
for i, url in enumerate(urls[:3]):
    print(f"{i+1}. {url}")


def extract_name(url):
    query = parse_qs(urlparse(url).query)
    name = query.get("name", ["Unknown"])[0]
    return name.replace("+", " ")


async def scrape(url, semaphore):
    async with semaphore:
        name = extract_name(url)
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,  # Set to False to see what's happening
                args=[
                    '--disable-blink-features=AutomationControlled',
                ]
            )
            
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            page = await context.new_page()
            
            try:
                print(f"\n{'='*60}")
                print(f"Processing: {name}")
                print(f"URL: {url}")
                
                # Navigate to page
                print("  Navigating to page...")
                response = await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                print(f"  Response status: {response.status if response else 'No response'}")
                
                # Wait a bit for initial load
                await asyncio.sleep(3)
                
                # Take screenshot of initial page
                screenshot_path = f"debug_1_initial_{name.replace(' ', '_')}.png"
                await page.screenshot(path=screenshot_path)
                print(f"  📸 Initial screenshot saved: {screenshot_path}")
                
                # Save page title
                title = await page.title()
                print(f"  Page title: {title}")
                
                # Check for any iframes (email finder might be in an iframe)
                frames = page.frames
                print(f"  Frames found: {len(frames)}")
                
                # Look for email in all frames
                email_found = None
                
                for frame_idx, frame in enumerate(frames):
                    try:
                        print(f"  Checking frame {frame_idx}...")
                        
                        # Get frame content
                        frame_content = await frame.content()
                        
                        # Look for email pattern in frame
                        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                        emails = re.findall(email_pattern, frame_content)
                        
                        if emails:
                            print(f"    Found emails in frame: {emails}")
                            
                            # Filter out placeholders
                            for email in emails:
                                if email != "firstname.lastname@company.com" and not email.endswith("@example.com"):
                                    # Check if it's a real email with proper domain
                                    if '.' in email.split('@')[1]:
                                        email_found = email
                                        print(f"    ✅ Found real email in frame: {email_found}")
                                        break
                        
                        # Also check visible text in frame
                        frame_text = await frame.evaluate('() => document.body?.innerText || ""')
                        if frame_text and '@' in frame_text:
                            text_emails = re.findall(email_pattern, frame_text)
                            for email in text_emails:
                                if email != "firstname.lastname@company.com" and not email.endswith("@example.com"):
                                    if '.' in email.split('@')[1]:
                                        email_found = email
                                        print(f"    ✅ Found email in frame text: {email_found}")
                                        break
                        
                        if email_found:
                            break
                            
                    except Exception as e:
                        print(f"    Error checking frame: {str(e)}")
                
                # If no email found yet, try to interact with the page
                if not email_found:
                    print("  No email found yet, trying to interact with page...")
                    
                    # Look for any input fields
                    inputs = await page.query_selector_all('input[type="text"], input[placeholder*="email"], input[placeholder*="name"]')
                    print(f"  Input fields found: {len(inputs)}")
                    
                    # Look for buttons
                    buttons = await page.query_selector_all('button')
                    print(f"  Buttons found: {len(buttons)}")
                    
                    # Try to click any "Find Email" or similar button
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
                                if email != "firstname.lastname@company.com" and not email.endswith("@example.com"):
                                    if '.' in email.split('@')[1]:
                                        email_found = email
                                        print(f"  ✅ Found email after click: {email_found}")
                                        break
                            
                            if email_found:
                                break
                
                # Final check of entire page
                if not email_found:
                    print("  Final check of entire page...")
                    
                    # Get full page content
                    page_content = await page.content()
                    
                    # Save HTML for debugging
                    html_path = f"debug_page_{name.replace(' ', '_')}.html"
                    with open(html_path, 'w', encoding='utf-8') as f:
                        f.write(page_content)
                    print(f"  📄 Full HTML saved: {html_path}")
                    
                    # Look for any email pattern
                    all_emails = re.findall(email_pattern, page_content)
                    print(f"  All emails found in page source: {all_emails}")
                    
                    # Filter for real emails
                    for email in all_emails:
                        if email != "firstname.lastname@company.com" and not email.endswith("@example.com"):
                            if '.' in email.split('@')[1]:
                                email_found = email
                                print(f"  ✅ Found email in page source: {email_found}")
                                break
                
                # Take final screenshot
                final_screenshot = f"debug_2_final_{name.replace(' ', '_')}.png"
                await page.screenshot(path=final_screenshot)
                print(f"  📸 Final screenshot saved: {final_screenshot}")
                
                if email_found:
                    print(f"✅ SUCCESS: {email_found}")
                    return [name, email_found, "Valid"]
                else:
                    print(f"❌ No real email found for {name}")
                    return [name, "Not Found", "Not Found"]
                    
            except Exception as e:
                print(f"❌ ERROR for {name}: {str(e)}")
                
                # Take error screenshot
                try:
                    error_screenshot = f"debug_error_{name.replace(' ', '_')}.png"
                    await page.screenshot(path=error_screenshot)
                    print(f"  📸 Error screenshot saved: {error_screenshot}")
                except:
                    pass
                    
                return [name, "Error", str(e)]
                
            finally:
                await page.close()
                await browser.close()


async def run():
    results = []
    
    print("\n" + "="*50)
    print("Starting scraper with DEBUG mode...")
    print("="*50 + "\n")
    
    # Process just ONE URL first for debugging
    if urls:
        print("🔍 DEBUG: Processing first URL only")
        semaphore = asyncio.Semaphore(1)
        result = await scrape(urls[0], semaphore)
        results.append(result)
    
    # If you want to process all, uncomment this:
    # semaphore = asyncio.Semaphore(1)
    # tasks = [scrape(url, semaphore) for url in urls]
    # results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out any exceptions
    processed_results = []
    for r in results:
        if isinstance(r, Exception):
            print(f"Task failed with error: {r}")
        else:
            processed_results.append(r)
    
    if processed_results:
        print(f"\nWriting {len(processed_results)} results to Google Sheet...")
        
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


if __name__ == "__main__":
    asyncio.run(run())
