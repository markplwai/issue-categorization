# Issue Categorization

This project provides a script to create new categories for recent content issues from the Athena platform, and to re-categorize existing issues based on these new categories.

## Features

- Fetch and process issues from repositories
- Create new categories using GPT-5 Mini
- Re-categorize existing issues based on new categories using GPT-5 Nano
- Export categorized results for further use

## Usage

1. Clone this repository.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   playwright install-deps
   playwright install
   ```
3. Run the main script or workflow to begin categorizing issues:
   ```bash
   ./main.py
   ```

Refer to the source code and comments for customization options.

## Warning

**Processing a large number of issues may take a significant amount of time. For very large repositories (thousands of issues), execution can take over an hour. Please plan accordingly and consider running the tool on a machine with sufficient resources.**

The reason for this is that the script does a few time-consuming things:
 - Images
   - For each issue, it takes the screenshot link(s) and actually gets the screenshot (using Python Playwright). This is done due to the fact that some issues might not have a good textual description, and the screenshot is often more informative.
   - Each image is also sent to the OpenAI API (using GPT-5 Nano) to get a textual description of the image. This is done to reduce context size. 

 - API Calls
   - Because of the nature of the task, category creation is done by sending _all_ the issues to GPT-5 Mini. This is a large context (in fact, it was crashing when raw screenshots were passed instead of summaries), and takes a while to process. 
   - Re-categorization is done issue-by-issue, sending each issue to GPT-5 Nano along with the new categories. This is done to ensure that each issue is categorized correctly, but it does mean that duration of this step scales with the number of issues.
