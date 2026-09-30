"""Bookmark-compatible redirects into the three-page valuation MVP."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import RedirectResponse


LEGACY_REDIRECTS: dict[str, str] = {
    "/": "/valuation",
    "/funds": "/valuation",
    "/assets": "/valuation",
    "/assets/funds": "/valuation",
    "/today": "/valuation",
    "/today/decisions": "/valuation",
    "/today/inbox": "/valuation",
    "/daily": "/valuation",
    "/assistant": "/valuation",
    "/funds/compare": "/valuation",
    "/portfolio/exposure": "/valuation",
    "/assets/research": "/valuation",
    "/review": "/valuation",
    "/review/journal": "/valuation",
    "/review/monthly": "/valuation",
    "/review/reports": "/valuation",
    "/review/tuning": "/valuation",
    "/reports": "/valuation",
    "/decisions": "/valuation",
    "/decisions/outcomes": "/valuation",
    "/decisions/threshold-tuning": "/valuation",
    "/ai-runs": "/valuation",
    "/transactions": "/valuation",
    "/trans": "/import",
    "/trans/records": "/valuation",
    "/trans/dca": "/valuation",
    "/dca": "/valuation",
    "/assets/holdings": "/holdings",
    "/trans/import": "/import",
}


def _make_redirect(target: str) -> Callable[[], RedirectResponse]:
    def _redirect() -> RedirectResponse:
        return RedirectResponse(url=target, status_code=308)

    return _redirect


def _valuation_redirect(**_: object) -> RedirectResponse:
    return RedirectResponse(url="/valuation", status_code=308)


LEGACY_PARAM_REDIRECTS: tuple[tuple[str, Callable[..., RedirectResponse]], ...] = (
    ("/reports/{id}", _valuation_redirect),
    ("/reports/{id}/decisions/{fingerprint}/history", _valuation_redirect),
    ("/ai-runs/{run_id}", _valuation_redirect),
    ("/candidate-research/{research_id}", _valuation_redirect),
)


def register_legacy_redirects(router: APIRouter) -> None:
    for path, target in LEGACY_REDIRECTS.items():
        router.add_api_route(
            path,
            _make_redirect(target),
            methods=["GET"],
            include_in_schema=False,
        )
    for path, endpoint in LEGACY_PARAM_REDIRECTS:
        router.add_api_route(path, endpoint, methods=["GET"], include_in_schema=False)
