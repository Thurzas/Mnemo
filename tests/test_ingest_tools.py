"""
Tests — ingest_tools.py
Deux groupes :
  - Sans dépendance externe : clean_text, chunk_text, chunk_pages, file_hash, DB helpers
  - @pytest.mark.ollama : upsert_doc_chunk, search_docs_*, ingest_pdf pipeline complet

Lance sans Ollama :
    uv run pytest tests/test_ingest_tools.py -v -m "not ollama"

Lance tout :
    uv run pytest tests/test_ingest_tools.py -v
"""
import hashlib
import sqlite3
import pytest

from pathlib import Path
from unittest.mock import MagicMock, patch


from Mnemo.tools.ingest_tools import (
    clean_text,
    chunk_text,
    chunk_pages,
    file_hash,
    is_already_ingested,
    register_document,
    list_ingested_documents,
    upsert_doc_chunk,
    search_docs_keyword,
    search_docs_vector,
    ingest_pdf,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    MIN_CHUNK_LEN,
)


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

def create_doc_schema(db: sqlite3.Connection):
    """Crée le schéma Phase 2 dans une DB en mémoire."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id          TEXT PRIMARY KEY,
            filename    TEXT NOT NULL,
            path        TEXT NOT NULL,
            mime_type   TEXT NOT NULL,
            page_count  INTEGER,
            chunk_count INTEGER DEFAULT 0,
            ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS doc_chunks (
            id                TEXT PRIMARY KEY,
            doc_id            TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            page              INTEGER,
            chunk_index       INTEGER,
            content           TEXT NOT NULL,
            importance_weight REAL DEFAULT 1.0,
            category          TEXT DEFAULT 'connaissance',
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS doc_embeddings (
            chunk_id    TEXT PRIMARY KEY REFERENCES doc_chunks(id) ON DELETE CASCADE,
            model       TEXT NOT NULL,
            vector      BLOB NOT NULL,
            dim         INTEGER NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
            chunk_id UNINDEXED,
            content,
            filename,
            tokenize = 'unicode61'
        );
    """)
    db.commit()


@pytest.fixture
def db_mem() -> sqlite3.Connection:
    """DB SQLite en mémoire avec schéma Phase 2."""
    db = sqlite3.connect(":memory:")
    create_doc_schema(db)
    yield db
    db.close()


@pytest.fixture
def db_file(tmp_path) -> Path:
    """DB SQLite sur disque temporaire — pour les tests qui passent db_path."""
    db_path = tmp_path / "test.db"
    db = sqlite3.connect(str(db_path))
    create_doc_schema(db)
    db.close()
    return db_path


@pytest.fixture
def sample_pdf(tmp_path) -> Path:
    """
    Crée un vrai PDF minimal avec pypdf pour les tests.
    Si pypdf n'est pas dispo, les tests qui l'utilisent sont skippés.
    """
    pytest.importorskip("pypdf")
    try:
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        path = tmp_path / "test.pdf"
        with open(path, "wb") as f:
            writer.write(f)
        return path
    except Exception:
        pytest.skip("Impossible de créer un PDF de test")


@pytest.fixture
def fake_pdf(tmp_path) -> Path:
    """Fichier .pdf factice (pas un vrai PDF) — pour tester les cas d'erreur."""
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"pas un vrai PDF")
    return path


# ══════════════════════════════════════════════════════════════
# clean_text
# ══════════════════════════════════════════════════════════════

class TestCleanText:

    def test_supprime_cesure_fin_de_ligne(self):
        assert clean_text("habi-\ntude") == "habitude"

    def test_normalise_sauts_de_ligne_multiples(self):
        result = clean_text("ligne 1\n\n\n\nligne 2")
        assert "\n\n\n" not in result

    def test_normalise_espaces_multiples(self):
        result = clean_text("mot1   mot2     mot3")
        assert "  " not in result
        assert "mot1 mot2 mot3" in result

    def test_texte_normal_inchange(self):
        text = "Ainz Ooal Gown est le maître du Grand Tombeau de Nazarick."
        assert clean_text(text) == text

    def test_strip_debut_fin(self):
        result = clean_text("   texte   ")
        assert result == result.strip()

    def test_string_vide(self):
        assert clean_text("") == ""

    def test_uniquement_espaces(self):
        assert clean_text("   \n\n   ") == ""


# ══════════════════════════════════════════════════════════════
# chunk_text
# ══════════════════════════════════════════════════════════════

class TestChunkText:

    def _make_text(self, n_words: int) -> str:
        return " ".join(f"mot{i}" for i in range(n_words))

    def test_texte_vide_retourne_rien(self):
        chunks = list(chunk_text(""))
        assert chunks == []

    def test_texte_court_retourne_un_chunk(self):
        text = "Un texte suffisamment long pour dépasser le minimum de quatre-vingts caractères requis."
        chunks = list(chunk_text(text, chunk_size=50, overlap=5))
        assert len(chunks) == 1

    def test_chunks_ont_taille_maximale(self):
        text = self._make_text(500)
        chunks = list(chunk_text(text, chunk_size=100, overlap=10))
        for chunk in chunks:
            words = chunk.split()
            assert len(words) <= 100

    def test_overlap_produit_mots_communs(self):
        """Les chunks consécutifs doivent partager des mots (overlap)."""
        text = self._make_text(300)
        chunks = list(chunk_text(text, chunk_size=100, overlap=20))
        if len(chunks) >= 2:
            words1 = set(chunks[0].split())
            words2 = set(chunks[1].split())
            assert len(words1 & words2) > 0

    def test_chunk_trop_court_ignore(self):
        """Un chunk de moins de MIN_CHUNK_LEN caractères doit être ignoré."""
        chunks = list(chunk_text("court", chunk_size=50, overlap=5))
        assert chunks == []

    def test_tous_les_chunks_respectent_min_len(self):
        text = self._make_text(1000)
        for chunk in chunk_text(text):
            assert len(chunk) >= MIN_CHUNK_LEN

    def test_chunk_size_personnalise(self):
        text = self._make_text(200)
        chunks_small = list(chunk_text(text, chunk_size=50, overlap=5))
        chunks_large = list(chunk_text(text, chunk_size=100, overlap=5))
        assert len(chunks_small) > len(chunks_large)


# ══════════════════════════════════════════════════════════════
# chunk_pages
# ══════════════════════════════════════════════════════════════

class TestChunkPages:

    def _make_pages(self, texts: list[str]) -> list[dict]:
        return [{"page": i + 1, "text": t} for i, t in enumerate(texts)]

    def test_pages_vides_retourne_liste_vide(self):
        assert chunk_pages([]) == []

    def test_structure_chunk(self):
        pages = self._make_pages(["Un texte suffisamment long pour générer au moins un chunk valide dans ce test."])
        chunks = chunk_pages(pages)
        if chunks:
            c = chunks[0]
            assert "page" in c
            assert "chunk_index" in c
            assert "content" in c

    def test_numero_page_correct(self):
        pages = self._make_pages([
            "Page 1 " + "mot " * 50,
            "Page 2 " + "mot " * 50,
        ])
        chunks = chunk_pages(pages)
        pages_in_chunks = {c["page"] for c in chunks}
        assert 1 in pages_in_chunks
        assert 2 in pages_in_chunks

    def test_chunk_index_incremental(self):
        pages = self._make_pages(["mot " * 200, "mot " * 200])
        chunks = chunk_pages(pages)
        indices = [c["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_texte_long_produit_plusieurs_chunks(self):
        """Un texte de 1000 mots doit produire plusieurs chunks."""
        pages = self._make_pages(["mot " * 1000])
        chunks = chunk_pages(pages)
        assert len(chunks) > 1


# ══════════════════════════════════════════════════════════════
# file_hash
# ══════════════════════════════════════════════════════════════

class TestFileHash:

    def test_meme_contenu_meme_hash(self, tmp_path):
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_bytes(b"contenu identique")
        f2.write_bytes(b"contenu identique")
        assert file_hash(f1) == file_hash(f2)

    def test_contenu_different_hash_different(self, tmp_path):
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_bytes(b"contenu A")
        f2.write_bytes(b"contenu B")
        assert file_hash(f1) != file_hash(f2)

    def test_hash_longueur_md5(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"test")
        assert len(file_hash(f)) == 32

    def test_modification_change_hash(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"version 1")
        h1 = file_hash(f)
        f.write_bytes(b"version 2")
        h2 = file_hash(f)
        assert h1 != h2


# ══════════════════════════════════════════════════════════════
# is_already_ingested / register_document
# ══════════════════════════════════════════════════════════════

class TestDocumentRegistry:

    def test_document_inexistant_retourne_false(self, db_mem):
        assert is_already_ingested(db_mem, "hash_inexistant") is False

    def test_document_enregistre_retourne_true(self, db_mem, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"contenu")
        register_document(db_mem, "doc_id_001", f, page_count=10, chunk_count=25)
        db_mem.commit()
        assert is_already_ingested(db_mem, "doc_id_001") is True

    def test_register_stocke_les_metadonnees(self, db_mem, tmp_path):
        f = tmp_path / "overlord.pdf"
        f.write_bytes(b"contenu")
        register_document(db_mem, "hash_abc", f, page_count=350, chunk_count=355)
        db_mem.commit()
        row = db_mem.execute(
            "SELECT filename, page_count, chunk_count, mime_type FROM documents WHERE id = ?",
            ("hash_abc",)
        ).fetchone()
        assert row[0] == "overlord.pdf"
        assert row[1] == 350
        assert row[2] == 355
        assert row[3] == "application/pdf"

    def test_register_stocke_le_chemin_absolu(self, db_mem, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"x")
        register_document(db_mem, "hash_path", f, page_count=1, chunk_count=1)
        db_mem.commit()
        row = db_mem.execute("SELECT path FROM documents WHERE id = ?", ("hash_path",)).fetchone()
        assert Path(row[0]).is_absolute()

    def test_deux_documents_differents(self, db_mem, tmp_path):
        f1 = tmp_path / "doc1.pdf"
        f2 = tmp_path / "doc2.pdf"
        f1.write_bytes(b"doc1")
        f2.write_bytes(b"doc2")
        register_document(db_mem, "hash_1", f1, 10, 20)
        register_document(db_mem, "hash_2", f2, 5, 10)
        db_mem.commit()
        assert is_already_ingested(db_mem, "hash_1") is True
        assert is_already_ingested(db_mem, "hash_2") is True


# ══════════════════════════════════════════════════════════════
# list_ingested_documents
# ══════════════════════════════════════════════════════════════

class TestListIngestedDocuments:

    def test_aucun_document_retourne_liste_vide(self, db_file):
        result = list_ingested_documents(db_file)
        assert result == []

    def test_retourne_les_documents_enregistres(self, db_file, tmp_path):
        db = sqlite3.connect(str(db_file))
        f = tmp_path / "test.pdf"
        f.write_bytes(b"x")
        register_document(db, "hash_list", f, 10, 20)
        db.commit()
        db.close()

        result = list_ingested_documents(db_file)
        assert len(result) == 1
        assert result[0]["filename"] == "test.pdf"
        assert result[0]["pages"] == 10
        assert result[0]["chunks"] == 20

    def test_structure_retournee(self, db_file, tmp_path):
        db = sqlite3.connect(str(db_file))
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"x")
        register_document(db, "hash_struct", f, 1, 1)
        db.commit()
        db.close()

        result = list_ingested_documents(db_file)
        doc = result[0]
        assert "filename" in doc
        assert "pages" in doc
        assert "chunks" in doc
        assert "ingested_at" in doc

    def test_ordre_decroissant_par_date(self, db_file, tmp_path):
        """Le document le plus récent doit apparaître en premier."""
        db = sqlite3.connect(str(db_file))
        for i, name in enumerate(["ancien.pdf", "recent.pdf"]):
            f = tmp_path / name
            f.write_bytes(f"contenu {i}".encode())
            register_document(db, f"hash_{i}", f, 1, 1)
            db.commit()
        db.close()

        result = list_ingested_documents(db_file)
        assert len(result) == 2
        # Le plus récent (recent.pdf) doit être en premier
        assert result[0]["filename"] == "recent.pdf"


# ══════════════════════════════════════════════════════════════
# extract_pdf_pages — tests avec mock PdfReader
# ══════════════════════════════════════════════════════════════

class TestExtractPdfPages:

    def _make_mock_reader(self, pages_text: list[str]):
        """Crée un mock PdfReader avec des pages dont extract_text() retourne le texte donné."""
        mock_reader = MagicMock()
        mock_pages  = []
        for text in pages_text:
            mock_page = MagicMock()
            mock_page.extract_text.return_value = text
            mock_pages.append(mock_page)
        mock_reader.pages = mock_pages
        return mock_reader

    def test_fichier_inexistant_leve_erreur(self, tmp_path):
        from Mnemo.tools.ingest_tools import extract_pdf_pages
        with pytest.raises(FileNotFoundError):
            extract_pdf_pages(tmp_path / "inexistant.pdf")

    def test_extension_non_pdf_leve_erreur(self, tmp_path):
        from Mnemo.tools.ingest_tools import extract_pdf_pages
        f = tmp_path / "doc.docx"
        f.write_bytes(b"x")
        with pytest.raises(ValueError):
            extract_pdf_pages(f)

    def test_extraction_retourne_pages_avec_texte(self, tmp_path):
        from Mnemo.tools.ingest_tools import extract_pdf_pages, HAS_PYPDF
        if not HAS_PYPDF:
            pytest.skip("pypdf non installé")

        f = tmp_path / "test.pdf"
        f.write_bytes(b"x")  # Fichier bidon, on mock le reader

        mock_reader = self._make_mock_reader(["Texte de la page 1", "Texte de la page 2", ""])
        with patch("Mnemo.tools.ingest_tools.PdfReader", return_value=mock_reader):
            pages = extract_pdf_pages(f)

        # La page vide doit être filtrée
        assert len(pages) == 2
        assert pages[0]["page"] == 1
        assert pages[1]["page"] == 2

    def test_pages_vides_filtrees(self, tmp_path):
        from Mnemo.tools.ingest_tools import extract_pdf_pages, HAS_PYPDF
        if not HAS_PYPDF:
            pytest.skip("pypdf non installé")

        f = tmp_path / "test.pdf"
        f.write_bytes(b"x")

        mock_reader = self._make_mock_reader(["", "", ""])
        with patch("Mnemo.tools.ingest_tools.PdfReader", return_value=mock_reader):
            pages = extract_pdf_pages(f)

        assert pages == []

    def test_numerotation_commence_a_1(self, tmp_path):
        from Mnemo.tools.ingest_tools import extract_pdf_pages, HAS_PYPDF
        if not HAS_PYPDF:
            pytest.skip("pypdf non installé")

        f = tmp_path / "test.pdf"
        f.write_bytes(b"x")

        mock_reader = self._make_mock_reader(["Page A", "Page B"])
        with patch("Mnemo.tools.ingest_tools.PdfReader", return_value=mock_reader):
            pages = extract_pdf_pages(f)

        assert pages[0]["page"] == 1
        assert pages[1]["page"] == 2


# ══════════════════════════════════════════════════════════════
# ingest_pdf — pipeline complet (Ollama mocké)
# ══════════════════════════════════════════════════════════════

class TestIngestPdfPipeline:
    """
    Tests du pipeline complet avec Ollama mocké.
    On vérifie la logique (détection doublon, status retourné, entrées DB)
    sans faire de vrais appels d'embedding.
    """

    @pytest.fixture(autouse=True)
    def mock_embed(self):
        """Mock embed() pour retourner un vecteur aléatoire fixe."""
        import numpy as np
        with patch("Mnemo.tools.ingest_tools.embed",
                   return_value=np.ones(768, dtype=np.float32)):
            yield

    @pytest.fixture(autouse=True)
    def mock_pdf_extraction(self):
        """Mock l'extraction PDF pour retourner des pages de test."""
        fake_pages = [
            {"page": 1, "text": "mot " * 200},
            {"page": 2, "text": "mot " * 200},
        ]
        with patch("Mnemo.tools.ingest_tools.extract_pdf_pages",
                   return_value=fake_pages), \
             patch("Mnemo.tools.ingest_tools.get_pdf_page_count",
                   return_value=2):
            yield

    def test_ingestion_retourne_status_ingested(self, db_file, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"contenu unique pdf")
        result = ingest_pdf(f, db_path=db_file)
        assert result["status"] == "ingested"

    def test_ingestion_retourne_filename(self, db_file, tmp_path):
        f = tmp_path / "overlord.pdf"
        f.write_bytes(b"contenu")
        result = ingest_pdf(f, db_path=db_file)
        assert result["filename"] == "overlord.pdf"

    def test_ingestion_retourne_nombre_chunks(self, db_file, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"contenu")
        result = ingest_pdf(f, db_path=db_file)
        assert result["chunks"] > 0

    def test_chunks_ecrits_en_db(self, db_file, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"contenu")
        result = ingest_pdf(f, db_path=db_file)

        db = sqlite3.connect(str(db_file))
        count = db.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()[0]
        db.close()
        assert count == result["chunks"]

    def test_document_enregistre_en_db(self, db_file, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"contenu")
        ingest_pdf(f, db_path=db_file)

        db = sqlite3.connect(str(db_file))
        row = db.execute("SELECT filename FROM documents").fetchone()
        db.close()
        assert row is not None
        assert row[0] == "doc.pdf"

    def test_double_ingestion_retourne_already_ingested(self, db_file, tmp_path):
        """Ingérer deux fois le même fichier doit retourner 'already_ingested'."""
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"contenu identique")
        ingest_pdf(f, db_path=db_file)
        result2 = ingest_pdf(f, db_path=db_file)
        assert result2["status"] == "already_ingested"

    def test_double_ingestion_ne_duplique_pas_les_chunks(self, db_file, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"contenu identique")
        r1 = ingest_pdf(f, db_path=db_file)
        ingest_pdf(f, db_path=db_file)

        db = sqlite3.connect(str(db_file))
        count = db.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()[0]
        db.close()
        assert count == r1["chunks"]

    def test_pdf_vide_retourne_status_empty(self, db_file, tmp_path):
        f = tmp_path / "vide.pdf"
        f.write_bytes(b"contenu")
        with patch("Mnemo.tools.ingest_tools.extract_pdf_pages", return_value=[]):
            result = ingest_pdf(f, db_path=db_file)
        assert result["status"] == "empty"
        assert result["chunks"] == 0


# ══════════════════════════════════════════════════════════════
# search_docs_keyword — FTS5, pas d'Ollama
# ══════════════════════════════════════════════════════════════

class TestSearchDocsKeyword:

    @pytest.fixture
    def db_with_doc(self, db_mem, tmp_path) -> sqlite3.Connection:
        """DB avec un document et des chunks pré-insérés."""
        import numpy as np
        f = tmp_path / "overlord.pdf"
        f.write_bytes(b"x")
        register_document(db_mem, "doc_001", f, 10, 3)

        chunks = [
            {"id": "c1", "content": "Ainz Ooal Gown est le roi des morts-vivants.", "page": 1, "chunk_index": 0},
            {"id": "c2", "content": "Le Grand Tombeau de Nazarick est une forteresse imprenable.", "page": 2, "chunk_index": 1},
            {"id": "c3", "content": "Albedo est la gardienne en chef du tombeau.", "page": 3, "chunk_index": 2},
        ]
        vec = np.ones(8, dtype=np.float32).tobytes()
        for c in chunks:
            db_mem.execute("""
                INSERT INTO doc_chunks (id, doc_id, page, chunk_index, content, importance_weight, category)
                VALUES (?, 'doc_001', ?, ?, ?, 1.0, 'connaissance')
            """, (c["id"], c["page"], c["chunk_index"], c["content"]))
            db_mem.execute("""
                INSERT INTO doc_embeddings (chunk_id, model, vector, dim)
                VALUES (?, 'test', ?, 8)
            """, (c["id"], vec))
            db_mem.execute("""
                INSERT INTO doc_chunks_fts (chunk_id, content, filename)
                VALUES (?, ?, 'overlord.pdf')
            """, (c["id"], c["content"]))
        db_mem.commit()
        return db_mem

    def test_trouve_par_mot_cle(self, db_with_doc):
        results = search_docs_keyword(db_with_doc, "Ainz")
        assert len(results) > 0
        assert any("Ainz" in r["content"] for r in results)

    def test_query_vide_retourne_liste_vide(self, db_with_doc):
        assert search_docs_keyword(db_with_doc, "") == []

    def test_mot_inexistant_retourne_vide(self, db_with_doc):
        results = search_docs_keyword(db_with_doc, "xyzquantumfoo")
        assert results == []

    def test_structure_resultat(self, db_with_doc):
        results = search_docs_keyword(db_with_doc, "Nazarick")
        assert len(results) > 0
        r = results[0]
        assert "id" in r
        assert "content" in r
        assert "page" in r
        assert "source" in r
        assert "score_fts" in r
        assert r["type"] == "document"

    def test_source_est_le_filename(self, db_with_doc):
        results = search_docs_keyword(db_with_doc, "Albedo")
        assert results[0]["source"] == "overlord.pdf"

    def test_top_k_respecte(self, db_with_doc):
        results = search_docs_keyword(db_with_doc, "tombeau", top_k=1)
        assert len(results) <= 1