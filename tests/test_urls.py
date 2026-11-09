"""Issue #13: Scribd links with locale subdomains, no www, no title, shared links."""

import pytest

EMBED = "https://www.scribd.com/embeds/123456789/content"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.scribd.com/document/123456789/Document-Title",
        "https://www.scribd.com/doc/123456789/Document-Title",
        "https://de.scribd.com/document/123456789/Document-Title",
        "https://es.scribd.com/document/123456789/Document-Title",
        "https://fr.scribd.com/doc/123456789/Document-Title",
        "https://scribd.com/document/123456789/Document-Title",
        "http://www.scribd.com/document/123456789/Document-Title",
        "www.scribd.com/document/123456789/Document-Title",
        "scribd.com/document/123456789/Document-Title",
        "https://www.scribd.com/document/123456789",
        "https://www.scribd.com/document/123456789/",
        "https://www.scribd.com/document/123456789/Document-Title?utm_source=x#page=2",
        "https://WWW.SCRIBD.COM/document/123456789/Document-Title",
        "https://www.scribd.com/embeds/123456789/content",
        "  https://de.scribd.com/document/123456789/Document-Title  ",
    ],
)
def test_accepts_scribd_document_links(sd, url):
    assert sd.convert_scribd_link(url) == EMBED
    assert sd.parse_scribd_url(url)["doc_id"] == "123456789"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "https://notscribd.com/document/123456789/Title",
        "https://evil-scribd.com/document/123456789/Title",
        "https://scribd.com.evil.com/document/123456789/Title",
        "https://www.scribd.com/user/123456789/someone",
        "https://www.scribd.com/document/abc/Title",
        "ftp://www.scribd.com/document/123456789/Title",
        "https://www.scribd.com/",
    ],
)
def test_rejects_non_document_and_lookalike_links(sd, url):
    assert sd.parse_scribd_url(url) is None
    assert sd.convert_scribd_link(url) == "Invalid Scribd URL"


def test_keeps_secret_password_for_shared_private_links(sd):
    info = sd.parse_scribd_url(
        "https://www.scribd.com/document/123456789/Title?secret_password=AbC123"
    )
    assert info["secret_password"] == "AbC123"
    assert info["embed_url"] == EMBED + "?secret_password=AbC123"


def test_secret_password_is_url_encoded(sd):
    info = sd.parse_scribd_url(
        "https://de.scribd.com/document/123456789/Title?x=1&secret_password=a%2Bb%26c%3D"
    )
    assert info["secret_password"] == "a+b&c="
    assert info["embed_url"] == EMBED + "?secret_password=a%2Bb%26c%3D"


def test_redacts_secret_password_in_output(sd):
    url = EMBED + "?secret_password=AbC123&x=1"
    assert sd.redact_url(url) == EMBED + "?secret_password=REDACTED&x=1"
    assert "AbC123" not in sd.redact_url(url)
    assert sd.redact_url(EMBED) == EMBED


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.scribd.com/document/123456789/Document-Title", "Document-Title.pdf"),
        ("https://de.scribd.com/document/123456789/Mein%20Dokument", "Mein Dokument.pdf"),
        ("https://www.scribd.com/document/123456789", "123456789.pdf"),
        ("https://www.scribd.com/document/123456789/A%3AB%3FC", "A_B_C.pdf"),
        ("https://www.scribd.com/document/123456789/Title?secret_password=x", "Title.pdf"),
    ],
)
def test_filename_from_url(sd, url, expected):
    assert sd.get_filename_from_url(url) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
        ("  .hidden.  ", "hidden"),
        ("", "scribd_document"),
        ("Résumé – 2024", "Résumé – 2024"),
    ],
)
def test_sanitize_filename(sd, name, expected):
    assert sd.sanitize_filename(name) == expected
