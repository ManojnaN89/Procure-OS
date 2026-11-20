"""A setup change made by one administrator and approved by another (D-22, maker-checker), as one call: for tests
whose subject is what the change does once it applies, not the approval itself."""

import json
from typing import Any

from fastapi.testclient import TestClient


class Applied:
    """The answer once the second administrator approved: the status the endpoint gave before maker-checker (201 for a
    post, else 200) and what the change returned, or 409 and the reason it could not apply."""

    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code, self._body = status_code, body

    def json(self) -> dict[str, Any]:
        return self._body

    @property
    def text(self) -> str:
        return json.dumps(self._body)


def change(client: TestClient, verb: str, path: str, maker: dict[str, str], checker: dict[str, str], **kw: Any) -> Any:
    """Make the change as `maker`; if it waits for approval, `checker` approves it. Anything else comes back as it is."""
    r = getattr(client, verb)(path, headers=maker, **kw)
    if r.status_code != 202 or "proposal_id" not in r.json():
        return r
    done = client.post(f"/api/admin/proposals/{r.json()['proposal_id']}/approve", json={}, headers=checker)
    assert done.status_code == 200, done.text
    body = done.json()
    if body["execution"] == "ok":
        return Applied(201 if verb == "post" else 200, body["result"])
    return Applied(409, {"detail": body["result"]["error"]})
