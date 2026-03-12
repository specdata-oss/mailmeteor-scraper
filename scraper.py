async def scrape_page(context, url):

    name = extract_name(url)
    page = await context.new_page()

    try:

        print("Processing:", name)

        # Load page fully
        await page.goto(url, wait_until="networkidle", timeout=60000)

        for _ in range(MAX_CHECKS):

            body = await page.inner_text("body")

            email = extract_email(body)

            if email:
                print("FOUND:", email)
                return [name, email, "Valid"]

            if "No results found" in body:
                return [name, "Not Found", "Not Found"]

            if "Searching" in body:
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            await asyncio.sleep(CHECK_INTERVAL)

        return [name, "Not Found", "Timeout"]

    except Exception as e:

        print("Error:", e)
        return [name, "Error", str(e)]

    finally:
        await page.close()
