"""The role guides: every screen and button a guide names is in the app, each role has one, and the Help page's API only serves them."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_sourcing import PASSWORD, _headers, db  # noqa: F401 - fixture

from p2p.config import REPO_ROOT
from p2p.main import app
from p2p.platform import guides_api, users

client = TestClient(app)
GUIDES = REPO_ROOT / "docs" / "guides"
GUIDE_FILES = sorted(p for p in GUIDES.glob("*.md") if p.stem != "README")
# where the words on the screens are written: the main app, the admin app, and the lists the server sends to the screens
SOURCES = [
    REPO_ROOT / "ui" / "index.html",
    *(REPO_ROOT / "ui" / "js").rglob("*.js"),
    *(REPO_ROOT / "frontend" / "src").rglob("*.tsx"),
    REPO_ROOT / "backend" / "p2p" / "platform" / "workspace_api.py",
    REPO_ROOT / "backend" / "p2p" / "platform" / "setup_api.py",
]


def _screen_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in SOURCES)


def _names(markdown: str) -> list[str]:
    """What a guide writes in bold: the name of a menu item, a button, a tab or a list on the screen."""
    return [n.strip().rstrip(".,") for n in re.findall(r"\*\*([^*]+)\*\*", markdown)]


def test_there_is_a_short_guide_for_every_role_and_one_overview() -> None:
    stems = {p.stem for p in GUIDE_FILES}

    assert set(guides_api.ROLE_GUIDE) == set(users.ROLES)
    assert set(guides_api.ROLE_GUIDE.values()) | {"overview"} == stems
    for path in GUIDE_FILES:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# "), path.name
        assert len(text.splitlines()) <= 50, f"{path.name} is longer than one screen"
        assert "<" not in text, f"{path.name} has markup: the guides are plain text"


@pytest.mark.parametrize("path", GUIDE_FILES, ids=lambda p: p.stem)
def test_every_screen_and_button_a_guide_names_is_in_the_app(path) -> None:  # type: ignore[no-untyped-def]
    screen = _screen_text()
    names = _names(path.read_text(encoding="utf-8"))

    assert names, f"{path.name} names no screen at all"
    missing = sorted({n for n in names if n not in screen})
    assert not missing, f"{path.name} names things the app does not show: {missing}"


def test_a_name_that_is_not_in_the_app_is_caught() -> None:
    assert _names("Click **Approve** then **Fly to the moon**.") == ["Approve", "Fly to the moon"]
    assert "Fly to the moon" not in _screen_text()  # the check above can fail


def _person(db_: Session, role: str) -> int:
    return users.create_user(
        db_.info["slug"], f"{role.lower()}{db_.info['sfx']}@x.example", PASSWORD, f"A {role}", role
    )


def test_a_person_is_shown_their_own_guide_first_and_can_read_the_others(db: Session) -> None:  # noqa: F811
    buyer, requester = _person(db, "Buyer"), _person(db, "Requester")
    slug = db.info["slug"]

    listed = client.get("/api/guides", headers=_headers(slug, buyer, "Buyer")).json()

    assert listed["start"] == "buyer"
    assert [g["slug"] for g in listed["guides"]] == ["overview", *guides_api.ROLE_GUIDE.values()]
    assert [g["slug"] for g in listed["guides"] if g["mine"]] == ["buyer"]
    other = client.get("/api/guides/finance", headers=_headers(slug, requester, "Requester"))
    assert other.status_code == 200 and other.json()["markdown"].startswith("# Finance guide")
    assert client.get("/api/guides", headers=_headers(slug, requester, "Requester")).json()["start"] == "requester"


def test_a_person_with_two_roles_has_both_guides_marked(db: Session) -> None:  # noqa: F811
    both = _person(db, "AP")
    users.grant_role(db.info["slug"], both, "Finance")

    listed = client.get("/api/guides", headers=_headers(db.info["slug"], both, "AP")).json()

    assert [g["slug"] for g in listed["guides"] if g["mine"]] == ["ap-clerk", "finance"]
    assert listed["start"] == "ap-clerk"


@pytest.mark.parametrize("slug", ["README", "nope", "..%2FCLAUDE", "overview.md", "a" * 41])
def test_only_the_guides_themselves_are_served(db: Session, slug: str) -> None:  # noqa: F811
    me = _person(db, "Requester")

    assert client.get(f"/api/guides/{slug}", headers=_headers(db.info["slug"], me, "Requester")).status_code == 404


def test_the_guides_need_a_login(db: Session) -> None:  # noqa: F811
    assert client.get("/api/guides").status_code == 401
    assert client.get("/api/guides/overview").status_code == 401
