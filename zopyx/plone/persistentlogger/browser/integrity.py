"""Manager-only integrity health surface."""

from __future__ import annotations

from Products.Five.browser import BrowserView

from ..integrity import verify_repository
from ..serialization import canonical_json
from ..storage import get_repository


class IntegrityHealthView(BrowserView):
    """Return a read-only JSON health report for one object's audit log."""

    def __call__(self) -> str:
        if getattr(self.request, "method", "GET") != "GET":
            self.request.response.setStatus(405)
            self.request.response.setHeader("Allow", "GET")
            self.request.response.setHeader("Content-Type", "application/json")
            return canonical_json(
                {"error": {"code": "method_not_allowed", "message": "GET required"}}
            )
        try:
            report = verify_repository(get_repository(self.context))
        except Exception:
            self.request.response.setStatus(500)
            self.request.response.setHeader("Content-Type", "application/json")
            self.request.response.setHeader("Cache-Control", "no-store")
            return canonical_json(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "integrity check unavailable",
                    }
                }
            )
        self.request.response.setHeader("Content-Type", "application/json")
        self.request.response.setHeader("Cache-Control", "no-store")
        return canonical_json(report.as_dict())


__all__ = ["IntegrityHealthView"]
