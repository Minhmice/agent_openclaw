"""Bearer-token authentication for the loopback dashboard."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping

from .models import DashboardAction

ACTOR_IDS: Mapping[str, str] = {
    "minh": "620891893659598850",
    "wien": "859783610625556480",
}

ACTOR_ACTIONS: Mapping[str, frozenset[DashboardAction]] = {
    "minh": frozenset(DashboardAction),
    "wien": frozenset(
        {
            DashboardAction.SELECT_LEAD,
            DashboardAction.WATCH_LEAD,
            DashboardAction.LEAD_APPROVE,
            DashboardAction.PAGE_STATUS,
            DashboardAction.PAGE_DONE,
            DashboardAction.PAGE_APPROVE,
            DashboardAction.FINAL_CONFIRM,
        }
    ),
}


class TokenAuthenticator:
    """Map opaque bearer tokens to actors without retaining plaintext tokens."""

    def __init__(self, tokens: Mapping[str, str]) -> None:
        digests: dict[str, str] = {}
        actors: dict[str, str] = {}
        for actor, token in tokens.items():
            actor_name = str(actor).strip().casefold()
            if actor_name not in ACTOR_ACTIONS:
                raise ValueError(f"unsupported dashboard actor: {actor}")
            if not isinstance(token, str) or not token:
                continue
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if digest in digests:
                raise ValueError("dashboard actors must not share a token")
            digests[digest] = actor_name
            actors[actor_name] = digest
        self._actor_by_digest = digests
        self._digest_by_actor = actors

    @classmethod
    def from_environment(cls) -> TokenAuthenticator:
        return cls(
            {
                "minh": os.environ.get("OPENCLAW_WEB_DASHBOARD_MINH_TOKEN", ""),
                "wien": os.environ.get("OPENCLAW_WEB_DASHBOARD_WIEN_TOKEN", ""),
            }
        )

    def authenticate(self, authorization: str | None) -> str | None:
        """Return the actor name or ``None`` for malformed/unknown credentials."""

        if not isinstance(authorization, str):
            return None
        parts = authorization.split(" ")
        if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1]:
            return None
        digest = hashlib.sha256(parts[1].encode("utf-8")).hexdigest()
        for known_digest, actor in self._actor_by_digest.items():
            if hmac.compare_digest(digest, known_digest):
                return actor
        return None

    def allows(self, actor: str, action: DashboardAction) -> bool:
        return action in ACTOR_ACTIONS.get(actor.casefold(), frozenset())

    def actor_id(self, actor: str) -> str:
        """Return the canonical workflow actor ID for an authenticated label."""

        try:
            return ACTOR_IDS[actor.casefold()]
        except KeyError as error:
            raise ValueError("unsupported dashboard actor") from error

    def permissions(self, actor: str) -> tuple[str, ...]:
        actions = ACTOR_ACTIONS.get(actor.casefold(), frozenset())
        return tuple(sorted(action.value for action in actions))


__all__ = ["ACTOR_ACTIONS", "ACTOR_IDS", "TokenAuthenticator"]
