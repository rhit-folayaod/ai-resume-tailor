import pytest

from resume_tailor.ingest import IngestError, html_to_text, text_from_pdf, text_from_upload

POSTING = (
    "Software Engineer Intern, Backend Platform. We are looking for someone with "
    "Python and PostgreSQL experience. Docker is a plus. You will build internal "
    "services, write tests, and work with the platform team on deployment tooling."
)


def test_html_extraction_drops_page_furniture():
    html = f"""
    <html><body>
      <nav>Home Jobs About</nav>
      <script>tracking()</script>
      <main><h1>Backend Intern</h1><p>{POSTING}</p></main>
      <footer>Cookie settings</footer>
    </body></html>
    """
    text = html_to_text(html)
    assert "PostgreSQL" in text
    assert "Cookie settings" not in text
    assert "tracking()" not in text


def test_html_extraction_prefers_the_main_region():
    html = f"""
    <html><body>
      <div>Recommended jobs you might like</div>
      <article><p>{POSTING}</p></article>
    </body></html>
    """
    assert "Recommended jobs" not in html_to_text(html)


def test_javascript_shell_is_reported_not_silently_accepted():
    html = "<html><body><div id='root'></div><p>Please enable JavaScript</p></body></html>"
    with pytest.raises(IngestError, match="rendered with JavaScript"):
        text_from_upload("posting.html", html.encode())


def test_uploaded_text_file():
    assert "PostgreSQL" in text_from_upload("posting.txt", POSTING.encode())


def test_pdf_without_a_text_layer_is_explained():
    minimal_pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    with pytest.raises(IngestError, match="no text layer|could not be read"):
        text_from_pdf(minimal_pdf)


def test_unreadable_bytes_are_reported():
    with pytest.raises(IngestError):
        text_from_pdf(b"this is not a pdf at all")
