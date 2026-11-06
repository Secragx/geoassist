"""Chunking por estructura para manuales Markdown.

El chunker conserva la estructura del documento: agrupa párrafos, tablas y
bloques de código en fragmentos que respetan ``max_tokens``, mantiene un
breadcrumb de encabezados H1-H4 y agrega solape entre fragmentos consecutivos.
No conoce el tokenizer concreto: recibe cualquier objeto que implemente
``TokenCounter``.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from geoassist.ingest.rules import normalize_text

if TYPE_CHECKING:
    from geoassist.rag.tokenization import TokenCounter

BOILERPLATE_HEADINGS: tuple[str, ...] = ("Elaboración, Revisión y Aprobación",)

_HEADING_RE = re.compile(r"^ {0,3}(#{1,4})[ \t]+(.+?)[ \t]*$")
_CLOSING_HASHES_RE = re.compile(r"[ \t]+#+[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")

_TEXT = "text"
_TABLE = "table"
_CODE = "code"


@dataclass(frozen=True, slots=True)
class TextChunk:
    """Fragmento listo para almacenar, embeber y citar."""

    ordinal: int
    heading: str
    text: str
    token_count: int
    has_images: bool


@dataclass(frozen=True, slots=True)
class _Block:
    """Bloque estructural con el encabezado vigente al que pertenece."""

    kind: str
    text: str
    heading: str
    level: int
    parent: str
    token_count: int


@dataclass(slots=True)
class _Draft:
    """Fragmento intermedio antes de asignar ordinales."""

    kind: str
    heading: str
    level: int
    parent: str
    text: str
    token_count: int
    split: bool = False


class Chunker:
    """Parte un manual Markdown por estructura, con solape configurable."""

    def __init__(
        self,
        counter: TokenCounter,
        target_tokens: int = 450,
        max_tokens: int = 600,
        min_tokens: int = 80,
        overlap_ratio: float = 0.12,
        boilerplate_headings: Collection[str] = BOILERPLATE_HEADINGS,
    ) -> None:
        if target_tokens < 1:
            raise ValueError("target_tokens debe ser mayor que cero")
        if max_tokens < 1:
            raise ValueError("max_tokens debe ser mayor que cero")
        if min_tokens < 0:
            raise ValueError("min_tokens no puede ser negativo")
        if not 0 <= overlap_ratio < 1:
            raise ValueError("overlap_ratio debe estar en [0, 1)")

        self._counter = counter
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens
        self._min_tokens = min_tokens
        self._overlap_ratio = overlap_ratio
        self._boilerplate = frozenset(
            normalize_text(heading).rstrip(" .:") for heading in boilerplate_headings
        )

    def chunk(self, markdown: str, title: str) -> list[TextChunk]:
        """Devuelve los fragmentos del manual en orden de aparición."""

        blocks = self._parse_blocks(markdown, title)
        groups = self._merge_blocks(blocks)
        drafts: list[_Draft] = []
        for group in groups:
            drafts.extend(self._draft_group(group))
        drafts = self._merge_short_siblings(drafts)

        chunks: list[TextChunk] = []
        for draft in drafts:
            text = draft.text.strip()
            if not text:
                continue
            chunks.append(
                TextChunk(
                    ordinal=len(chunks),
                    heading=draft.heading,
                    text=text,
                    token_count=self._counter.count(text),
                    has_images="![" in text,
                )
            )
        return chunks

    def _parse_blocks(self, markdown: str, title: str) -> list[_Block]:
        lines = markdown.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        stack: list[tuple[int, str]] = []
        blocks: list[_Block] = []
        skip_level: int | None = None
        index = 0

        while index < len(lines):
            line = lines[index]
            if not line.strip():
                index += 1
                continue

            heading = _match_heading(line)
            if heading is not None:
                level, text = heading
                if skip_level is not None and level > skip_level:
                    index += 1
                    continue
                if skip_level is not None:
                    skip_level = None
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, text))
                if _heading_key(text) in self._boilerplate:
                    skip_level = level
                index += 1
                continue

            if skip_level is not None:
                index += 1
                continue

            fence = _match_fence(line)
            if fence is not None:
                block_lines, index = _read_code_block(lines, index, fence)
                blocks.append(self._make_block(_CODE, block_lines, stack, title))
                continue

            if _is_table_line(line):
                table_lines: list[str] = []
                while index < len(lines) and _is_table_line(lines[index]):
                    table_lines.append(lines[index])
                    index += 1
                blocks.append(self._make_block(_TABLE, table_lines, stack, title))
                continue

            paragraph_lines: list[str] = []
            while index < len(lines):
                candidate = lines[index]
                if not candidate.strip():
                    break
                if _match_heading(candidate) is not None:
                    break
                if _match_fence(candidate) is not None:
                    break
                if _is_table_line(candidate):
                    break
                paragraph_lines.append(candidate)
                index += 1
            blocks.append(self._make_block(_TEXT, paragraph_lines, stack, title))

        return blocks

    def _make_block(
        self,
        kind: str,
        lines: Iterable[str],
        stack: list[tuple[int, str]],
        title: str,
    ) -> _Block:
        text = "\n".join(lines).strip()
        heading, level, parent = _breadcrumb(stack, title)
        return _Block(
            kind=kind,
            text=text,
            heading=heading,
            level=level,
            parent=parent,
            token_count=self._counter.count(text),
        )

    def _merge_blocks(self, blocks: list[_Block]) -> list[list[_Block]]:
        """Fusiona bloques de texto cortos que comparten el mismo encabezado."""

        groups: list[list[_Block]] = []
        current: list[_Block] = []

        def flush() -> None:
            nonlocal current
            if current:
                groups.append(current)
                current = []

        for block in blocks:
            if block.kind != _TEXT:
                flush()
                groups.append([block])
                continue
            if not current:
                current = [block]
                continue
            same_heading = current[0].heading == block.heading
            current_tokens = self._counter.count("\n\n".join(item.text for item in current))
            if (
                same_heading
                and current_tokens < self._min_tokens
                and block.token_count < self._min_tokens
                and current_tokens + block.token_count <= self._max_tokens
            ):
                current.append(block)
            else:
                flush()
                current = [block]
        flush()
        return groups

    def _draft_group(self, group: list[_Block]) -> list[_Draft]:
        first = group[0]
        text = "\n\n".join(block.text for block in group)
        token_count = self._counter.count(text)
        if token_count <= self._max_tokens:
            return [
                _Draft(
                    kind=first.kind,
                    heading=first.heading,
                    level=first.level,
                    parent=first.parent,
                    text=text,
                    token_count=token_count,
                )
            ]

        if first.kind == _TABLE:
            parts = self._split_table(text)
        elif first.kind == _CODE:
            parts = self._split_code(text)
        else:
            parts = self._split_words(text)
        return [
            _Draft(
                kind=first.kind,
                heading=first.heading,
                level=first.level,
                parent=first.parent,
                text=part,
                token_count=self._counter.count(part),
                split=True,
            )
            for part in parts
        ]

    def _split_words(self, text: str) -> list[str]:
        """Parte un bloque de texto por palabras respetando ``max_tokens``."""

        words = text.split()
        if not words:
            return []
        limit, overlap = self._window()
        parts: list[list[str]] = []
        start = 0
        while start < len(words):
            span = min(limit, len(words) - start)
            span = self._largest_fitting_span(words, start, span)
            end = start + span
            parts.append(words[start:end])
            if end >= len(words):
                break
            effective_overlap = min(overlap, span - 1)
            start = end - effective_overlap
        return [" ".join(part) for part in parts]

    def _largest_fitting_span(
        self,
        words: list[str],
        start: int,
        span: int,
        limit: int | None = None,
    ) -> int:
        """Reduce ``span`` si el conteo real de tokens supera el límite."""

        max_tokens = self._max_tokens if limit is None else limit
        best = 1
        low, high = 1, span
        while low <= high:
            middle = (low + high) // 2
            candidate = " ".join(words[start : start + middle])
            if self._counter.count(candidate) <= max_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        return best

    def _split_table(self, text: str) -> list[str]:
        """Parte una tabla repitiendo su encabezado en cada fragmento."""

        lines = text.splitlines()
        if not lines:
            return []
        header_count = 1
        if len(lines) > 1 and _TABLE_SEPARATOR_RE.match(lines[1]):
            header_count = 2
        header = lines[:header_count]
        rows = lines[header_count:]
        if not rows:
            return [text]

        header_text = "\n".join(header)
        header_tokens = self._counter.count(header_text)
        if header_tokens >= self._max_tokens:
            return self._split_words(text)

        body_limit = self._max_tokens - header_tokens
        parts: list[str] = []
        current_rows: list[str] = []

        def flush() -> None:
            nonlocal current_rows
            if current_rows:
                parts.append("\n".join([*header, *current_rows]))
                current_rows = []

        for row in rows:
            if self._counter.count("\n".join([*header, row])) > self._max_tokens:
                flush()
                for piece in self._split_row(row, body_limit):
                    parts.append("\n".join([*header, piece]))
                continue
            candidate_rows = [*current_rows, row]
            candidate = "\n".join([*header, *candidate_rows])
            if current_rows and self._counter.count(candidate) > self._max_tokens:
                flush()
                current_rows = [row]
            else:
                current_rows = candidate_rows
        flush()
        return parts

    def _split_row(self, row: str, limit: int) -> list[str]:
        words = row.split()
        if not words:
            return [row]
        parts: list[str] = []
        start = 0
        while start < len(words):
            span = min(limit, len(words) - start)
            span = self._largest_fitting_span(words, start, span, limit)
            parts.append(" ".join(words[start : start + span]))
            start += span
        return parts

    def _split_code(self, text: str) -> list[str]:
        """Parte un bloque de código solo cuando excede el máximo."""

        lines = text.splitlines()
        if len(lines) < 2:
            return self._split_words(text)

        opening = lines[0]
        opening_fence = _match_fence(opening)
        if opening_fence is None:
            return self._split_words(text)
        closing_fence = _match_fence(lines[-1])
        has_closing = closing_fence is not None and closing_fence[0] == opening_fence[0]
        closing = lines[-1] if has_closing else ""
        body = lines[1:-1] if has_closing else lines[1:]
        wrapper = "\n".join([opening, closing]) if has_closing else opening
        wrapper_tokens = self._counter.count(wrapper)
        if wrapper_tokens >= self._max_tokens:
            return self._split_words(text)
        body_limit = self._max_tokens - wrapper_tokens

        parts: list[str] = []
        current: list[str] = []

        def render(body_lines: list[str]) -> str:
            if has_closing:
                return "\n".join([opening, *body_lines, closing])
            return "\n".join([opening, *body_lines])

        def flush() -> None:
            nonlocal current
            if current:
                parts.append(render(current))
                current = []

        for line in body:
            candidate = render([*current, line])
            if current and self._counter.count(candidate) > self._max_tokens:
                flush()
                current = [line]
            elif not current and self._counter.count(candidate) > self._max_tokens:
                for piece in self._split_row(line, body_limit):
                    parts.append(render([piece]))
            else:
                current.append(line)
        flush()
        return parts

    def _merge_short_siblings(self, drafts: list[_Draft]) -> list[_Draft]:
        """Une secciones hermanas cortas para no indexar fragmentos mínimos."""

        merged: list[_Draft] = []
        for draft in drafts:
            if not merged:
                merged.append(draft)
                continue
            previous = merged[-1]
            can_merge = (
                previous.kind == _TEXT
                and draft.kind == _TEXT
                and not previous.split
                and not draft.split
                and previous.heading != draft.heading
                and previous.level == draft.level
                and bool(previous.parent)
                and previous.parent == draft.parent
                and previous.token_count < self._min_tokens
                and draft.token_count < self._min_tokens
            )
            if not can_merge:
                merged.append(draft)
                continue
            text = f"{previous.text}\n\n{draft.text}"
            token_count = self._counter.count(text)
            if token_count > self._max_tokens:
                merged.append(draft)
                continue
            previous.text = text
            previous.token_count = token_count
            previous.heading = previous.parent
        return merged

    def _window(self) -> tuple[int, int]:
        limit = max(1, min(self._target_tokens, self._max_tokens))
        overlap = int(self._overlap_ratio * self._target_tokens)
        overlap = min(max(overlap, 0), limit - 1)
        return limit, overlap


def _match_heading(line: str) -> tuple[int, str] | None:
    match = _HEADING_RE.match(line)
    if match is None:
        return None
    text = _CLOSING_HASHES_RE.sub("", match.group(2)).strip()
    if not text:
        return None
    return len(match.group(1)), text


def _match_fence(line: str) -> tuple[str, int] | None:
    match = _FENCE_RE.match(line)
    if match is None:
        return None
    marker = match.group(1)
    return marker[0], len(marker)


def _read_code_block(
    lines: list[str],
    start: int,
    fence: tuple[str, int],
) -> tuple[list[str], int]:
    char, length = fence
    collected = [lines[start]]
    index = start + 1
    while index < len(lines):
        collected.append(lines[index])
        closing = _match_fence(lines[index])
        if closing is not None and closing[0] == char and closing[1] >= length:
            index += 1
            break
        index += 1
    return collected, index


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def _heading_key(text: str) -> str:
    return normalize_text(text).rstrip(" .:")


def _breadcrumb(stack: list[tuple[int, str]], title: str) -> tuple[str, int, str]:
    if not stack:
        return title, 0, ""
    texts = [text for _, text in stack]
    heading = " > ".join(texts)
    parent = " > ".join(texts[:-1])
    return heading, stack[-1][0], parent
