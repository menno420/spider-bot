"""Projecting a public-safe report into a GitHub issue on `spider-swing`.

The owner asked for reports that are *"durable, easy for the developer to find
and act on — preferably through GitHub"*. GitHub is where the game work is
already managed, so this projects into `menno420/spider-swing` rather than
starting a second bug tracker inside the bot.

**GitHub is a sink, not the record.** Everything in this module runs *after*
the report is durably stored. A failure here marks the report
`publish_failed` and leaves it retryable; it never loses one.

**Idempotency, and why it takes three mechanisms rather than one.** GitHub
documents no idempotency key and no conditional create for `POST /issues`
(`docs.github.com/rest/guides/best-practices-for-using-the-rest-api`), so
retry-safety cannot come from the API. It is assembled here:

1. **The store's own publication record.** Written after a successful create,
   read before every attempt. This is the fast path and it is authoritative.
2. **A marker string in the issue body**, `Intake id <id>`, searched for before
   creating. `MEASURED` 2026-09-04 with positive AND negative controls against
   the real repository: `repo:menno420/spider-swing in:body "<marker>" is:issue`
   returns the containing issue for a phrase that is present and `total_count=0`
   for one that is not. It closes the window where step 1's write failed after
   the issue was created — the only way a duplicate could otherwise appear.
3. **Refusing to publish a report that already carries an issue number.**
   `Report.may_publish` is false once `github_issue_number` is set, so a
   re-entrant call cannot reach the network at all.

None of the three is atomic and the combination is not either: two truly
concurrent publishes of the same report inside one search window could still
double-create. That is stated rather than papered over — the intake service
serialises publication per report id, which closes it in practice for a single
worker, and a second worker is not something this deployment has.

**Fail-closed without a credential.** No token means `NullGitHubClient`: every
publish returns a refusal naming the missing configuration, the report stays
`stored`, and the owner's console shows it as awaiting publication. Nothing
invents a credential and nothing pretends to have published.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

from spiderbot.intake.models import Report

log = logging.getLogger("spiderbot.intake.github")

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
TIMEOUT_S = 20.0

#: The one label this bot adds. Everything else reuses `spider-swing`'s
#: existing taxonomy — see `Report.labels`.
ORIGIN_LABEL = "from-spider-bot"


@dataclass(frozen=True)
class Published:
    number: int
    url: str


@dataclass(frozen=True)
class PublishFailure:
    reason: str
    detail: str = ""
    retryable: bool = True


PublishResult = Published | PublishFailure


class GitHubClient(Protocol):
    @property
    def available(self) -> bool: ...

    async def find_issue_by_marker(self, marker: str) -> Published | None: ...

    async def create_issue(
        self, title: str, body: str, labels: list[str]
    ) -> PublishResult: ...


class NullGitHubClient:
    """No credential configured. Fail-closed, and say which setting is missing."""

    def __init__(self, reason: str = "GITHUB_TOKEN is not set") -> None:
        self._reason = reason

    @property
    def available(self) -> bool:
        return False

    async def find_issue_by_marker(self, marker: str) -> Published | None:
        return None

    async def create_issue(self, title, body, labels) -> PublishResult:
        return PublishFailure("no_credential", self._reason, retryable=True)


class HttpGitHubClient:
    """The real one. `aiohttp` comes with discord.py, so this adds no dependency.

    Never raises: every failure is a `PublishFailure` naming what happened, and
    the distinction between retryable and permanent is what stops a retry loop
    hammering a 404 forever.
    """

    def __init__(self, token: str, repo: str, *, session=None) -> None:
        self._token = token
        self._repo = repo
        self._session = session

    @property
    def available(self) -> bool:
        return bool(self._token and self._repo)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "spider-bot",
        }

    async def _request(self, method: str, url: str, payload: Any = None):
        session = self._session
        owned = session is None
        if owned:
            session = aiohttp.ClientSession()
        try:
            async with session.request(
                method,
                url,
                headers=self._headers(),
                data=json.dumps(payload) if payload is not None else None,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as response:
                text = await response.text()
                return response.status, text
        finally:
            if owned:
                await session.close()

    async def find_issue_by_marker(self, marker: str) -> Published | None:
        """Search for an issue already carrying this marker.

        Best-effort by design: a failure here returns None and the caller
        proceeds to create, because refusing to publish because *search* was
        down would turn a GitHub hiccup into a lost projection. The store's
        publication record is the authoritative check; this is the backstop.

        A hit is believed only when the returned body actually contains the
        marker. Search matches on tokens, and the marker sits in a field a
        member types, so an unverified hit is a way to make a report resolve
        to somebody else's issue. `redact.for_github` breaks minted ids in
        member text as well; either half alone closes it, and both are cheap.
        """
        from urllib.parse import quote

        query = quote(f'repo:{self._repo} in:body "{marker}" is:issue')
        try:
            status, text = await self._request("GET", f"{API_ROOT}/search/issues?q={query}")
        except (aiohttp.ClientError, TimeoutError):
            log.warning("github: marker search failed; falling back to create")
            return None
        if status != 200:
            log.warning("github: marker search returned %s", status)
            return None
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        # Shape, not just parseability: an interposing proxy or CDN error page
        # can return a 200 whose body is a JSON array, and `.get` on a list
        # raises out of a class whose docstring promises it never raises.
        items = payload.get("items") or [] if isinstance(payload, dict) else []
        for item in items:
            if not isinstance(item, dict) or not item.get("number"):
                continue
            # GitHub's code search tokenises, so a quoted phrase is a hint and
            # not a guarantee. Confirm the marker is actually in the body
            # before treating this issue as the report's existing projection —
            # otherwise a member who wrote another report's id into their own
            # text makes that report resolve to THIS issue and disappear.
            body = item.get("body")
            if isinstance(body, str) and marker not in body:
                continue
            try:
                return Published(int(item["number"]), str(item.get("html_url") or ""))
            except (TypeError, ValueError):
                continue
        return None

    async def create_issue(self, title, body, labels) -> PublishResult:
        payload = {"title": title, "body": body, "labels": labels}
        try:
            status, text = await self._request(
                "POST", f"{API_ROOT}/repos/{self._repo}/issues", payload
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            return PublishFailure("network", type(exc).__name__, retryable=True)
        if status == 201:
            try:
                data = json.loads(text)
                return Published(int(data["number"]), str(data.get("html_url") or ""))
            except (ValueError, KeyError, TypeError):
                # Created but unparseable: the marker search will find it on the
                # next attempt, so this is retryable and cannot duplicate.
                return PublishFailure("bad_response", text[:120], retryable=True)
        if status in (403, 429):
            # GitHub returns 403 for BOTH "token lacks permission" and a
            # secondary rate limit, so this cannot be classified as permanent
            # without guessing. Retryable, and the reason names the ambiguity.
            return PublishFailure(
                "forbidden_or_rate_limited",
                f"HTTP {status} — a missing Issues:write permission and a "
                "secondary rate limit are indistinguishable here",
                retryable=True,
            )
        if status == 404:
            # GitHub deliberately returns 404 rather than 403 for a repository
            # the token cannot see, so this means "wrong repo OR no access".
            return PublishFailure(
                "not_found_or_no_access",
                f"HTTP 404 for {self._repo}",
                retryable=False,
            )
        if status == 410:
            return PublishFailure("issues_disabled", "HTTP 410", retryable=False)
        if status == 422:
            return PublishFailure("rejected", text[:200], retryable=False)
        return PublishFailure("http_error", f"HTTP {status}: {text[:120]}", retryable=True)


async def publish(client: GitHubClient, report: Report) -> PublishResult:
    """Project one report. The caller must already have checked `may_publish`.

    This function re-checks it anyway. A publication path that trusts its
    caller is one refactor away from publishing a private complaint, and the
    check costs nothing.
    """
    if not report.may_publish:
        return PublishFailure(
            "not_publishable",
            f"sensitivity={report.sensitivity} status={report.status} "
            f"issue={report.github_issue_number}",
            retryable=False,
        )
    if not client.available:
        return PublishFailure("no_credential", "no GitHub client configured")

    existing = await client.find_issue_by_marker(report.marker())
    if existing is not None:
        log.info("github: %s already published as #%s", report.id, existing.number)
        return existing

    title, body, labels = report.public_title(), report.public_body(), report.labels()
    result = await client.create_issue(title, body, labels)
    if isinstance(result, PublishFailure) and result.reason == "rejected" and labels:
        # A 422 with labels present, retried once without them. `from-spider-bot`
        # does NOT exist in menno420/spider-swing — verified live 2026-09-04, the
        # repo has thirteen labels and that is not one of them — and GitHub's own
        # docs do not say what happens to an unknown label name when the caller
        # has push access. Losing a report to a label would be absurd, so the
        # label is what gets dropped. This lives here rather than in the HTTP
        # client because it is a policy decision about what matters, not a
        # transport detail: every client gets it, and a fake can exercise it.
        log.warning("github: create rejected with labels %s; retrying without", labels)
        bare = await client.create_issue(title, body, [])
        if isinstance(bare, Published):
            return bare
    return result
