"""What the scripted demo runs (E30) are built from: the web app served inside this process, a browser per person, the
worker run by hand between steps, and a transcript with a screenshot of every step.

Serving the app in this process (a thread) rather than as a separate program means the evals' fixed date, the fake mail
box and the throw-away databases all apply to what the browser sees.
"""

import os
import re
import shutil
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self

import httpx
import lib

PORT = int(os.environ.get("P2P_DEMO_PORT", "18290"))
URL = f"http://127.0.0.1:{PORT}"
RESULTS = lib.ROOT / "evals" / "results" / "demo_runs"
LAPTOP = {"width": 1280, "height": 760}
FLOW = {  # the plan's 13 steps (planning/P2P-13-Step-Plan.md)
    1: "Demand intake",
    2: "Stock check",
    3: "Requisition",
    4: "Approval",
    5: "Buying route",
    6: "Quotes and supplier selection",
    7: "Purchase order",
    8: "Confirmation",
    9: "Goods receipt",
    10: "Invoice",
    11: "Three-way match",
    12: "Payment",
    13: "Reconcile and close",
}


class Site:
    """The whole web app (API, main interface and admin console) on a local port, for the length of the with block."""

    def __enter__(self) -> Self:
        import uvicorn
        from p2p.main import app

        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                if httpx.get(f"{URL}/api/health", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.3)
        raise RuntimeError("the web app did not start")

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


def slug(text_: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text_.lower()).strip("-")[:48]


def squash(text_: str, limit: int = 260) -> str:
    one = re.sub(r"\s+", " ", text_ or "").strip()
    return one if len(one) <= limit else one[: limit - 1] + "…"


class Demo:
    """One run: who is signed in, what they did, what they saw, and what the browser complained about."""

    def __init__(self, name: str, title: str, world: Any, outbox: Any, browser: Any) -> None:
        self.name, self.title, self.w, self.db, self.outbox, self.browser = (
            name,
            title,
            world,
            world.db,
            outbox,
            browser,
        )
        self.contexts: dict[str, Any] = {}
        self.pages: dict[str, Any] = {}
        self.names: dict[str, str] = {}  # key -> "Prakash Gowda (Head - Stores & Logistics)"
        self.steps: list[dict[str, str]] = []
        self.flow: dict[int, int] = {}  # plan step -> transcript step that shows it
        self.kept: list[tuple[int, int, str]] = []  # more pictures of a plan step: (plan step, transcript step, what)
        self.problems: list[str] = []
        self.allowed: list[str] = []  # responses the script expects to be refused, while it checks a refusal
        self.folder = RESULTS / name
        self.folder.mkdir(parents=True, exist_ok=True)
        for old in self.folder.glob("*.jpg"):
            old.unlink()

    # ---- people ---------------------------------------------------------------------------------

    def sign_in(self, key: str, email: str, who: str) -> Any:
        """A person opens the app in their own browser window and signs in on the login screen."""
        from playwright.sync_api import expect

        ctx = self.browser.new_context(viewport=LAPTOP)
        ctx.add_init_script("localStorage.setItem('procureos_launch_focus_mode', 'false')")
        page = ctx.new_page()
        page.set_default_timeout(30_000)
        page.on("pageerror", lambda e, k=key: self.problems.append(f"{k}: page error: {e}"))
        page.on(
            "console",
            lambda m, k=key: self.problems.append(f"{k}: console error: {m.text[:140]}") if m.type == "error" else None,
        )
        page.on("response", lambda r, k=key: self._response(k, r))
        page.goto(f"{URL}/")
        page.locator("#loginEmail").fill(email)
        page.locator("#loginPassword").fill(lib.PASSWORD)
        page.locator("#loginButton").click()
        expect(page.locator(".user-badge .who")).not_to_have_text(" ")
        expect(page.locator("#loginOverlay")).to_be_hidden()
        self.contexts[key], self.pages[key], self.names[key] = ctx, page, who
        return page

    def _response(self, key: str, r: Any) -> None:
        if r.status < 400 or "/api/auth/" in r.url:
            return
        line = f"{key}: {r.status} {r.request.method} {r.url.replace(URL, '')[:80]}"
        if any(a in line for a in self.allowed):
            return
        self.problems.append(line)

    def go(self, key: str, route: str) -> Any:
        """Open a screen of the main interface (a route such as 'purchase-orders/PO-1'). A person who is already on that
        screen presses reload, which is what shows them what changed since they last looked."""
        page = self.pages[key]
        target = f"{URL}/#/{route}"
        if page.url == target:
            page.reload()
        else:
            page.goto(target)
        page.wait_for_load_state("networkidle")
        return page

    def go_admin(self, key: str, route: str) -> Any:
        page = self.pages[key]
        page.goto(f"{URL}/admin/{route}")
        page.wait_for_load_state("networkidle")
        return page

    # ---- the transcript -------------------------------------------------------------------------

    def seen(self, page: Any) -> str:
        for sel in ("#appContent", "main"):
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                return squash(loc.first.inner_text())
        return squash(page.locator("body").inner_text())

    @contextmanager
    def step(self, key: str, what: str) -> Iterator[Any]:
        """One thing a person does. Afterwards: what the screen shows and a picture of it."""
        page = self.pages[key]
        started = time.monotonic()
        failed = None
        try:
            yield page
        except BaseException as exc:
            failed = exc
            raise
        finally:
            n = len(self.steps) + 1
            shot = f"{n:02d}-{key}-{slug(what)}.jpg"
            try:
                page.wait_for_timeout(150)
                page.screenshot(path=str(self.folder / shot), type="jpeg", quality=62)
                shown = self.seen(page)
            except Exception:  # noqa: BLE001 - a page that is already gone still gets its line in the transcript
                shot, shown = "", "(the page could not be read)"
            self.steps.append(
                {
                    "n": str(n),
                    "who": f"{self.names.get(key, key)}",
                    "did": what,
                    "saw": shown,
                    "shot": shot,
                    "seconds": f"{time.monotonic() - started:.1f}",
                    "failed": "" if failed is None else f"{type(failed).__name__}: {squash(str(failed), 300)}",
                }
            )

    # ---- the plan's 13 steps ----------------------------------------------------------------------

    def mark(self, flow_step: int) -> None:
        """The step just taken shows this step of the plan's flow (the last one marked for a step is its picture)."""
        self.flow[flow_step] = len(self.steps)

    def keep(self, flow_step: int, what: str) -> None:
        """The step just taken shows one more screen of this step of the plan; its picture is kept in `steps/` too."""
        self.kept.append((flow_step, len(self.steps), what))

    def visitor(self, key: str, who: str) -> Any:
        """Someone with no login (a supplier, an approver by email) in their own browser window."""
        ctx = self.browser.new_context(viewport=LAPTOP)
        page = ctx.new_page()
        page.set_default_timeout(30_000)
        page.on("pageerror", lambda e, k=key: self.problems.append(f"{k}: page error: {e}"))
        self.contexts[key], self.pages[key], self.names[key] = ctx, page, who
        return page

    def event(self, who: str, what: str, saw: str = "") -> None:
        """Something that happens outside the screens (a supplier's email arrives, the daily job runs): a line in the
        transcript with no picture."""
        self.steps.append(
            {
                "n": str(len(self.steps) + 1),
                "who": who,
                "did": what,
                "saw": squash(saw),
                "shot": "",
                "seconds": "0.0",
                "failed": "",
            }
        )

    def write_transcript(self, facts: list[str], verdict: str) -> Path:
        lines = [f"# {self.title}", "", f"Result: **{verdict}**", ""]
        lines += [
            "| # | Who | Did | The screen then showed | Picture |",
            "|---|---|---|---|---|",
        ]
        for s in self.steps:
            saw = s["saw"].replace("|", "/")
            did = s["did"] + (f" **FAILED: {s['failed']}**" if s["failed"] else "")
            pic = f"[{s['shot']}]({self.name}/{s['shot']})" if s["shot"] else ""
            lines.append(f"| {s['n']} | {s['who']} | {did} | {saw} | {pic} |")
        if self.flow:
            lines[4:4] = self._flow_table() + [""]
        if facts:
            lines += ["", "## What the data showed at the end", ""] + [f"- {f}" for f in facts]
        if self.problems:
            lines += ["", "## What the browser complained about", ""] + [f"- {p}" for p in self.problems]
        path = RESULTS / f"{self.name}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _flow_table(self) -> list[str]:
        """Which transcript step shows each of the plan's 13 steps; its picture is kept in `steps/` (committed)."""
        steps_dir = RESULTS / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        for old in steps_dir.glob(f"{self.name}-*.jpg"):
            old.unlink()
        out = ["## The 13 steps of the flow", "", "| Step | Shown at | Picture |", "|---|---|---|"]
        for n in sorted(self.flow):
            s = self.steps[self.flow[n] - 1]
            pic = ""
            if s["shot"] and (self.folder / s["shot"]).is_file():
                kept = f"{self.name}-{n:02d}-{slug(FLOW[n])}.jpg"
                shutil.copyfile(self.folder / s["shot"], steps_dir / kept)
                pic = f"[{kept}](steps/{kept})"
            out.append(f"| {n}. {FLOW[n]} | #{s['n']}: {s['did']} | {pic} |")
            for i, (_, at, what) in enumerate((k for k in self.kept if k[0] == n), start=1):
                s = self.steps[at - 1]
                pic = ""
                if s["shot"] and (self.folder / s["shot"]).is_file():
                    kept = f"{self.name}-{n:02d}-{i}-{slug(what)}.jpg"
                    shutil.copyfile(self.folder / s["shot"], steps_dir / kept)
                    pic = f"[{kept}](steps/{kept})"
                out.append(f"| {n}. {FLOW[n]}: {what} | #{s['n']}: {s['did']} | {pic} |")
        missing = [f"{n}. {FLOW[n]}" for n in FLOW if n not in self.flow]
        if missing:
            out += ["", f"Not shown: {', '.join(missing)}"]
        return out

    # ---- the background worker and the database -------------------------------------------------

    def work(self) -> int:
        """What the job worker does on its own between the steps: sourcing, ordering, notifications."""
        from p2p.platform import worker

        self.db.rollback()
        total = 0
        for _ in range(40):
            n = worker.run_once()
            total += n
            if not n:
                break
        self.db.rollback()
        return total

    def sync(self) -> None:
        """Let go of what this session has seen, so the next read shows what the browser changed."""
        self.db.rollback()

    def close(self) -> None:
        for ctx in self.contexts.values():
            try:
                ctx.close()
            except Exception:  # noqa: BLE001, S110
                pass


def api(method: str, path: str, headers: dict[str, str], **kw: Any) -> httpx.Response:
    """A direct call to the running app, to check what it refuses (the screens never offer these actions)."""
    return httpx.request(method, f"{URL}{path}", headers=headers, timeout=30, **kw)
