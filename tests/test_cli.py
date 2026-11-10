"""The command line: every command runs on an empty company without crashing (a command that is only imported in
another command's branch fails here)."""

import uuid
from collections.abc import Iterator

import pytest

from p2p import cli
from p2p.platform import routing, tenants


@pytest.fixture
def slug(control_db: str) -> Iterator[str]:
    name = "c" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(name, load_reference=False).ok
    yield name
    routing.clear_engine_cache()


@pytest.mark.parametrize(
    "command",
    [
        ["source"],
        ["order"],
        ["remind"],
        ["remind", "--dry-run"],
        ["remind", "--schedule"],
        ["replenish", "--dry-run"],
    ],
)
def test_each_command_runs_on_a_company_with_nothing_in_it(
    slug: str, command: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([command[0], slug, *command[1:]]) == 0
    assert capsys.readouterr().out.strip() != ""
