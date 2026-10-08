"""Parse the official GDPR XHTML and eCFR XML at their legal boundaries."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, Tag

from ingest.common import write_chunks, write_json
from rag.types import Chunk

GDPR_URL = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679"
GDPR_PUBLICATIONS_URL = (
    "https://op.europa.eu/o/opportal-service/download-handler?"
    "identifier=3e485e15-11bd-11e6-ba9a-01aa75ed71a1&format=xhtml"
    "&language=en&productionSystem=cellar&part="
)
DEFINITION_SECTIONS = {"160.103", "164.103", "164.304", "164.501"}
PARSER_VERSION = "legal-v1"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")


def html_text(element: Tag) -> str:
    return normalize(element.get_text(" ", strip=True))


def parse_gdpr(html: str | bytes, source_date: str, *, validate: bool = True) -> list[Chunk]:
    # The official HTML is well-formed XHTML. XML parsing preserves all unit IDs.
    soup = BeautifulSoup(html, "xml" if b"<?xml" in (html[:100] if isinstance(html, bytes) else html[:100].encode()) else "lxml")
    articles = soup.find_all(id=re.compile(r"^art_\d+$"))
    recitals = soup.find_all(id=re.compile(r"^rct_\d+$"))
    if validate:
        if {int(a["id"].split("_")[1]) for a in articles} != set(range(1, 100)):
            raise ValueError("GDPR source must contain all 99 articles; an HTML download may be a challenge page")
        if {int(r["id"].split("_")[1]) for r in recitals} != set(range(1, 174)):
            raise ValueError("GDPR source must contain all 173 recitals")
    chunks: list[Chunk] = []
    common = {"corpus": "gdpr", "source_date": source_date, "parser_version": PARSER_VERSION}
    for recital in recitals:
        number = int(recital["id"].split("_")[1])
        chunks.append(Chunk(f"gdpr:rec{number}", html_text(recital), ["GDPR", f"Recital {number}"],
                            {**common, "unit_type": "recital", "recital": number,
                             "source_url": GDPR_URL + f"#rct_{number}", "citation_label": f"GDPR Recital {number}"}))
    for article in articles:
        number = int(article["id"].split("_")[1])
        title_element = article.find(id=f"art_{number}.tit_1")
        title = html_text(title_element) if title_element else ""
        headings = ["GDPR", f"Article {number}"] + ([title] if title else [])
        metadata = {**common, "unit_type": "article", "article": number,
                    "source_url": GDPR_URL + f"#art_{number}"}
        children = article.find_all(recursive=False)
        body = [node for node in children if not (node.get("id") == f"art_{number}.tit_1" or html_text(node) == f"Article {number}")]
        if number == 4:
            points = [node for node in body if node.name == "table"]
            introduction = "\n".join(html_text(node) for node in body if node.name != "table")
            for index, point in enumerate(points):
                text = html_text(point)
                match = re.match(r"\((\d+)\)", text)
                if not match:
                    raise ValueError("Unexpected GDPR Article 4 definition structure")
                point_number = int(match.group(1))
                # Preserve the introduction once; only HIPAA introductions repeat.
                if index == 0 and introduction:
                    text = introduction + "\n" + text
                chunks.append(Chunk(f"gdpr:art4:{point_number}", text, [*headings, f"Point {point_number}"],
                                    {**metadata, "point": point_number, "citation_label": f"GDPR Article 4({point_number})"}))
            if validate and len(points) != 26:
                raise ValueError("GDPR Article 4 must contain 26 definition points")
            continue
        paragraphs = [node for node in body if re.fullmatch(r"\d{3}\.\d{3}", node.get("id", ""))]
        if paragraphs:
            # Only direct children are visited: their nested lists stay in the paragraph.
            extras = [html_text(node) for node in body if node not in paragraphs]
            for index, paragraph in enumerate(paragraphs):
                paragraph_number = int(paragraph["id"].split(".")[1])
                text = html_text(paragraph)
                if index == 0 and extras:
                    text = "\n".join([*extras, text])
                chunks.append(Chunk(f"gdpr:art{number}:p{paragraph_number}", text,
                                    [*headings, f"Paragraph {paragraph_number}"],
                                    {**metadata, "paragraph": paragraph_number,
                                     "citation_label": f"GDPR Article {number}({paragraph_number})"}))
        else:
            text = "\n".join(html_text(node) for node in body)
            if text and not re.fullmatch(r"\[?Reserved\]?\.?", text, re.I):
                chunks.append(Chunk(f"gdpr:art{number}", text, headings,
                                    {**metadata, "citation_label": f"GDPR Article {number}"}))
    return chunks


def selected_section(number: str) -> bool:
    try:
        part, section = map(int, number.split("."))
    except ValueError:
        return False
    return (part == 160 and 101 <= section <= 105) or (
        part == 164 and any(start <= section <= end for start, end in [(102, 106), (302, 318), (500, 534)])
    )


def xml_text(element: ET.Element) -> str:
    return normalize("".join(element.itertext()))


def definition_term(paragraph: ET.Element) -> str | None:
    # eCFR uses initial italics for defined terms. Nested numbered paragraphs can
    # contain italics too, so require the term to begin the paragraph's text.
    if normalize(paragraph.text or ""):
        return None
    children = list(paragraph)
    if not children or children[0].tag != "I":
        return None
    term = xml_text(children[0]).rstrip(":.")
    return term if term else None


def parse_hipaa(xml: str | bytes, source_date: str) -> list[Chunk]:
    root = ET.fromstring(xml)
    chunks: list[Chunk] = []
    for section in root.iter():
        number = section.get("N", "")
        if section.get("TYPE") != "SECTION" or not selected_section(number):
            continue
        title_element = section.find("HEAD")
        title = xml_text(title_element) if title_element is not None else f"§ {number}"
        if "[Reserved]" in title:
            continue
        # CITA/source notes and editorial AUTH nodes are not regulation text.
        paragraphs = [node for node in section if node.tag in {"P", "FP", "GPOTABLE"}]
        units: list[tuple[str, list[str]]] = []
        introduction: list[str] = []
        definition_section = number in DEFINITION_SECTIONS
        next_top_letter = "a"
        for paragraph_index, paragraph in enumerate(paragraphs):
            text = xml_text(paragraph)
            if not text:
                continue
            term = definition_term(paragraph) if definition_section else None
            # A single letter (i) can be a nested Roman numeral. Top-level letters
            # arrive in alphabetical sequence; nested (i) follows another unit.
            reserved_range = re.fullmatch(r"\(([a-z])\)\s*[-–—]\s*\(([a-z])\)\s*\[Reserved\]\.?", text, re.I)
            if not definition_section and reserved_range:
                next_top_letter = chr(ord(reserved_range.group(2).lower()) + 1)
                continue
            expected = next_top_letter
            top = re.match(r"^\(([a-z])\)\s*", text) if not definition_section else None
            starts_numbered_children = bool(re.match(r"^\([ivx]\)\s*\(1\)", text) or re.search(r"[.—]\s*\(1\)", text))
            if top and top.group(1) in {"i", "v", "x"} and not starts_numbered_children:
                # Roman numerals at depth three overlap the top-level alphabet.
                # A following Roman sibling makes this a nested paragraph. Stop
                # when a new numeric/top-level sibling or another (i) begins.
                next_roman = {"i": "ii", "v": "vi", "x": "xi"}[top.group(1)]
                for following in paragraphs[paragraph_index + 1:]:
                    marker = re.match(r"^\(([^)]+)\)", xml_text(following))
                    if not marker or marker.group(1).isupper():
                        continue
                    if marker.group(1) == next_roman:
                        top = None
                    break
            if term:
                units.append((term, [text]))
            elif top and (top.group(1) == expected or not units and top.group(1) == "a"):
                units.append((top.group(1), [text]))
                next_top_letter = chr(ord(top.group(1)) + 1)
            elif units:
                units[-1][1].append(text)
            else:
                introduction.append(text)
        common = {"corpus": "hipaa", "source_date": source_date, "parser_version": PARSER_VERSION,
                  "section": number, "source_url": f"https://www.ecfr.gov/on/{source_date}/title-45/section-{number}"}
        headings = ["HIPAA", f"45 CFR {number}", title]
        if not units:
            text = "\n".join(introduction)
            if text and not re.fullmatch(r"\[?Reserved\]?\.?", text, re.I):
                chunks.append(Chunk(f"hipaa:{number}", text, headings,
                                    {**common, "unit_type": "section", "citation_label": f"HIPAA 45 CFR {number}"}))
            continue
        for name, content in units:
            if re.fullmatch(r"\([a-z]\)\s*\[Reserved\]\.?", " ".join(content), re.I):
                continue
            unit_id = f"hipaa:{number}:{slug(name)}" if definition_section else f"hipaa:{number}.{name}"
            label = f"HIPAA 45 CFR {number}: {name}" if definition_section else f"HIPAA 45 CFR {number}({name})"
            chunks.append(Chunk(unit_id, "\n".join([*introduction, *content]), [*headings, name],
                                {**common, "unit_type": "definition" if definition_section else "paragraph",
                                 "citation_label": label}))
    return chunks


def fetch_sources(raw_dir: Path, source_date: str) -> dict[str, Path]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources = {"gdpr": raw_dir / "gdpr.html", **{f"hipaa{part}": raw_dir / f"hipaa-{part}-{source_date}.xml" for part in (160, 164)}}
    provenance: dict[str, str] = {}
    with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "course-rag/1.0", "Accept-Encoding": "gzip, deflate"}) as client:
        for name, path in sources.items():
            if path.exists() and path.stat().st_size > 10_000:
                continue
            urls = [GDPR_URL, GDPR_PUBLICATIONS_URL] if name == "gdpr" else [
                f"https://www.ecfr.gov/api/versioner/v1/full/{source_date}/title-45.xml?part={name[5:]}"
            ]
            for url in urls:
                response = client.get(url)
                if response.status_code == 200 and len(response.content) > 10_000:
                    if name == "gdpr":
                        parse_gdpr(response.content, source_date)
                    else:
                        ET.fromstring(response.content)
                    path.write_bytes(response.content)
                    provenance[name] = url
                    break
            else:
                raise RuntimeError(f"Could not fetch official source {name}; supply its downloaded file at {path}")
    if provenance:
        write_json(raw_dir / "downloads.json", {"source_date": source_date, "urls": provenance})
    return sources


def build_legal(raw_dir: Path, output: Path, source_date: str) -> int:
    sources = fetch_sources(raw_dir, source_date)
    chunks = parse_gdpr(sources["gdpr"].read_bytes(), source_date)
    for part in (160, 164):
        chunks.extend(parse_hipaa(sources[f"hipaa{part}"].read_bytes(), source_date))
    return write_chunks(output, chunks)
