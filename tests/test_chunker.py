from geoassist.ingest.chunker import Chunker


class WordCounter:
    def count(self, text):
        return len(text.split())


def test_heading_breadcrumb_and_ordinals():
    md = "# Manual\n\ntexto uno\n\n## Sección A\n\ntexto dos\n"
    chunks = Chunker(WordCounter()).chunk(md, title="Manual")
    assert chunks[0].heading == "Manual"
    assert chunks[1].heading == "Manual > Sección A"
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_long_paragraph_is_split_and_overlaps():
    words = [f"w{i}" for i in range(1200)]
    md = "# T\n\n" + " ".join(words)
    chunks = Chunker(WordCounter(), max_tokens=600, target_tokens=450).chunk(md, title="T")
    assert len(chunks) >= 3
    assert all(c.token_count <= 600 for c in chunks)
    first = set(chunks[0].text.split()[-60:])
    second = set(chunks[1].text.split()[:60])
    assert len(first & second) >= 40


def test_short_sections_are_merged():
    md = "# T\n\n## A\n\ncorto\n\n## B\n\ncorto dos\n"
    chunks = Chunker(WordCounter(), min_tokens=80).chunk(md, title="T")
    assert len(chunks) == 1


def test_table_is_kept_whole_when_it_fits():
    rows = "\n".join(f"| fila {i} | valor {i} |" for i in range(10))
    md = f"# T\n\n| col | val |\n|---|---|\n{rows}\n"
    chunks = Chunker(WordCounter()).chunk(md, title="T")
    assert len(chunks) == 1
    assert "fila 9" in chunks[0].text


def test_long_table_repeats_header():
    rows = "\n".join(f"| fila {i} | valor {i} |" for i in range(200))
    md = f"# T\n\n| col | val |\n|---|---|\n{rows}\n"
    chunks = Chunker(WordCounter(), max_tokens=120).chunk(md, title="T")
    assert len(chunks) > 1
    assert all("| col | val |" in c.text for c in chunks)


def test_code_block_is_not_split_when_it_fits():
    code = "```sql\n" + "\n".join(f"SELECT {i};" for i in range(10)) + "\n```"
    chunks = Chunker(WordCounter()).chunk(f"# T\n\n{code}\n", title="T")
    assert len(chunks) == 1
    assert "SELECT 9;" in chunks[0].text


def test_boilerplate_section_is_skipped():
    md = "# T\n\n## Elaboración, Revisión y Aprobación\n\n| a | b |\n\n# Objeto\n\ncontenido útil\n"
    chunks = Chunker(WordCounter()).chunk(md, title="T")
    assert all("Elaboración" not in c.text for c in chunks)


def test_image_alt_is_kept_and_flagged():
    md = "# T\n\n![Pantallazo del error](SopPro_img1.png)\n"
    chunk = Chunker(WordCounter()).chunk(md, title="T")[0]
    assert "Pantallazo del error" in chunk.text
    assert chunk.has_images is True


def test_oversized_code_line_is_split_and_keeps_fences():
    long_line = " ".join(f"w{i}" for i in range(100))
    body = f"SELECT 1;\n{long_line}\nSELECT 2;"
    code = f"```sql\n{body}\n```"
    chunks = Chunker(WordCounter(), max_tokens=30, target_tokens=20).chunk(
        f"# T\n\n{code}\n", title="T"
    )
    assert len(chunks) > 1
    assert all(chunk.token_count <= 30 for chunk in chunks)
    assert all(chunk.text.startswith("```sql") for chunk in chunks)
    assert all(chunk.text.endswith("```") for chunk in chunks)


def test_short_paragraph_merges_into_following_long_paragraph():
    long_paragraph = " ".join(f"w{i}" for i in range(200))
    md = f"# T\n\nshort\n\n{long_paragraph}\n"
    chunks = Chunker(WordCounter(), min_tokens=80, max_tokens=600).chunk(md, title="T")
    assert chunks[0].token_count <= 600
    assert all(
        chunk.token_count >= 80 or index == len(chunks) - 1 for index, chunk in enumerate(chunks)
    )
    assert chunks[0].text.startswith("short")
