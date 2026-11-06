"""Hands-on browser pass against a running dev environment (python scripts/dev.py), as each role of the 'demo' company.

    python scripts/acceptance_browser.py [--shots DIR] [--approve-first]

Logins come from the git-ignored file .dev-credentials. For every role it signs in, opens every page the menu offers on a
laptop and (for admin and finance) on a phone, and records: page errors, console errors, failed requests, sideways scroll,
and how long each page took to show its content. --approve-first has the finance user approve the first pending proposal.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://localhost:5173"
LAPTOP = {"width": 1366, "height": 768}
PHONE = {"width": 390, "height": 844}


def credentials() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in (ROOT / ".dev-credentials").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            email, password = line.split()
            out[email.split("@")[0]] = (email, password)
    return out


def watch(page: Page) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {"page_errors": [], "console_errors": [], "failed_requests": []}
    page.on("pageerror", lambda e: seen["page_errors"].append(str(e)[:200]))
    page.on("console", lambda m: seen["console_errors"].append(m.text[:200]) if m.type == "error" else None)
    page.on(
        "response",
        lambda r: (
            seen["failed_requests"].append(f"{r.status} {r.request.method} {r.url.split('localhost:5173')[-1]}")
            if r.status >= 400 and "/api/auth/login" not in r.url
            else None
        ),
    )
    return seen


def sign_in(page: Page, email: str, password: str) -> float:
    started = time.time()
    page.goto(f"{URL}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.get_by_text(re.compile(r"Welcome, \w+ Demo")).wait_for(timeout=20000)  # the name arrives with the profile
    return time.time() - started


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="")
    ap.add_argument("--approve-first", action="store_true")
    args = ap.parse_args()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    creds = credentials()
    report: list[dict] = []
    problems = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for role, (email, password) in creds.items():
            for size_name, size in (("laptop", LAPTOP), ("phone", PHONE)):
                if size_name == "phone" and role not in ("admin", "finance", "requester"):
                    continue
                page = browser.new_context(viewport=size).new_page()
                page.set_default_timeout(20000)
                seen = watch(page)
                row: dict = {"role": role, "size": size_name, "pages": {}}
                try:
                    row["login_seconds"] = round(sign_in(page, email, password), 2)
                    if size_name == "phone":
                        page.get_by_role("button", name="Menu").click()
                    links = page.get_by_label("Main").get_by_role("link")
                    hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
                    if size_name == "phone":
                        page.get_by_role("button", name="Menu").click()
                    row["menu"] = [h for h in hrefs if h]
                    for href in row["menu"]:
                        t = time.time()
                        page.goto(f"{URL}{href}")
                        page.get_by_role("heading").first.wait_for()
                        page.wait_for_load_state("networkidle")
                        sideways = not page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        row["pages"][href] = {"seconds": round(time.time() - t, 2), "sideways_scroll": sideways}
                        if shots:
                            page.screenshot(
                                path=str(shots / f"{role}_{size_name}{href.replace('/', '_') or '_home'}.png")
                            )
                    if role == "finance" and size_name == "laptop" and args.approve_first:
                        page.goto(f"{URL}/approvals")
                        page.get_by_text("Release payment for INV-9").wait_for()
                        page.get_by_label("Note (optional)").fill("checked against the invoice")
                        page.get_by_role("button", name="Approve", exact=True).click()
                        page.get_by_text("Nothing is waiting for you.").wait_for()
                        row["approved_first"] = True
                except Exception as exc:  # noqa: BLE001 - the report must say what failed
                    row["exception"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                row.update(seen)
                bad = (
                    row.get("exception")
                    or seen["page_errors"]
                    or seen["console_errors"]
                    or seen["failed_requests"]
                    or any(v["sideways_scroll"] for v in row["pages"].values())
                )
                problems += bool(bad)
                report.append(row)
                page.context.close()
        browser.close()
    print(json.dumps(report, indent=1))
    print(f"\nroles/sizes checked: {len(report)}; with problems: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
