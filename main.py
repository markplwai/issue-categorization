#! /usr/bin/env python3

# pylint: disable=C0114
# pylint: disable=W0718

### IMPORTS ###

# Standard
import asyncio
import base64
import datetime
import sys
import warnings

# Typing
from typing import Optional

# 3rd Party
from openai import OpenAI  # AI API
import openpyxl  # Excel
from playwright.async_api import async_playwright  # Web automation
from termcolor import colored  # Colored terminal output

# Suppress openpyxl warnings about data validation
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

### GLOBALS ###
CLIENT: OpenAI = OpenAI()
DEBUG = 1

### FUNCTIONS ###
# Issue loader
def load_issues(file_path: str) -> list[list]:
    """Load issues from an Excel file and return them as a list of lists."""
    try:
        workbook = openpyxl.load_workbook(file_path)
        if DEBUG:
            print(
                colored("[DEBUG]", "cyan"),
                f"Loaded workbook {file_path}\n        with sheets: {workbook.sheetnames}\n",
            )

        issues = []
        for sheet in workbook:
            for row in sheet.iter_rows(min_row=2, values_only=True):
                issue = row[:11]  # include col J (index 10, screenshot URLs)
                issues.append(issue)

        if DEBUG:
            print(colored("[DEBUG]", "cyan"), f"Loaded {len(issues)} issues.\n")
            print(colored("[DEBUG]", "cyan"), f"Sample issue: {issues[0]}\n")

        return issues

    except Exception as e:
        print(
            colored("[ERROR]", "red", attrs=["bold"]),
            f"Error loading issues from {file_path}: {e}\n"
        )
        sys.exit(1)

# Issue filter
def filter_issues(issues: list[list], age: int) -> list[list]:
    """Filter issues based on their age in days (keep those within `age` days)."""
    skipped_count = 0
    try:
        filtered_issues = []
        current_date = datetime.datetime.now()

        for issue in issues:
            if not issue[0]:
                skipped_count += 1
                continue
            try:
                created_at = issue[0]
                if isinstance(created_at, datetime.date) and not isinstance(
                    created_at, datetime.datetime
                ):
                    created_at = datetime.datetime.combine(
                        created_at, datetime.time.min
                    )
                issue_age = (current_date - created_at).days
                if issue_age <= age:
                    filtered_issues.append(issue)
            except Exception as e:
                print(
                    colored("[ERROR]", "red"),
                    f"Error processing issue {issue}: {e}\n"
                )
                continue

        if DEBUG:
            print(
                colored("[DEBUG]", "cyan"),
                f"Filtered issues: {len(filtered_issues)} "
                f"out of {len(issues)} (skipped {skipped_count})\n",
            )

        return filtered_issues

    except Exception as e:
        print(
            colored("[ERROR]", "red", attrs=["bold"]),
            f"Error filtering issues: {e}\n"
        )
        sys.exit(1)

# Screenshot URL extractor
def extract_urls(cell: Optional[str]) -> list[str]:
    """Split a col-J cell (may have newlines) into a list of URLs."""
    if not cell:
        return []
    return [u.strip() for u in str(cell).splitlines() if u.strip()]

# Screenshot taker
async def screenshot_urls(urls: list[str], concurrency: int = 20) -> list[str]:
    """
    Given a list of URLs, visit them in a shared browser and return
    base64-encoded full-page screenshots.
    """
    urls = [u.strip() for u in urls if u.strip()]
    results: list[Optional[str]] = [None] * len(urls)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        sem = asyncio.Semaphore(concurrency)

        async def grab(i: int, url: str):
            async with sem:
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                try:
                    await page.goto(url, wait_until="load", timeout=30000)
                    shot = await page.screenshot(full_page=True, type="jpeg")
                    results[i] = base64.b64encode(shot).decode("ascii")
                except Exception as e:
                    print(colored("[ERROR]", "red"), f"Screenshot failed for {url}: {e}")
                finally:
                    await page.close()

        await asyncio.gather(*(grab(i, u) for i, u in enumerate(urls)))
        await browser.close()

    return [r for r in results if r]

# Convert issues to API input format
async def issues_to_api_inputs(issues: list[list]) -> list[dict]:
    """
    Convert issues into Responses API input format.
    Each issue -> one user message with text + all screenshots.
    """
    messages: list[dict] = []

    for issue in issues:
        # Text block
        text = (
            f"Lesson: {issue[3]} | Content Type: {issue[4]} | "
            f"Issue Severity: {issue[7]} | Issue Desccription: {issue[8]}"
        )
        content_blocks: list[dict] = [{"type": "input_text", "text": text}]

        # Screenshots from col J
        urls = extract_urls(issue[9])
        if urls:
            b64s = await screenshot_urls(urls)
            for b in b64s:
                content_blocks.append(
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b}"}
                )

        messages.append({"role": "user", "content": content_blocks})

    return messages

# AI bucket-maker
def ai_make_buckets(issue_inputs: list[dict]) -> str:
    """Generate new, targeted buckets for issues using OpenAI's GPT-5 Mini."""
    try:
        if DEBUG:
            print(colored("[DEBUG]", "cyan"),
                  f"Sending {len(issue_inputs)} issues to bucket-maker.\n")

        system = (
            "You are an expert categorizer of content issues.\n"
            "Your task:\n"
            "1. Analyze the provided issues (text and screenshots).\n"
            "2. Identify common recurring types of issues.\n"
            "3. Create new issue categories ('buckets') with names + definitions.\n"
            "Guidelines:\n"
            "- Buckets should be clear and descriptive.\n"
            "- Mutually exclusive and collectively exhaustive.\n"
            "- Not too broad or too narrow.\n"
            "- Output JSON array with fields: name, definition.\n"
            "- Ignore irrelevant UI/toolbars in screenshots.\n"
        )

        inputs = [{"role": "system", "content": [{"type": "input_text", "text": system}]}]
        inputs.extend(issue_inputs)

        response = CLIENT.responses.create(
            model="gpt-5-mini",
            input=inputs,  # type: ignore
            text={"verbosity": "low"},
            reasoning={"effort": "low"},
        )

        if DEBUG:
            print(colored("[DEBUG]", "cyan"), f"Bucket-maker response: {response}\n")

        return response.output_text

    except Exception as e:
        print(
            colored("[ERROR]", "red", attrs=["bold"]),
            f"Error generating buckets: {e}\n"
        )
        sys.exit(1)

# Main function
def main():
    """Main function to load, filter, and process issues."""
    issues = load_issues("issues.xlsx")
    filtered_issues = filter_issues(issues, age=30)
    if not filtered_issues:
        print(colored("[WARNING]", "yellow", attrs=["bold"]), "All issues filtered out! Exiting.")
        return

    if DEBUG:
        print(colored("[DEBUG]", "cyan"), f"First filtered issue: {filtered_issues[0]}\n")
        print(colored("[DEBUG]", "cyan"), f"Total filtered issues: {len(filtered_issues)}\n")

    # Build API inputs (with screenshots)
    if DEBUG:
        inputs = asyncio.run(issues_to_api_inputs(filtered_issues[:10]))  # sample 10 for demo
        print(colored("[DEBUG]", "cyan"), f"First API input: {str(inputs[0])[:1000] + "..."}\n")
        print(colored("[DEBUG]", "cyan"), f"Total API inputs: {len(inputs)}\n")
    else:
        inputs = asyncio.run(issues_to_api_inputs(filtered_issues))

    if not inputs:
        print(colored("[WARNING]", "yellow", attrs=["bold"]),
              "No valid API inputs generated! Exiting.")
        return

    # Send to bucket-maker
    buckets = ai_make_buckets(inputs)
    if DEBUG:
        print(colored("[DEBUG]", "cyan"), f"Buckets: {buckets}\n")

# Entry point
if __name__ == "__main__":
    main()
