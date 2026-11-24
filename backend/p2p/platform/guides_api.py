"""The short guides in docs/guides/, one per role, read by any signed-in person on the Help page.

The pages are plain markdown files in the repository. Only files that sit in that folder and whose name is a plain slug
are served, so a request cannot reach any other file. The screen shows the text as text, never as HTML.
"""

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.config import REPO_ROOT
from p2p.platform import routing

router = APIRouter(prefix="/api", tags=["guides"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]

GUIDES = REPO_ROOT / "docs" / "guides"
SLUG = re.compile(r"[a-z0-9-]{1,40}")
# the guide that belongs to each role, in the order a person is shown them
ROLE_GUIDE = {
    "Requester": "requester",
    "Approver": "approver",
    "Buyer": "buyer",
    "Store": "store",
    "Quality": "quality",
    "AP": "ap-clerk",
    "Finance": "finance",
    "Admin": "admin",
}
FIRST = ("overview",)  # shown to everyone, before the role guides


def _title(markdown: str, slug: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return slug


def _available() -> dict[str, str]:
    """slug -> title of every guide on disk (the index page is not one of them)."""
    found: dict[str, str] = {}
    if GUIDES.is_dir():
        for path in sorted(GUIDES.glob("*.md")):
            if SLUG.fullmatch(path.stem) and path.stem != "README":
                found[path.stem] = _title(path.read_text(encoding="utf-8"), path.stem)
    return found


@router.get("/guides")
def list_guides(db: Db, ctx: Ctx) -> dict[str, Any]:
    """Every guide, with the ones for this person's roles marked."""
    roles = [
        r[0]
        for r in db.execute(
            text("select distinct role from user_role where user_id = :u order by 1"), {"u": ctx.user_id}
        )
    ]
    mine = [ROLE_GUIDE[r] for r in ROLE_GUIDE if r in roles]
    available = _available()
    order = [*FIRST, *ROLE_GUIDE.values()]
    slugs = [s for s in order if s in available]
    return {
        "guides": [{"slug": s, "title": available[s], "mine": s in mine} for s in slugs],
        "start": next((s for s in mine if s in available), FIRST[0] if FIRST[0] in available else None),
    }


@router.get("/guides/{slug}")
def get_guide(slug: str, ctx: Ctx) -> dict[str, str]:
    """The text of one guide, as markdown."""
    if not SLUG.fullmatch(slug) or slug not in _available():
        raise HTTPException(404, "There is no such guide")
    markdown = (GUIDES / f"{slug}.md").read_text(encoding="utf-8")
    return {"slug": slug, "title": _title(markdown, slug), "markdown": markdown}
