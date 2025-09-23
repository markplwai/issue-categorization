#! /usr/bin/env python3

# pylint: disable=C0114
# pylint: disable=W0718

import datetime
import warnings
import openpyxl
from termcolor import colored

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

DEBUG = 1

def load_issues(file_path: str) -> list[list]:
    """Load issues from an Excel file and return them as a list of dictionaries."""
    try:
        workbook = openpyxl.load_workbook(file_path)
        if DEBUG:
            print(colored("[DEBUG]", "cyan"),
                  f"Loaded workbook {file_path}\n        with sheets: {workbook.sheetnames}\n")

        issues = []
        for sheet in workbook:
            for row in sheet.iter_rows(min_row=2, values_only=True):
                issue = row[:10]
                issues.append(issue)

        if DEBUG:
            print(colored("[DEBUG]", "cyan"), f"Loaded {len(issues)} issues.\n")
            print(colored("[DEBUG]", "cyan"), f"Sample issue: {issues[0]}\n")

        return issues

    except Exception as e:
        print(colored("[ERROR]", "red", attrs=["bold"]),
              f"Error loading issues from {file_path}: {e}\n")
        return []

def filter_issues(issues: list[list], age: int) -> list[list]:
    """Filter issues based on their age in days."""
    if DEBUG:
        skipped_count = 0
    try:
        filtered_issues = []
        current_date = datetime.datetime.now()

        for issue in issues:
            if not issue[0]:
                if DEBUG:
                    skipped_count += 1  # type: ignore
                continue
            try:
                issue_age = (current_date - issue[0]).days  # type: ignore
                if issue_age >= age:
                    filtered_issues.append(issue)
            except Exception as e:
                print(colored("[ERROR]", "red", attrs=["bold"]),
                      f"Error processing issue {issue}: {e}\n")
                return []

        if DEBUG:
            print(colored("[DEBUG]", "cyan"),
                  f"Filtered issues: {len(filtered_issues)} "
                  f"out of {len(issues)} (skipped {skipped_count})\n")  # type: ignore

        return filtered_issues

    except Exception as e:
        print(colored("[ERROR]", "red", attrs=["bold"]),
              f"Error filtering issues: {e}\n")
        return []

def main():
    """Main function to load and filter issues."""
    issues = load_issues("issues.xlsx")
    filtered_issues = filter_issues(issues, age=30)
    print(filtered_issues[0])  # Print first filtered issue for verification
    print(f"Total filtered issues: {len(filtered_issues)}")

if __name__ == "__main__":
    main()
