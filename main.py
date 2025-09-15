#!/usr/bin/env python3
"""
Bucket Issues (Local Python) — CSV output, OpenAI SDK
- Reads your Excel workbook (sheets: Grade 3..8 by default)
- Extracts fields E & G–J for each row
- Opens the Screenshot Link (col J) with Playwright (full-page), captures a PNG in-memory
- Sends text (E,G,H,I,J) + screenshot (as a data URL) to OpenAI Responses API with a strict JSON schema
- Writes a CSV copying A–K and appending L=Bucket, M=Rationale
- Prints per-sheet and global stats
"""

import asyncio
import base64
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from openai import AsyncOpenAI, APIStatusError  # << SDK
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from playwright.async_api import async_playwright

# --------------------------- CONFIG --------------------------- #
MODEL = os.environ.get("OAI_MODEL", "gpt-4o-mini")  # safer default than 'gpt-5-mini'
TEMPERATURE = float(os.environ.get("OAI_TEMPERATURE", "0"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")  # leave empty for api.openai.com

DEBUG = int(os.environ.get("DEBUG", "1"))  # 1 = on, 0 = off

BUCKETS = [
    "LaTeX / Equation Issues",
    "Image/Diagram Formatting",
    "Text Styling Issues",
    "Layout Issues",
    "Incomplete Explanation",
    "Confusing Wording",
    "Weak Distractors",
    "Conceptual Misalignment",
    "Math Error",
    "Conceptual Error",
    "Mis-keyed Answer",
    "Exact Duplicate",
    "Near-Duplicate",
    "Missing Content",
    "Metadata/Navigation Error",
    "Uncategorized",
]

VIEWPORT = {"width": 1280, "height": 800, "scale": 2}
NAV_TIMEOUT_MS = int(os.environ.get("NAV_TIMEOUT_MS", "20000"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "4"))

# Column headers (exact match)
HDR_CONTENT_TYPE = "Content Type"  # E
HDR_ISSUE_TYPE   = "Issue Type"    # G
HDR_SEVERITY     = "Severity"      # H (fallback to Status)
HDR_STATUS       = "Status"        # alternative to Severity
HDR_DESCRIPTION  = "Description"   # I
HDR_SCREENSHOT   = "Screenshot"    # J (contains hyperlink or text)

# Output headers (copy A..K, then L, M)
OUT_HEADERS = [
    "Timestamp","Grade","Unit","Lesson","Content Type","Content Item ID (Optional)",
    "Issue Type","Status","Description","Assigned To","Resolution",
    "Bucket","Rationale"
]

# --------------------------- DATA --------------------------- #
@dataclass
class RowData:
    sheet: str
    row_idx: int
    a_to_k: List[Optional[str]]  # 11 values
    content_type: str
    issue_type: str
    severity_or_status: str
    description: str
    screenshot_url: str

@dataclass
class ClassifyResult:
    bucket: str
    confidence: float
    rationale: str

# --------------------------- EXCEL PARSING --------------------------- #
def _header_map(ws: Worksheet) -> Dict[str, int]:
    headers = {}
    for c, cell in enumerate(ws[1], start=1):
        v = str(cell.value).strip() if cell.value is not None else ""
        if v:
            headers[v] = c
    return headers

# add this helper near the other helpers
def build_response_format():
    properties = {
        "bucket": {"type": "string", "enum": BUCKETS},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
    }
    # strict mode: some deployments require ALL keys to be listed under "required"
    required = list(properties.keys())
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "issue_bucket",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }

def iter_rows_from_sheet(ws: Worksheet) -> List[RowData]:
    hdr = _header_map(ws)
    if not hdr:
        if DEBUG: print(f"[DEBUG] Sheet '{ws.title}': no header row?")
        return []

    missing_keys = [k for k in [HDR_CONTENT_TYPE, HDR_ISSUE_TYPE, HDR_DESCRIPTION] if k not in hdr]
    if missing_keys:
        print(f"[WARN] Sheet '{ws.title}' missing headers: {missing_keys}")

    sev_key = HDR_SEVERITY if HDR_SEVERITY in hdr else (HDR_STATUS if HDR_STATUS in hdr else None)

    rows: List[RowData] = []
    max_col = min(ws.max_column, 11)  # A..K

    # Per-sheet debug cap: rows 2..21
    max_row = ws.max_row + 1

    for r in range(2, max_row):
        a_to_k = [ws.cell(row=r, column=c).value for c in range(1, max_col + 1)]

        content_type = str(ws.cell(row=r, column=hdr.get(HDR_CONTENT_TYPE, 0)).value or "").strip()
        issue_type   = str(ws.cell(row=r, column=hdr.get(HDR_ISSUE_TYPE, 0)).value or "").strip()
        description  = str(ws.cell(row=r, column=hdr.get(HDR_DESCRIPTION, 0)).value or "").strip()
        severity     = str(ws.cell(row=r, column=hdr.get(sev_key, 0)).value or "").strip() if sev_key else ""

        screenshot_url = ""
        if HDR_SCREENSHOT in hdr:
            j_col = hdr[HDR_SCREENSHOT]
            cell = ws.cell(row=r, column=j_col)
            if cell.hyperlink and cell.hyperlink.target:
                screenshot_url = str(cell.hyperlink.target).strip()
            else:
                screenshot_url = str(cell.value or "").strip()

        rows.append(RowData(
            sheet=ws.title,
            row_idx=r,
            a_to_k=a_to_k + ([None] * (11 - len(a_to_k))),
            content_type=content_type,
            issue_type=issue_type,
            severity_or_status=severity,
            description=description,
            screenshot_url=screenshot_url,
        ))
    if DEBUG: print(f"[DEBUG] '{ws.title}': queued {len(rows)} rows.")
    return rows

# --------------------------- PLAYWRIGHT --------------------------- #
async def screenshot_data_url(context, url: str) -> Optional[str]:
    if not url:
        return None
    try:
        page = await context.new_page()
        await page.route("**/*", lambda route: route.abort() if _is_tracker(route.request().url) else route.continue_())
        if DEBUG: print(f"[DEBUG] nav -> {url}")
        await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
        await page.add_style_tag(content="*{animation:none!important;transition:none!important}")
        png_bytes = await page.screenshot(type="png", full_page=True)
        await page.close()
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        print(f"[screenshot] failed for {url}: {e}")
        return None

def _is_tracker(u: str) -> bool:
    return any(s in u for s in ["googletagmanager", "doubleclick", "facebook", "optimizely", "hotjar"])

# --------------------------- OPENAI (SDK) --------------------------- #
def build_parts(row: RowData, image_data_url: Optional[str]) -> List[dict]:
    text = "\n".join([
        "You classify quality issues in math curriculum content.",
        "Pick exactly one bucket from the enum list provided by the tool.",
        "Be conservative: if ambiguous, choose 'Uncategorized'.",
        "",
        f"Content Type (E): {row.content_type or '(missing)'}",
        f"Issue Type (G): {row.issue_type or '(missing)'}",
        f"Severity/Status (H): {row.severity_or_status or '(missing)'}",
        f"Description (I): {row.description or '(missing)'}",
        f"Screenshot Link (J): {row.screenshot_url or '(missing)'}",
    ])
    parts = [{"type": "input_text", "text": text}]
    if image_data_url:
        parts.append({"type": "input_image", "image_url": image_data_url, "detail": "low"})
    return parts

async def classify_issue(client: AsyncOpenAI, parts: list) -> ClassifyResult:
    # convert your generic parts -> chat message content
    msg_content = []
    for p in parts:
        if p.get("type") == "input_text":
            msg_content.append({"type": "text", "text": p["text"]})
        elif p.get("type") == "input_image":
            msg_content.append({
                "type": "image_url",
                "image_url": {"url": p["image_url"], "detail": p.get("detail", "low")}
            })

    response_format = build_response_format()

    try:
        resp = await client.chat.completions.create(
            model=MODEL,                 # e.g., "gpt-4o-mini" or "gpt-4o-2024-08-06"
            temperature=TEMPERATURE,
            response_format=response_format,
            messages=[
                {
                    "role": "system",
                    "content": "You classify quality issues in math curriculum content. "
                               "Choose exactly one bucket from the provided enum. "
                               "If ambiguous, choose 'Uncategorized'."
                },
                {"role": "user", "content": msg_content},
            ],
        )

        msg = resp.choices[0].message
        bucket, conf, rationale = "Uncategorized", 0.0, ""

        # Prefer typed .parsed if your SDK exposes it
        if hasattr(msg, "parsed") and msg.parsed:
            j = msg.parsed
        else:
            # otherwise content is a JSON string
            content = msg.content
            if isinstance(content, str):
                j = json.loads(content)
            else:
                # some SDKs return a list of parts; look for text
                j = None
                for part in (content or []):
                    if isinstance(part, dict) and part.get("type") in ("text", "output_text") and part.get("text"):
                        try:
                            j = json.loads(part["text"])
                            break
                        except Exception:
                            continue

        if j:
            bucket = j.get("bucket", bucket)
            conf = float(j.get("confidence", conf))
            rationale = j.get("rationale", rationale)

        return ClassifyResult(
            bucket=bucket if bucket in BUCKETS else "Uncategorized",
            confidence=conf,
            rationale=rationale
        )

    except Exception as e:
        if DEBUG:
            print(f"[DEBUG] chat.completions.create error: {e}")
        return ClassifyResult(bucket="Uncategorized", confidence=0.0, rationale=f"error: {e}")


# --------------------------- PIPELINE --------------------------- #
async def process_workbook(input_path: str, output_path: str, sheet_names: List[str]):
    if not OPENAI_API_KEY:
        print("ERROR: Please set OPENAI_API_KEY in environment.")
        sys.exit(2)

    client_kwargs = {}
    if OPENAI_BASE_URL:
        client_kwargs["base_url"] = OPENAI_BASE_URL  # optional override
    client = AsyncOpenAI(api_key=OPENAI_API_KEY, **client_kwargs)

    in_path = Path(input_path).resolve()
    out_path = Path(output_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("CWD:   ", Path().resolve())
    print("Input: ", in_path)
    print("Output:", out_path)
    print("Model: ", MODEL)

    wb = load_workbook(in_path, data_only=True)
    all_rows: List[RowData] = []
    for name in sheet_names:
        if name not in wb.sheetnames:
            print(f"[WARN] Sheet '{name}' not found; skipping.")
            continue
        ws = wb[name]
        rows = iter_rows_from_sheet(ws)
        all_rows.extend(rows)
    if not all_rows:
        print("No rows found to process.")
        await client.close()
        return

    counts_global: Dict[str, int] = {b: 0 for b in BUCKETS}
    counts_by_sheet: Dict[str, Dict[str, int]] = {}
    output_rows: List[List[Optional[str]]] = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            viewport={"width": VIEWPORT["width"], "height": VIEWPORT["height"]},
            device_scale_factor=VIEWPORT["scale"],
            timezone_id="America/New_York",
            locale="en-US",
            color_scheme="light",
            reduced_motion="reduce",
        )

        async def handle_row(row: RowData):
            async with sem:
                if DEBUG:
                    print(f"[DEBUG] → {row.sheet} R{row.row_idx} | G='{row.issue_type}' | H='{row.severity_or_status}'")

                img = await screenshot_data_url(context, row.screenshot_url) if row.screenshot_url else None
                parts = build_parts(row, img)
                result = await classify_issue(client, parts)

                bucket = result.bucket if result.bucket in BUCKETS else "Uncategorized"
                counts_global[bucket] += 1
                counts_by_sheet.setdefault(row.sheet, {b: 0 for b in BUCKETS})
                counts_by_sheet[row.sheet][bucket] += 1

                out_row = list(row.a_to_k)
                if len(out_row) < 11:
                    out_row.extend([None] * (11 - len(out_row)))
                out_row.append(bucket)
                out_row.append(result.rationale or "")
                output_rows.append(out_row)

                if DEBUG and (result.rationale or bucket == "Uncategorized"):
                    print(f"[DEBUG]   bucket={bucket} conf={result.confidence:.2f} rationale={result.rationale[:180]}")

        for row in all_rows:
            await handle_row(row)

        await context.close()
        await browser.close()

    # ---- Write CSV ----
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(OUT_HEADERS)
        for row in output_rows:
            w.writerow(["" if v is None else v for v in row])

    print(f"\nSaved results to: {out_path}")

    # Stats
    print("\n=== PER-SHEET STATS ===")
    for sheet, counts in counts_by_sheet.items():
        print(f"\n[{sheet}]")
        for b in sorted(counts, key=lambda k: counts[k], reverse=True):
            if counts[b]:
                print(f"  {b}: {counts[b]}")

    print("\n=== GLOBAL TOTALS ===")
    for b in sorted(counts_global, key=lambda k: counts_global[k], reverse=True):
        if counts_global[b]:
            print(f"  {b}: {counts_global[b]}")

    await client.close()

# --------------------------- CLI --------------------------- #
def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Bucket issues with Playwright + OpenAI (CSV output, SDK)")
    p.add_argument("--input", required=True, help="Path to input Excel (.xlsx)")
    p.add_argument("--output", default="issue_buckets_results.csv", help="Path to output CSV")
    p.add_argument("--sheets", default="Grade 3,Grade 4,Grade 5,Grade 6,Grade 7,Grade 8",
                   help="Comma-separated list of sheet names")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    sheets = [s.strip() for s in args.sheets.split(",") if s.strip()]
    asyncio.run(process_workbook(args.input, args.output, sheets))
