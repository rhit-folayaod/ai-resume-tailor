"""Getting job posting text out of a URL or an uploaded file.

This is a best-effort layer by nature. Plenty of job boards render their
postings with JavaScript or block anything that is not a browser, and no amount
of care here changes that. So every failure says specifically what went wrong
and points at pasting the text instead, which always works.
"""

from __future__ import annotations

import io
import re
from urllib.parse import urlparse

from .errors import ResumeTailorError

# Enough text that it is plausibly a posting rather than a cookie banner or a
# "please enable JavaScript" shell.
MIN_USEFUL_CHARACTERS = 200

REQUEST_TIMEOUT = 20.0

# Sent because many sites serve a stub or a 403 to unrecognized clients. This is
# not an attempt to defeat a block; when a site does block, the error says so.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Page furniture that is never part of a posting.
_STRIP_TAGS = ("script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside")

_BLANK_LINES_RE = re.compile(r"\n{3,}")


class IngestError(ResumeTailorError):
    """A posting could not be read from the given source."""


def text_from_url(url: str) -> str:
    """Fetch a posting URL and return its readable text."""

    import httpx

    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise IngestError(f"{url!r} is not an http(s) URL.")

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers=BROWSER_HEADERS,
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise IngestError(f"could not reach {parsed.netloc}: {exc}") from exc

    if response.status_code in (401, 403, 429):
        raise IngestError(
            f"{parsed.netloc} refused the request (HTTP {response.status_code}). "
            "Sites like LinkedIn and Workday block automated fetches. Open the "
            "posting in your browser and paste the text, or save it as a PDF and "
            "upload that."
        )
    if response.status_code >= 400:
        raise IngestError(f"{parsed.netloc} returned HTTP {response.status_code} for that URL.")

    content_type = response.headers.get("content-type", "")
    if "application/pdf" in content_type:
        return text_from_pdf(response.content)

    return _finish(html_to_text(response.text), parsed.netloc)


def text_from_pdf(data: bytes) -> str:
    """Extract text from a PDF, e.g. a posting saved from a browser."""

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, OSError, ValueError) as exc:
        raise IngestError(f"that PDF could not be read: {exc}") from exc

    text = _collapse("\n".join(pages))
    if len(text) < MIN_USEFUL_CHARACTERS:
        raise IngestError(
            "that PDF has almost no extractable text. If it is a scan or a screenshot, "
            "there is no text layer to read — paste the posting text instead."
        )
    return text


def text_from_upload(filename: str, data: bytes) -> str:
    """Dispatch an uploaded file on its extension, with sniffing as a backstop."""

    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        return text_from_pdf(data)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise IngestError(f"{filename} is not text or a PDF.") from exc

    if name.endswith((".html", ".htm")) or "<html" in text[:2000].lower():
        text = html_to_text(text)

    return _finish(_collapse(text), filename or "that file")


def html_to_text(html: str) -> str:
    """Reduce a page to its readable text, preferring the main content region."""

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()

    region = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    return _collapse((region or soup).get_text("\n"))


def _collapse(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _finish(text: str, source: str) -> str:
    if len(text) < MIN_USEFUL_CHARACTERS:
        raise IngestError(
            f"only {len(text)} characters of text came back from {source}. The posting is "
            "probably rendered with JavaScript, which a plain fetch cannot see. Paste the "
            "text or upload a PDF of the page instead."
        )
    return text
