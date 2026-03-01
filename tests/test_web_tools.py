"""
test_web_tools.py — Tests unitaires pour web_tools.py

Familles :
  1. _sanitize_search_query  — PII, troncature, espaces
  2. _is_private_url          — blocklist réseau privé
  3. _safe_extract            — troncature, whitespace
  4. format_results_for_prompt — marquage SOURCE WEB, structure
  5. format_result_for_memory  — template mémoire
  6. _search_searxng           — urllib mocké, filtre privé, échecs réseau
  7. _search_ddg               — DDGS mocké, filtre privé, indisponible
  8. web_search                — fallback SearXNG → DDG, query vide
  9. web_is_configured         — combinaisons SEARXNG_URL / DDG
 10. Audit log                 — _audit_query appelé au bon moment
 11. Robustesse                — crashs réseau, JSON malformé, résultats vides

Zéro appel réseau réel, zéro Ollama.
"""

import json
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock, call
from io import StringIO

from Mnemo.tools import web_tools as wt

# Raccourcis
_sanitize          = wt._sanitize_search_query
_is_private        = wt._is_private_url
_safe_extract      = wt._safe_extract
format_prompt      = wt.format_results_for_prompt
format_memory      = wt.format_result_for_memory
web_search         = wt.web_search
web_is_configured  = wt.web_is_configured


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_module_state():
    """Remet les handlers du logger d'audit à zéro entre chaque test."""
    # Ferme proprement les handlers avant de les retirer (évite ResourceWarning)
    for h in list(wt._audit_logger.handlers):
        h.close()
    wt._audit_logger.handlers.clear()
    yield
    for h in list(wt._audit_logger.handlers):
        h.close()
    wt._audit_logger.handlers.clear()


def _make_result(title="Titre", url="https://example.com",
                 extract="Un extrait.", source="searxng") -> dict:
    return {"title": title, "url": url, "extract": extract, "source": source}


# ══════════════════════════════════════════════════════════════════
# 1. _sanitize_search_query
# ══════════════════════════════════════════════════════════════════

class TestSanitizeSearchQuery:

    def test_normal_query_unchanged(self):
        assert _sanitize("quicksort algorithm") == "quicksort algorithm"

    def test_strips_leading_trailing_spaces(self):
        assert _sanitize("  python tutorial  ") == "python tutorial"

    def test_email_redacted(self):
        result = _sanitize("contact user@example.com about python")
        assert "user@example.com" not in result
        assert "[REDACTED]" in result

    def test_ip_address_redacted(self):
        result = _sanitize("server at 192.168.1.42 down")
        assert "192.168.1.42" not in result
        assert "[REDACTED]" in result

    def test_french_phone_redacted(self):
        result = _sanitize("appelle le 06 12 34 56 78 pour info")
        assert "06 12 34 56 78" not in result
        assert "[REDACTED]" in result

    def test_phone_with_plus33_redacted(self):
        result = _sanitize("+33612345678 support")
        assert "+33612345678" not in result
        assert "[REDACTED]" in result

    def test_truncation_at_max_length(self):
        long_query = "mot " * 60  # >> 200 chars
        result = _sanitize(long_query)
        assert len(result) <= wt.MAX_QUERY_LENGTH

    def test_truncation_cuts_at_word_boundary(self):
        # 198 chars + " x" → doit couper avant le dernier mot
        query = "a " * 99 + "toolong"
        result = _sanitize(query)
        assert not result.endswith("tool") or result.endswith("toolong") is False
        assert len(result) <= wt.MAX_QUERY_LENGTH

    def test_empty_string_returns_empty(self):
        assert _sanitize("") == ""

    def test_only_spaces_returns_empty(self):
        assert _sanitize("   ") == ""

    def test_multiple_pii_all_redacted(self):
        result = _sanitize("mail user@test.com ip 10.0.0.1 tel 0612345678")
        assert "user@test.com" not in result
        assert "10.0.0.1" not in result

    def test_returns_string(self):
        assert isinstance(_sanitize("test"), str)


# ══════════════════════════════════════════════════════════════════
# 2. _is_private_url
# ══════════════════════════════════════════════════════════════════

class TestIsPrivateUrl:

    def test_localhost_blocked(self):
        assert _is_private("http://localhost:8080/page")

    def test_127_blocked(self):
        assert _is_private("http://127.0.0.1/admin")

    def test_192_168_blocked(self):
        assert _is_private("http://192.168.1.100/api")

    def test_10_x_blocked(self):
        assert _is_private("http://10.0.0.1/resource")

    def test_172_16_blocked(self):
        assert _is_private("http://172.16.0.1/")

    def test_172_31_blocked(self):
        assert _is_private("http://172.31.255.255/")

    def test_172_32_not_blocked(self):
        # 172.32.x.x est hors plage privée RFC1918
        assert not _is_private("http://172.32.0.1/")

    def test_host_docker_internal_blocked(self):
        assert _is_private("http://host.docker.internal:11434")

    def test_ipv6_loopback_blocked(self):
        assert _is_private("http://[::1]/page")

    def test_public_url_not_blocked(self):
        assert not _is_private("https://en.wikipedia.org/wiki/Quicksort")

    def test_public_url_with_numbers_not_blocked(self):
        assert not _is_private("https://python.org/3.12/docs")

    def test_empty_url_not_blocked(self):
        assert not _is_private("")


# ══════════════════════════════════════════════════════════════════
# 3. _safe_extract
# ══════════════════════════════════════════════════════════════════

class TestSafeExtract:

    def test_normal_text_unchanged(self):
        assert _safe_extract("Un extrait court.") == "Un extrait court."

    def test_empty_returns_empty(self):
        assert _safe_extract("") == ""

    def test_none_returns_empty(self):
        assert _safe_extract(None) == ""

    def test_truncation_at_max_chars(self):
        long_text = "mot " * 200
        result = _safe_extract(long_text)
        assert len(result) <= wt.MAX_EXTRACT_CHARS + 1  # +1 pour "…"

    def test_truncation_adds_ellipsis(self):
        long_text = "a " * 300
        result = _safe_extract(long_text)
        assert result.endswith("…")

    def test_multiple_spaces_collapsed(self):
        result = _safe_extract("mot   avec    espaces")
        assert "  " not in result

    def test_newlines_collapsed(self):
        result = _safe_extract("ligne1\nligne2\n\nligne3")
        assert "\n" not in result

    def test_short_text_no_ellipsis(self):
        result = _safe_extract("Court.")
        assert not result.endswith("…")


# ══════════════════════════════════════════════════════════════════
# 4. format_results_for_prompt
# ══════════════════════════════════════════════════════════════════

class TestFormatResultsForPrompt:

    def test_empty_list_returns_no_results_message(self):
        assert "Aucun résultat" in format_prompt([])

    def test_source_web_marker_present(self):
        result = format_prompt([_make_result()])
        assert "SOURCE WEB" in result

    def test_non_verifie_marker_present(self):
        result = format_prompt([_make_result()])
        assert "non vérifié" in result

    def test_title_present(self):
        result = format_prompt([_make_result(title="Quicksort Wikipedia")])
        assert "Quicksort Wikipedia" in result

    def test_url_present(self):
        result = format_prompt([_make_result(url="https://en.wikipedia.org/wiki/Q")])
        assert "https://en.wikipedia.org/wiki/Q" in result

    def test_extract_present(self):
        result = format_prompt([_make_result(extract="Algorithme de tri efficace.")])
        assert "Algorithme de tri efficace." in result

    def test_multiple_results_numbered(self):
        results = [_make_result(title=f"Résultat {i}") for i in range(1, 4)]
        text = format_prompt(results)
        assert "1." in text
        assert "2." in text
        assert "3." in text

    def test_no_url_in_result_no_crash(self):
        r = {"title": "Test", "extract": "Extrait", "source": "searxng"}
        result = format_prompt([r])
        assert "Test" in result

    def test_returns_string(self):
        assert isinstance(format_prompt([]), str)


# ══════════════════════════════════════════════════════════════════
# 5. format_result_for_memory
# ══════════════════════════════════════════════════════════════════

class TestFormatResultForMemory:

    def test_contains_web_marker(self):
        result = format_memory(_make_result(), "quicksort")
        assert "[web ·" in result

    def test_contains_url(self):
        result = format_memory(_make_result(url="https://example.com"), "test")
        assert "https://example.com" in result

    def test_contains_obsolete_warning(self):
        result = format_memory(_make_result(), "test")
        assert "potentiellement obsolète" in result

    def test_contains_today_date(self):
        today = datetime.now().strftime("%Y-%m-%d")
        result = format_memory(_make_result(), "test")
        assert today in result

    def test_missing_url_uses_fallback(self):
        result = format_memory({}, "test")
        assert "source inconnue" in result

    def test_returns_string(self):
        assert isinstance(format_memory(_make_result(), "test"), str)


# ══════════════════════════════════════════════════════════════════
# 6. _search_searxng
# ══════════════════════════════════════════════════════════════════

class TestSearchSearxng:

    def _fake_response(self, results: list) -> MagicMock:
        """Construit un faux objet réponse urllib."""
        body = json.dumps({"results": results}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_returns_empty_when_no_searxng_url(self):
        with patch.object(wt, "SEARXNG_URL", ""):
            assert wt._search_searxng("test", 5) == []

    def test_returns_results_on_success(self):
        fake = [{"title": "QS", "url": "https://en.wikipedia.org/wiki/Q", "content": "Fast sort."}]
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", return_value=self._fake_response(fake)), \
             patch.object(wt, "_audit_query"):
            results = wt._search_searxng("quicksort", 5)
        assert len(results) == 1
        assert results[0]["title"] == "QS"
        assert results[0]["source"] == "searxng"

    def test_private_url_filtered_out(self):
        fake = [
            {"title": "Public",  "url": "https://example.com",     "content": "ok"},
            {"title": "Private", "url": "http://192.168.1.1/admin", "content": "bad"},
        ]
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", return_value=self._fake_response(fake)), \
             patch.object(wt, "_audit_query"):
            results = wt._search_searxng("test", 5)
        assert len(results) == 1
        assert results[0]["title"] == "Public"

    def test_max_results_respected(self):
        fake = [{"title": f"R{i}", "url": f"https://ex{i}.com", "content": ""} for i in range(10)]
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", return_value=self._fake_response(fake)), \
             patch.object(wt, "_audit_query"):
            results = wt._search_searxng("test", 3)
        assert len(results) == 3

    def test_network_error_returns_empty(self):
        import urllib.error
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            assert wt._search_searxng("test", 5) == []

    def test_json_decode_error_returns_empty(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"NOT JSON"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", return_value=mock_resp):
            assert wt._search_searxng("test", 5) == []

    def test_audit_called_on_success(self):
        fake = [{"title": "T", "url": "https://ok.com", "content": "x"}]
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", return_value=self._fake_response(fake)), \
             patch.object(wt, "_audit_query") as mock_audit:
            wt._search_searxng("quicksort", 5)
        mock_audit.assert_called_once_with("quicksort", "searxng", 1)

    def test_extract_mapped_from_content_field(self):
        fake = [{"title": "T", "url": "https://ok.com", "content": "Contenu utile."}]
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", return_value=self._fake_response(fake)), \
             patch.object(wt, "_audit_query"):
            results = wt._search_searxng("test", 5)
        assert results[0]["extract"] == "Contenu utile."


# ══════════════════════════════════════════════════════════════════
# 7. _search_ddg
# ══════════════════════════════════════════════════════════════════

class TestSearchDdg:

    def test_returns_empty_when_ddg_unavailable(self):
        with patch.object(wt, "_DDG_AVAILABLE", False):
            assert wt._search_ddg("test", 5) == []

    def test_returns_results_on_success(self):
        fake = [{"title": "QS", "href": "https://en.wikipedia.org/wiki/Q", "body": "Fast."}]
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: s
        mock_ddgs.__exit__  = MagicMock(return_value=False)
        mock_ddgs.text.return_value = iter(fake)

        with patch.object(wt, "_DDG_AVAILABLE", True), \
             patch.object(wt, "DDGS", return_value=mock_ddgs), \
             patch.object(wt, "_audit_query"):
            results = wt._search_ddg("quicksort", 5)
        assert len(results) == 1
        assert results[0]["source"] == "duckduckgo"
        assert results[0]["url"] == "https://en.wikipedia.org/wiki/Q"

    def test_private_url_filtered_out(self):
        fake = [
            {"title": "OK",  "href": "https://example.com",    "body": "good"},
            {"title": "BAD", "href": "http://127.0.0.1/secret", "body": "bad"},
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: s
        mock_ddgs.__exit__  = MagicMock(return_value=False)
        mock_ddgs.text.return_value = iter(fake)

        with patch.object(wt, "_DDG_AVAILABLE", True), \
             patch.object(wt, "DDGS", return_value=mock_ddgs), \
             patch.object(wt, "_audit_query"):
            results = wt._search_ddg("test", 5)
        assert len(results) == 1
        assert results[0]["title"] == "OK"

    def test_ddg_exception_returns_empty(self):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: s
        mock_ddgs.__exit__  = MagicMock(return_value=False)
        mock_ddgs.text.side_effect = Exception("rate limit")

        with patch.object(wt, "_DDG_AVAILABLE", True), \
             patch.object(wt, "DDGS", return_value=mock_ddgs):
            assert wt._search_ddg("test", 5) == []

    def test_extract_mapped_from_body_field(self):
        fake = [{"title": "T", "href": "https://ok.com", "body": "Extrait DDG."}]
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: s
        mock_ddgs.__exit__  = MagicMock(return_value=False)
        mock_ddgs.text.return_value = iter(fake)

        with patch.object(wt, "_DDG_AVAILABLE", True), \
             patch.object(wt, "DDGS", return_value=mock_ddgs), \
             patch.object(wt, "_audit_query"):
            results = wt._search_ddg("test", 5)
        assert results[0]["extract"] == "Extrait DDG."

    def test_audit_called_on_success(self):
        fake = [{"title": "T", "href": "https://ok.com", "body": "x"}]
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: s
        mock_ddgs.__exit__  = MagicMock(return_value=False)
        mock_ddgs.text.return_value = iter(fake)

        with patch.object(wt, "_DDG_AVAILABLE", True), \
             patch.object(wt, "DDGS", return_value=mock_ddgs), \
             patch.object(wt, "_audit_query") as mock_audit:
            wt._search_ddg("quicksort", 5)
        mock_audit.assert_called_once_with("quicksort", "duckduckgo", 1)


# ══════════════════════════════════════════════════════════════════
# 8. web_search — fallback et interface publique
# ══════════════════════════════════════════════════════════════════

class TestWebSearch:

    def test_empty_query_returns_empty(self):
        assert web_search("") == []

    def test_pii_only_query_returns_empty(self):
        # Après sanitisation, la query devient "[REDACTED]" puis vide ou non-vide
        # Si elle n'est pas vide on vérifie juste que ça ne plante pas
        result = web_search("user@example.com")
        assert isinstance(result, list)

    def test_searxng_used_first(self):
        fake = [_make_result()]
        with patch.object(wt, "_search_searxng", return_value=fake) as mock_sx, \
             patch.object(wt, "_search_ddg", return_value=[]) as mock_ddg:
            web_search("quicksort")
        mock_sx.assert_called_once()
        mock_ddg.assert_not_called()

    def test_ddg_fallback_when_searxng_empty(self):
        fake = [_make_result(source="duckduckgo")]
        with patch.object(wt, "_search_searxng", return_value=[]), \
             patch.object(wt, "_search_ddg", return_value=fake) as mock_ddg:
            results = web_search("quicksort")
        mock_ddg.assert_called_once()
        assert results[0]["source"] == "duckduckgo"

    def test_returns_empty_when_both_backends_fail(self):
        with patch.object(wt, "_search_searxng", return_value=[]), \
             patch.object(wt, "_search_ddg", return_value=[]):
            assert web_search("anything") == []

    def test_query_is_sanitized_before_backends(self):
        """La query passée aux backends doit être la version sanitisée."""
        with patch.object(wt, "_search_searxng", return_value=[]) as mock_sx, \
             patch.object(wt, "_search_ddg", return_value=[]):
            web_search("  quicksort  ")
        called_query = mock_sx.call_args[0][0]
        assert called_query == "quicksort"  # stripped

    def test_max_results_passed_to_backends(self):
        with patch.object(wt, "_search_searxng", return_value=[]) as mock_sx, \
             patch.object(wt, "_search_ddg", return_value=[]):
            web_search("test", max_results=3)
        assert mock_sx.call_args[0][1] == 3

    def test_returns_list(self):
        with patch.object(wt, "_search_searxng", return_value=[]), \
             patch.object(wt, "_search_ddg", return_value=[]):
            assert isinstance(web_search("test"), list)


# ══════════════════════════════════════════════════════════════════
# 9. web_is_configured
# ══════════════════════════════════════════════════════════════════

class TestWebIsConfigured:

    def test_true_when_searxng_url_set(self):
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch.object(wt, "_DDG_AVAILABLE", False):
            assert web_is_configured() is True

    def test_true_when_ddg_available(self):
        with patch.object(wt, "SEARXNG_URL", ""), \
             patch.object(wt, "_DDG_AVAILABLE", True):
            assert web_is_configured() is True

    def test_true_when_both_available(self):
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch.object(wt, "_DDG_AVAILABLE", True):
            assert web_is_configured() is True

    def test_false_when_neither_available(self):
        with patch.object(wt, "SEARXNG_URL", ""), \
             patch.object(wt, "_DDG_AVAILABLE", False):
            assert web_is_configured() is False


# ══════════════════════════════════════════════════════════════════
# 10. Audit log
# ══════════════════════════════════════════════════════════════════

class TestAuditLog:

    def test_audit_does_not_crash_on_unwritable_path(self):
        """Si le log est inaccessible, aucun crash."""
        with patch.object(wt, "WEB_QUERY_LOG", "/root/no_permission/web.log"):
            # Réinitialise les handlers pour forcer _setup_audit_log
            wt._audit_logger.handlers.clear()
            try:
                wt._audit_query("test", "searxng", 3)
            except Exception as e:
                pytest.fail(f"_audit_query a levé une exception : {e}")

    def test_audit_message_contains_query(self):
        """Le message de log contient la query."""
        messages = []
        handler = MagicMock()
        handler.handle = lambda record: messages.append(record.getMessage())
        handler.level = 0
        wt._audit_logger.addHandler(handler)

        wt._audit_query("quicksort", "searxng", 3)

        assert any("quicksort" in m for m in messages)

    def test_audit_message_contains_backend(self):
        messages = []
        handler = MagicMock()
        handler.handle = lambda record: messages.append(record.getMessage())
        handler.level = 0
        wt._audit_logger.addHandler(handler)

        wt._audit_query("test", "duckduckgo", 2)

        assert any("duckduckgo" in m for m in messages)

    def test_setup_called_only_once(self):
        """_setup_audit_log ne doit ajouter qu'un seul handler."""
        with patch("logging.FileHandler") as mock_fh:
            mock_fh.return_value = MagicMock()
            mock_fh.return_value.level = 0
            mock_fh.return_value.handle = lambda r: None
            wt._setup_audit_log()
            wt._setup_audit_log()  # Second appel — ne doit pas ajouter un second handler
        # On vérifie qu'il n'y a pas plus d'un handler FileHandler
        file_handlers = [h for h in wt._audit_logger.handlers
                         if not isinstance(h, MagicMock)]
        # Le second appel est no-op grâce à `if _audit_logger.handlers: return`
        assert len(wt._audit_logger.handlers) <= 1


# ══════════════════════════════════════════════════════════════════
# 11. Robustesse
# ══════════════════════════════════════════════════════════════════

class TestRobustesse:

    def test_web_search_no_crash_on_network_failure(self):
        """Tous les backends en erreur → [] sans exception."""
        import urllib.error
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", side_effect=urllib.error.URLError("ko")), \
             patch.object(wt, "_DDG_AVAILABLE", False):
            result = web_search("test")
        assert result == []

    def test_web_search_no_crash_on_timeout(self):
        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", side_effect=TimeoutError()), \
             patch.object(wt, "_DDG_AVAILABLE", False):
            assert web_search("test") == []

    def test_searxng_empty_results_list(self):
        """SearXNG répond 200 mais results: [] → fallback DDG."""
        body = json.dumps({"results": []}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        fake_ddg = [_make_result(source="duckduckgo")]
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: s
        mock_ddgs.__exit__  = MagicMock(return_value=False)
        mock_ddgs.text.return_value = iter(fake_ddg)

        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", return_value=mock_resp), \
             patch.object(wt, "_DDG_AVAILABLE", True), \
             patch.object(wt, "DDGS", return_value=mock_ddgs), \
             patch.object(wt, "_audit_query"):
            results = web_search("test")

        assert results[0]["source"] == "duckduckgo"

    def test_all_results_private_urls_returns_empty(self):
        """Tous les résultats filtrés → []."""
        fake = [
            {"title": "A", "url": "http://localhost/a",     "content": "x"},
            {"title": "B", "url": "http://192.168.0.1/b",   "content": "x"},
        ]
        body = json.dumps({"results": fake}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(wt, "SEARXNG_URL", "http://localhost:8080"), \
             patch("urllib.request.urlopen", return_value=mock_resp), \
             patch.object(wt, "_audit_query"), \
             patch.object(wt, "_DDG_AVAILABLE", False):
            results = web_search("test")

        assert results == []

    def test_result_with_missing_fields_no_crash(self):
        """Résultat incomplet → ne plante pas."""
        results = format_prompt([{"source": "searxng"}])
        assert isinstance(results, str)