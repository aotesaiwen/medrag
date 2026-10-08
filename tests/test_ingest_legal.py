import pytest

from ingest.legal import parse_gdpr, parse_hipaa, selected_section, slug


def test_gdpr_paragraph_keeps_nested_points_without_parent_chunk():
    html = '''<html><div id="art_17"><p>Article 17</p>
      <div id="art_17.tit_1"><p>Right to erasure</p></div>
      <div id="017.001"><p>1. Erase personal data.</p><table><tr><td>(a)</td><td>Consent withdrawn.</td></tr></table></div>
      <div id="017.002"><p>2. Inform controllers.</p></div></div></html>'''
    chunks = parse_gdpr(html, "2026-10-01", validate=False)
    assert [c.id for c in chunks] == ["gdpr:art17:p1", "gdpr:art17:p2"]
    assert "Consent withdrawn." in chunks[0].text
    assert "Consent withdrawn." not in chunks[1].text
    assert "Right to erasure" in chunks[0].heading_path


def test_gdpr_article_four_definitions_and_introduction_stored_once():
    html = '''<html><div id="art_4"><p>Article 4</p><div id="art_4.tit_1">Definitions</div>
    <p>For the purposes of this Regulation:</p>
    <table><tr><td>(1)</td><td>Personal data means information.</td></tr></table>
    <table><tr><td>(2)</td><td>Processing means operations.</td></tr></table></div></html>'''
    chunks = parse_gdpr(html, "2026-10-01", validate=False)
    assert [c.id for c in chunks] == ["gdpr:art4:1", "gdpr:art4:2"]
    assert "For the purposes" in chunks[0].text
    assert "For the purposes" not in chunks[1].text


def test_gdpr_unnumbered_article_and_recital():
    html = '''<html><div id="rct_65">(65) Explanation.</div><div id="art_16"><p>Article 16</p>
    <div id="art_16.tit_1">Rectification</div><p>Correct inaccurate data.</p><p>Complete incomplete data.</p></div></html>'''
    chunks = parse_gdpr(html, "2026-10-01", validate=False)
    assert [c.id for c in chunks] == ["gdpr:rec65", "gdpr:art16"]
    assert "Complete incomplete data." in chunks[1].text
    assert chunks[0].metadata["unit_type"] == "recital"


def test_gdpr_rejects_missing_articles():
    with pytest.raises(ValueError, match="99 articles"):
        parse_gdpr("<html>Access challenge</html>", "2026-10-01")


def test_hipaa_paragraphs_repeat_introduction_but_preserve_nested_text():
    xml = '''<DIV5><DIV8 N="164.526" TYPE="SECTION"><HEAD>Amendment.</HEAD>
    <P>Introductory scope.</P><P>(a) Right to amend.</P><P>(1) First condition.</P>
    <P>(i) Nested limitation.</P><P>(ii) Second limitation.</P><P>(b) Timely action.</P>
    <CITA>Editorial citation not regulation text.</CITA></DIV8></DIV5>'''
    chunks = parse_hipaa(xml, "2026-10-01")
    assert [c.id for c in chunks] == ["hipaa:164.526.a", "hipaa:164.526.b"]
    assert all(c.text.startswith("Introductory scope.") for c in chunks)
    assert "Nested limitation." in chunks[0].text
    assert "Editorial citation" not in chunks[0].text


def test_hipaa_definitions_group_nested_lists_and_slug_terms():
    xml = '''<DIV8 N="160.103" TYPE="SECTION"><HEAD>Definitions.</HEAD>
    <P>For this part:</P><P><I>Business associate</I> means a person.</P><P>(1) One kind.</P>
    <P>(i) Nested kind.</P><P><I>Covered entity</I> means a provider.</P></DIV8>'''
    chunks = parse_hipaa(xml, "2026-10-01")
    assert [c.id for c in chunks] == ["hipaa:160.103:business-associate", "hipaa:160.103:covered-entity"]
    assert "Nested kind." in chunks[0].text
    assert chunks[1].text.startswith("For this part:")
    assert slug(" Security / Security measures ") == "security-security-measures"


def test_hipaa_roman_i_under_h_does_not_become_top_level_i():
    paragraphs = "".join(f"<P>({letter}) Unit {letter}.</P>" for letter in "abcdefgh")
    xml = f'''<DIV8 N="164.514" TYPE="SECTION"><HEAD>Other requirements.</HEAD>{paragraphs}
    <P>(1) Verification.</P><P>(i) Verify identity.</P><P>(ii) Verify authority.</P></DIV8>'''
    chunks = parse_hipaa(xml, "2026-10-01")
    assert len(chunks) == 8
    assert chunks[-1].id == "hipaa:164.514.h"
    assert "Verify identity" in chunks[-1].text


def test_hipaa_top_level_i_can_itself_contain_roman_children():
    paragraphs = "".join(f"<P>({letter}) Unit {letter}.</P>" for letter in "abcdefgh")
    xml = f'''<DIV8 N="164.512" TYPE="SECTION"><HEAD>Research.</HEAD>{paragraphs}
    <P>(i) Research—(1) Permitted uses.</P><P>(i) Approval.</P><P>(ii) Preparatory research.</P>
    <P>(j) Threats.</P></DIV8>'''
    chunks = parse_hipaa(xml, "2026-10-01")
    assert [c.id.rsplit(".", 1)[-1] for c in chunks] == list("abcdefghij")
    assert "Preparatory research" in chunks[-2].text


def test_hipaa_reserved_units_omitted_and_unlettered_section_kept():
    xml = '''<DIV5><DIV8 N="160.104" TYPE="SECTION"><HEAD>[Reserved]</HEAD></DIV8>
    <DIV8 N="160.102" TYPE="SECTION"><HEAD>Applicability.</HEAD><P>Applies here.</P></DIV8>
    <DIV8 N="164.526" TYPE="SECTION"><HEAD>Rights.</HEAD><P>(a) Right.</P><P>(b) [Reserved]</P><P>(c) Other right.</P></DIV8>
    <DIV8 N="164.400" TYPE="SECTION"><HEAD>Out of scope.</HEAD><P>Excluded.</P></DIV8></DIV5>'''
    assert [c.id for c in parse_hipaa(xml, "2026-10-01")] == ["hipaa:160.102", "hipaa:164.526.a", "hipaa:164.526.c"]


def test_hipaa_reserved_range_does_not_swallow_later_units():
    xml = '''<DIV8 N="164.504" TYPE="SECTION"><HEAD>Organizational requirements.</HEAD>
    <P>(a) Definitions.</P><P>(b)-(d) [Reserved]</P><P>(e)(1) Business associate contracts.</P>
    <P>(i) Permitted uses.</P><P>(ii) Required safeguards.</P><P>(f) Group health plans.</P></DIV8>'''
    chunks = parse_hipaa(xml, "2026-10-01")
    assert [c.id for c in chunks] == ["hipaa:164.504.a", "hipaa:164.504.e", "hipaa:164.504.f"]
    assert "Required safeguards" in chunks[1].text
    assert "Reserved" not in " ".join(c.text for c in chunks)


def test_top_level_i_with_inline_numeric_and_roman_child():
    paragraphs = "".join(f"<P>({letter}) Unit {letter}.</P>" for letter in "abcdefgh")
    xml = f'''<DIV8 N="164.512" TYPE="SECTION"><HEAD>Research.</HEAD>{paragraphs}
    <P>(i)(1) Research. (i) First condition.</P><P>(ii) Second condition.</P><P>(j) Next unit.</P></DIV8>'''
    chunks = parse_hipaa(xml, "2026-10-01")
    assert chunks[-2].id == "hipaa:164.512.i"
    assert "Second condition" in chunks[-2].text


@pytest.mark.parametrize("section,expected", [("160.101", True), ("160.106", False), ("164.318", True), ("164.319", False), ("164.534", True), ("164.535", False)])
def test_hipaa_section_selection(section, expected):
    assert selected_section(section) is expected
