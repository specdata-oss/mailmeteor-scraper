async def scrape_page(context, url):

    name = extract_name(url)

    page = await context.new_page()

    try:
        await page.goto(url, timeout=60000)

        max_checks = 40
        delay = 0.5

        for _ in range(max_checks):

            text = await page.inner_text("body")

            email = extract_email(text)

            if email:
                return [name, email, "Valid"]

            if "No results found" in text or "couldn't find an email address" in text:
                return [name, "Not Found", "Not Found"]

            if "Searching" in text:
                await asyncio.sleep(delay)
                continue

            await asyncio.sleep(delay)

        return [name, "Not Found", "Timeout"]

    except Exception as e:
        return [name, "Error", str(e)]

    finally:
        await page.close()
