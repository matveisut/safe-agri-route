#!/usr/bin/env python3
"""Build diploma DOCX files from LaTeX with deterministic Word cleanup.

Pandoc is still used as the general LaTeX-to-DOCX converter, but LaTeX
``longtable`` environments are removed from the temporary input before Pandoc
sees them. The script then inserts native Word tables at explicit placeholders,
which avoids Pandoc's DOCX table garbage for computed column specs.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


BASE_DIR = Path(__file__).resolve().parent
LATEX_DIR = BASE_DIR / "latex"
DOCX_DIR = BASE_DIR / "docx"

DOCS = {
    "formal": ("formal.tex", "formal_parts.docx"),
    "main": ("main.tex", "diploma_main_part.docx"),
    "appendices": ("appendices.tex", "appendices.docx"),
}

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"w": W_NS, "r": R_NS}
FONT = "Times New Roman"
FONT_SIZE = "28"  # 14 pt in half-points
FONT_SIZE_TABLE_SMALL = "26"
CONTENT_WIDTH = "9638"
FIRST_LINE_INDENT = "709"   # 1.25 cm in twips
BODY_LINE_SPACING = "360"   # 1.5 lines
TABLE_LINE_SPACING = "240"  # single line inside table cells
LIST_INDENT = "0"           # left=0: continuation wraps to left margin (same as body text)
# list firstLine reuses FIRST_LINE_INDENT (709): dash sits at the paragraph indent position
HEADING_SPACE_BEFORE = "240"  # 12 pt before heading (one single-spaced line)
HEADING_SPACE_AFTER = "120"   # 6 pt after heading
FOOTER_DISTANCE = "709"       # 1.25 cm from bottom page edge to footer


@dataclass(frozen=True)
class ExtractedTable:
    marker: str
    rows: list[list[str]]


def q(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def set_attr(element: ET.Element, name: str, value: str | int) -> None:
    element.set(q(name), str(value))


def child(parent: ET.Element, tag: str, *, first: bool = False) -> ET.Element:
    found = parent.find(f"w:{tag}", NS)
    if found is not None:
        return found
    created = ET.Element(q(tag))
    if first:
        parent.insert(0, created)
    else:
        parent.append(created)
    return created


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()


def paragraph_style(paragraph: ET.Element) -> str:
    style = paragraph.find("w:pPr/w:pStyle", NS)
    return style.get(q("val"), "") if style is not None else ""


def strip_latex_commands(text: str) -> str:
    """Preserve command arguments while removing LaTeX command syntax.

    The table cells in these documents often contain nested commands like
    ``\texttt{Geometry(\textquotesingle{}POLYGON...)}``. Regex-only cleanup can
    drop nested arguments, so this scanner keeps balanced-brace content.
    """
    result: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue

        # Escaped punctuation should become the punctuation itself.
        if index + 1 < len(text) and not text[index + 1].isalpha():
            result.append(text[index + 1])
            index += 2
            continue

        command_match = re.match(r"\\[a-zA-Z]+\*?", text[index:])
        if command_match is None:
            index += 1
            continue
        command = command_match.group(0)
        index += len(command)

        # Drop optional arguments such as [b].
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == "[":
            depth = 1
            index += 1
            while index < len(text) and depth:
                if text[index] == "[":
                    depth += 1
                elif text[index] == "]":
                    depth -= 1
                index += 1

        # Keep all balanced brace arguments as plain text.
        kept_argument = False
        while index < len(text) and text[index].isspace():
            index += 1
        while index < len(text) and text[index] == "{":
            depth = 1
            index += 1
            start = index
            while index < len(text) and depth:
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                index += 1
            argument = text[start : index - 1] if depth == 0 else text[start:index]
            result.append(strip_latex_commands(argument))
            kept_argument = True
            while index < len(text) and text[index].isspace():
                index += 1

        if command in (r"\times", r"\le", r"\ge", r"\cdot", r"\textbar", r"\textasciitilde"):
            symbol_map = {
                r"\times": "x",
                r"\le": "<=",
                r"\ge": ">=",
                r"\cdot": "*",
                r"\textbar": "|",
                r"\textasciitilde": "~",
            }
            result.append(symbol_map[command])
        elif not kept_argument and command in (r"\%", r"\_", r"\#", r"\&"):
            result.append(command[-1])

    return "".join(result)


def cleanup_latex_text(text: str) -> str:
    """Convert the small LaTeX subset used in table cells to plain text."""
    text = text.strip()
    text = re.sub(
        r"\\begin\{minipage\}(?:\[[^\]]*\])?\{[^{}]*\}\\raggedright\s*",
        "",
        text,
    )
    text = text.replace(r"\end{minipage}", "")

    replacements = {
        r"\%": "%",
        r"\_": "_",
        r"\{": "{",
        r"\}": "}",
        r"\#": "#",
        r"\&": "&",
        r"\textbar{}": "|",
        r"\textbar": "|",
        r"\textasciitilde{}": "~",
        r"\textasciitilde": "~",
        r"\times": "x",
        r"\le": "<=",
        r"\ge": ">=",
        r"\cdot": "*",
        "{[}": "[",
        "{]}": "]",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = text.replace("---", "-").replace("--", "-")
    text = strip_latex_commands(text)
    text = text.replace("$", "")
    text = re.sub(r"[{}]", "", text)
    return normalize_space(text)


def split_latex_row(row: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in row:
        if char == "&" and not escaped:
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    cells.append("".join(current))
    return [cleanup_latex_text(cell) for cell in cells]


def parse_longtable_rows(raw_body: str) -> list[list[str]]:
    content = raw_body
    first_rule = content.find(r"\toprule")
    if first_rule >= 0:
        content = content[first_rule:]

    content = re.sub(r"\\(toprule|midrule|bottomrule)\\noalign\{\}", "", content)
    content = re.sub(r"\\(toprule|midrule|bottomrule)", "", content)
    content = content.replace(r"\endhead", "").replace(r"\endlastfoot", "")

    rows: list[list[str]] = []
    for raw_row in re.split(r"\\\\\s*(?:\n|$)", content):
        raw_row = raw_row.strip()
        if not raw_row:
            continue
        if "tabcolsep" in raw_row or raw_row.startswith("[]"):
            continue
        cells = split_latex_row(raw_row)
        if any(cells):
            rows.append(cells)

    max_columns = max((len(row) for row in rows), default=0)
    return [row + [""] * (max_columns - len(row)) for row in rows]


def extract_tables(tex: str, doc_key: str) -> tuple[str, list[ExtractedTable]]:
    tables: list[ExtractedTable] = []
    pattern = re.compile(r"\\begin\{longtable\}.*?\n(.*?)\\end\{longtable\}", re.S)

    def replace(match: re.Match[str]) -> str:
        marker = f"__DOCX_TABLE_{doc_key.upper()}_{len(tables) + 1:03d}__"
        tables.append(ExtractedTable(marker=marker, rows=parse_longtable_rows(match.group(1))))
        return f"\n\n{marker}\n\n"

    return pattern.sub(replace, tex), tables


def run_pandoc(input_tex: Path, output_docx: Path) -> None:
    command = [
        "pandoc",
        str(input_tex),
        "-o",
        str(output_docx),
        f"--resource-path={LATEX_DIR}:{BASE_DIR}:{BASE_DIR / 'sources'}",
    ]
    subprocess.run(command, cwd=LATEX_DIR, check=True)


def make_paragraph(text: str, *, size: str = FONT_SIZE, spacing: str = TABLE_LINE_SPACING) -> ET.Element:
    paragraph = ET.Element(q("p"))
    paragraph_properties = ET.SubElement(paragraph, q("pPr"))
    justification = ET.SubElement(paragraph_properties, q("jc"))
    set_attr(justification, "val", "both")
    spacing_element = ET.SubElement(paragraph_properties, q("spacing"))
    set_attr(spacing_element, "line", spacing)
    set_attr(spacing_element, "lineRule", "auto")
    set_attr(spacing_element, "before", "0")
    set_attr(spacing_element, "after", "0")

    run = ET.SubElement(paragraph, q("r"))
    run_properties = ET.SubElement(run, q("rPr"))
    apply_run_formatting(run_properties)
    for tag in ("sz", "szCs"):
        set_attr(child(run_properties, tag), "val", size)
    text_element = ET.SubElement(run, q("t"))
    text_element.text = text
    set_attr(text_element, "space", "preserve")
    return paragraph


def apply_table_borders(table_properties: ET.Element) -> None:
    borders = child(table_properties, "tblBorders")
    for name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = child(borders, name)
        set_attr(border, "val", "single")
        set_attr(border, "sz", "4")
        set_attr(border, "space", "0")
        set_attr(border, "color", "000000")


def make_table(rows: list[list[str]]) -> ET.Element:
    table = ET.Element(q("tbl"))
    table_properties = ET.SubElement(table, q("tblPr"))
    table_width = ET.SubElement(table_properties, q("tblW"))
    set_attr(table_width, "w", CONTENT_WIDTH)
    set_attr(table_width, "type", "dxa")
    layout = ET.SubElement(table_properties, q("tblLayout"))
    set_attr(layout, "type", "fixed")
    apply_table_borders(table_properties)

    if not rows:
        return table

    columns = max(len(row) for row in rows)
    column_width = str(max(850, int(CONTENT_WIDTH) // columns))
    grid = ET.SubElement(table, q("tblGrid"))
    for _ in range(columns):
        column = ET.SubElement(grid, q("gridCol"))
        set_attr(column, "w", column_width)

    for row in rows:
        table_row = ET.SubElement(table, q("tr"))
        for cell_text in row:
            cell = ET.SubElement(table_row, q("tc"))
            cell_properties = ET.SubElement(cell, q("tcPr"))
            cell_width = ET.SubElement(cell_properties, q("tcW"))
            set_attr(cell_width, "w", column_width)
            set_attr(cell_width, "type", "dxa")

            margins = ET.SubElement(cell_properties, q("tcMar"))
            for side in ("top", "left", "bottom", "right"):
                margin = ET.SubElement(margins, q(side))
                set_attr(margin, "w", "80")
                set_attr(margin, "type", "dxa")

            size = FONT_SIZE_TABLE_SMALL if len(cell_text) > 55 else FONT_SIZE
            cell.append(make_paragraph(cell_text, size=size))
    return table


def apply_run_formatting(
    run_properties: ET.Element, *, no_underline: bool = False, bold: bool = False
) -> None:
    for tag in ("b", "bCs", "i", "iCs"):
        for node in list(run_properties.findall(f"w:{tag}", NS)):
            run_properties.remove(node)

    fonts = child(run_properties, "rFonts", first=True)
    for attr in ("ascii", "hAnsi", "cs", "eastAsia"):
        set_attr(fonts, attr, FONT)
    set_attr(child(run_properties, "sz"), "val", FONT_SIZE)
    set_attr(child(run_properties, "szCs"), "val", FONT_SIZE)
    set_attr(child(run_properties, "color"), "val", "000000")
    if no_underline:
        set_attr(child(run_properties, "u"), "val", "none")
    if bold:
        run_properties.insert(0, ET.Element(q("bCs")))
        run_properties.insert(0, ET.Element(q("b")))


def set_paragraph_spacing(paragraph_properties: ET.Element, line: str) -> None:
    spacing = child(paragraph_properties, "spacing")
    set_attr(spacing, "line", line)
    set_attr(spacing, "lineRule", "auto")
    set_attr(spacing, "before", "0")
    set_attr(spacing, "after", "0")


def set_heading_spacing(paragraph_properties: ET.Element) -> None:
    spacing = child(paragraph_properties, "spacing")
    set_attr(spacing, "line", BODY_LINE_SPACING)
    set_attr(spacing, "lineRule", "auto")
    set_attr(spacing, "before", HEADING_SPACE_BEFORE)
    set_attr(spacing, "after", HEADING_SPACE_AFTER)


def remove_all_bold_italic(root: ET.Element) -> None:
    # Preserve bold inside heading paragraphs (bold is a ГОСТ requirement for headings)
    heading_elements: set[int] = set()
    for p in root.findall(".//w:p", NS):
        if paragraph_style(p).startswith("Heading"):
            for elem in p.iter():
                heading_elements.add(id(elem))
    for elem in root.iter():
        if id(elem) in heading_elements:
            continue
        for tag in ("b", "bCs", "i", "iCs"):
            for node in list(elem.findall(f"w:{tag}", NS)):
                elem.remove(node)


def replace_table_markers(root: ET.Element, tables: list[ExtractedTable]) -> None:
    body = root.find("w:body", NS)
    if body is None:
        raise RuntimeError("word/document.xml has no body")

    by_marker = {table.marker: table for table in tables}
    for element in list(body):
        if element.tag != q("p"):
            continue
        paragraph = normalize_space(paragraph_text(element))
        matched_marker = next((marker for marker in by_marker if marker in paragraph), "")
        if not matched_marker:
            continue
        table = by_marker[matched_marker]
        index = list(body).index(element)
        body.remove(element)
        body.insert(index, make_table(table.rows))


def patch_document_xml(root: ET.Element, tables: list[ExtractedTable], docx_name: str) -> None:
    replace_table_markers(root, tables)

    table_paragraph_ids = {
        id(paragraph)
        for table in root.findall(".//w:tbl", NS)
        for paragraph in table.findall(".//w:p", NS)
    }

    for section in root.findall(".//w:sectPr", NS):
        margins = child(section, "pgMar")
        set_attr(margins, "top", "1134")
        set_attr(margins, "right", "567")
        set_attr(margins, "bottom", "1134")
        set_attr(margins, "left", "1701")
        set_attr(margins, "footer", FOOTER_DISTANCE)

    for table in root.findall(".//w:tbl", NS):
        table_properties = child(table, "tblPr", first=True)
        table_width = child(table_properties, "tblW")
        set_attr(table_width, "w", CONTENT_WIDTH)
        set_attr(table_width, "type", "dxa")
        set_attr(child(table_properties, "tblLayout"), "type", "fixed")
        apply_table_borders(table_properties)

    for paragraph in root.findall(".//w:p", NS):
        if docx_name == "diploma_main_part.docx" and paragraph_text(paragraph) == "Table of Contents":
            text_nodes = paragraph.findall(".//w:t", NS)
            if text_nodes:
                text_nodes[0].text = "СОДЕРЖАНИЕ"
                for node in text_nodes[1:]:
                    node.text = ""

        properties = child(paragraph, "pPr", first=True)
        indent = child(properties, "ind")
        style = paragraph_style(paragraph)
        is_table_paragraph = id(paragraph) in table_paragraph_ids

        is_list_paragraph = properties.find("w:numPr", NS) is not None
        is_caption_paragraph = style in ("ImageCaption", "Caption", "FigureCaption")
        is_heading = style.startswith("Heading")
        heading_level = int(style[7:]) if is_heading and style[7:].isdigit() else 0

        # Detect table-caption text lines (written as body paragraphs by pandoc)
        para_text = paragraph_text(paragraph)
        is_table_caption_text = (
            not is_table_paragraph and not is_heading
            and bool(re.match(r"Таблица\s+\d", para_text))
        )

        if is_table_paragraph:
            set_attr(indent, "firstLine", "0")
            set_attr(indent, "left", "0")
            set_paragraph_spacing(properties, TABLE_LINE_SPACING)
        elif is_list_paragraph:
            # Dash at red-line position (1.25 cm), continuation wraps to left margin (0)
            set_attr(indent, "firstLine", FIRST_LINE_INDENT)
            set_attr(indent, "left", LIST_INDENT)
            indent.attrib.pop(q("hanging"), None)  # firstLine and hanging are mutually exclusive
            set_paragraph_spacing(properties, BODY_LINE_SPACING)
        elif is_caption_paragraph:
            set_attr(indent, "firstLine", "0")
            set_attr(indent, "left", "0")
            set_attr(child(properties, "jc"), "val", "center")
            set_paragraph_spacing(properties, BODY_LINE_SPACING)
            for run in paragraph.findall(".//w:r", NS):
                apply_run_formatting(child(run, "rPr", first=True))
            continue
        elif is_heading:
            set_attr(indent, "left", "0")
            if heading_level == 1:
                set_attr(indent, "firstLine", "0")
                set_attr(child(properties, "jc"), "val", "center")
            else:
                set_attr(indent, "firstLine", FIRST_LINE_INDENT)
                set_attr(child(properties, "jc"), "val", "left")
            set_heading_spacing(properties)
            for run in paragraph.findall(".//w:r", NS):
                apply_run_formatting(child(run, "rPr", first=True), bold=True)
            continue
        elif is_table_caption_text:
            set_attr(indent, "firstLine", "0")
            set_attr(indent, "left", "0")
            spacing = child(properties, "spacing")
            set_attr(spacing, "line", BODY_LINE_SPACING)
            set_attr(spacing, "lineRule", "auto")
            set_attr(spacing, "before", HEADING_SPACE_BEFORE)
            set_attr(spacing, "after", "0")
            set_attr(child(properties, "jc"), "val", "left")
            for run in paragraph.findall(".//w:r", NS):
                apply_run_formatting(child(run, "rPr", first=True))
            continue
        else:
            if para_text and not style.startswith(("TOC", "Title", "Caption", "Image")):
                set_attr(indent, "firstLine", FIRST_LINE_INDENT)
            set_paragraph_spacing(properties, BODY_LINE_SPACING)
        set_attr(child(properties, "jc"), "val", "both")

        for run in paragraph.findall(".//w:r", NS):
            apply_run_formatting(child(run, "rPr", first=True))

    for text_node in root.findall(".//w:t", NS):
        if text_node.text and " " in text_node.text:
            text_node.text = text_node.text.replace(" ", " ")

    remove_all_bold_italic(root)


def patch_numbering_xml(root: ET.Element) -> None:
    """Replace all bullet list markers with an em-dash in Times New Roman."""
    for lvl in root.findall(".//w:lvl", NS):
        num_fmt = lvl.find("w:numFmt", NS)
        if num_fmt is None or num_fmt.get(q("val")) != "bullet":
            continue
        lvl_text = lvl.find("w:lvlText", NS)
        if lvl_text is not None:
            set_attr(lvl_text, "val", "—")
        rpr = child(lvl, "rPr")
        fonts = child(rpr, "rFonts", first=True)
        for attr in ("ascii", "hAnsi", "cs", "eastAsia"):
            set_attr(fonts, attr, FONT)
        set_attr(child(rpr, "sz"), "val", FONT_SIZE)
        set_attr(child(rpr, "szCs"), "val", FONT_SIZE)
        ppr = lvl.find("w:pPr", NS)
        if ppr is not None:
            ind = child(ppr, "ind")
            set_attr(ind, "firstLine", FIRST_LINE_INDENT)
            set_attr(ind, "left", LIST_INDENT)
            ind.attrib.pop(q("hanging"), None)


def patch_styles_xml(root: ET.Element) -> None:
    for style in root.findall("w:style", NS):
        style_id = style.get(q("styleId"), "")
        is_heading_style = style_id.startswith("Heading")
        apply_run_formatting(
            child(style, "rPr"),
            no_underline=style_id == "Hyperlink",
            bold=is_heading_style,
        )
        paragraph_properties = child(style, "pPr")

        if is_heading_style:
            try:
                level = int(style_id[7:])
            except ValueError:
                level = 1
            jc_val = "center" if level == 1 else "left"
            set_attr(child(paragraph_properties, "jc"), "val", jc_val)
            ind = child(paragraph_properties, "ind")
            if level == 1:
                set_attr(ind, "firstLine", "0")
                set_attr(ind, "left", "0")
            else:
                set_attr(ind, "firstLine", FIRST_LINE_INDENT)
                set_attr(ind, "left", "0")
            set_heading_spacing(paragraph_properties)
        else:
            set_attr(child(paragraph_properties, "jc"), "val", "both")
            set_paragraph_spacing(paragraph_properties, BODY_LINE_SPACING)

        if (
            style.get(q("type")) == "paragraph"
            and not is_heading_style
            and not style_id.startswith(("TOC", "Title", "Caption", "Image"))
        ):
            set_attr(child(paragraph_properties, "ind"), "firstLine", FIRST_LINE_INDENT)

    for size in root.findall(".//w:sz", NS) + root.findall(".//w:szCs", NS):
        set_attr(size, "val", FONT_SIZE)
    for color in root.findall(".//w:color", NS):
        set_attr(color, "val", "000000")
    # Bold in heading styles is intentional — remove only from non-heading styles
    for style in root.findall("w:style", NS):
        if style.get(q("styleId"), "").startswith("Heading"):
            continue
        rpr = style.find("w:rPr", NS)
        if rpr is None:
            continue
        for tag in ("i", "iCs"):
            for node in list(rpr.findall(f"w:{tag}", NS)):
                rpr.remove(node)


_FOOTER_XML = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p>
    <w:pPr>
      <w:jc w:val="center"/>
      <w:rPr>
        <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>
        <w:sz w:val="28"/>
        <w:szCs w:val="28"/>
      </w:rPr>
    </w:pPr>
    <w:r>
      <w:rPr>
        <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>
        <w:sz w:val="28"/>
        <w:szCs w:val="28"/>
      </w:rPr>
      <w:fldChar w:fldCharType="begin"/>
    </w:r>
    <w:r>
      <w:rPr>
        <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>
        <w:sz w:val="28"/>
        <w:szCs w:val="28"/>
      </w:rPr>
      <w:instrText xml:space="preserve"> PAGE </w:instrText>
    </w:r>
    <w:r>
      <w:rPr>
        <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>
        <w:sz w:val="28"/>
        <w:szCs w:val="28"/>
      </w:rPr>
      <w:fldChar w:fldCharType="end"/>
    </w:r>
  </w:p>
</w:ftr>
"""

_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_FOOTER_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"
_FOOTER_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"


def _setup_footer_files(unpacked: Path) -> str:
    """Write footer1.xml, register it in rels and content-types. Returns the rel ID."""
    (unpacked / "word" / "footer1.xml").write_text(_FOOTER_XML, encoding="utf-8")

    rels_path = unpacked / "word" / "_rels" / "document.xml.rels"
    rels_tree = ET.parse(rels_path)
    rels_root = rels_tree.getroot()
    ET.register_namespace("", _RELS_NS)
    existing_ids = {el.get("Id", "") for el in rels_root.findall(f"{{{_RELS_NS}}}Relationship")}
    footer_rel_id = "rIdFooter1"
    while footer_rel_id in existing_ids:
        footer_rel_id += "x"
    footer_rel = ET.SubElement(rels_root, f"{{{_RELS_NS}}}Relationship")
    footer_rel.set("Id", footer_rel_id)
    footer_rel.set("Type", _FOOTER_REL_TYPE)
    footer_rel.set("Target", "footer1.xml")
    rels_tree.write(rels_path, encoding="utf-8", xml_declaration=True)

    ct_path = unpacked / "[Content_Types].xml"
    ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    ct_tree = ET.parse(ct_path)
    ct_root = ct_tree.getroot()
    ET.register_namespace("", ct_ns)
    if not any(el.get("PartName") == "/word/footer1.xml" for el in ct_root.findall(f"{{{ct_ns}}}Override")):
        override = ET.SubElement(ct_root, f"{{{ct_ns}}}Override")
        override.set("PartName", "/word/footer1.xml")
        override.set("ContentType", _FOOTER_CONTENT_TYPE)
    ct_tree.write(ct_path, encoding="utf-8", xml_declaration=True)

    return footer_rel_id


def _add_footer_refs(doc_root: ET.Element, footer_rel_id: str) -> None:
    """Add footerReference elements to all sectPr nodes in the document tree."""
    ET.register_namespace("r", R_NS)
    for sect in doc_root.findall(".//w:sectPr", NS):
        if not sect.findall("w:footerReference", NS):
            footer_ref = ET.SubElement(sect, q("footerReference"))
            footer_ref.set(q("type"), "default")
            footer_ref.set(f"{{{R_NS}}}id", footer_rel_id)


def patch_docx(docx_path: Path, tables: list[ExtractedTable]) -> None:
    ET.register_namespace("w", W_NS)
    with tempfile.TemporaryDirectory() as temp_dir:
        unpacked = Path(temp_dir)
        with ZipFile(docx_path) as archive:
            archive.extractall(unpacked)

        footer_rel_id = _setup_footer_files(unpacked)

        document_xml = unpacked / "word" / "document.xml"
        document_tree = ET.parse(document_xml)
        doc_root = document_tree.getroot()
        patch_document_xml(doc_root, tables, docx_path.name)
        _add_footer_refs(doc_root, footer_rel_id)
        document_tree.write(document_xml, encoding="utf-8", xml_declaration=True)

        styles_xml = unpacked / "word" / "styles.xml"
        if styles_xml.exists():
            styles_tree = ET.parse(styles_xml)
            patch_styles_xml(styles_tree.getroot())
            styles_tree.write(styles_xml, encoding="utf-8", xml_declaration=True)

        numbering_xml = unpacked / "word" / "numbering.xml"
        if numbering_xml.exists():
            numbering_tree = ET.parse(numbering_xml)
            patch_numbering_xml(numbering_tree.getroot())
            numbering_tree.write(numbering_xml, encoding="utf-8", xml_declaration=True)

        temporary_docx = docx_path.with_suffix(".tmp.docx")
        with ZipFile(temporary_docx, "w", ZIP_DEFLATED) as output:
            for file in unpacked.rglob("*"):
                if file.is_file():
                    output.write(file, file.relative_to(unpacked).as_posix())
        temporary_docx.replace(docx_path)


def verify_docx(docx_path: Path, tables: list[ExtractedTable]) -> None:
    with ZipFile(docx_path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        styles = ET.fromstring(archive.read("word/styles.xml"))

    text = normalize_space(" ".join(paragraph_text(paragraph) for paragraph in document.findall(".//w:p", NS)))
    table_count = len(document.findall(".//w:tbl", NS))

    # Bold is intentional in heading paragraphs and heading style definitions; count only
    # unexpected bold (in non-heading body runs) and all italic.
    heading_run_ids: set[int] = set()
    for p in document.findall(".//w:p", NS):
        if paragraph_style(p).startswith("Heading"):
            for run in p.findall(".//w:r", NS):
                heading_run_ids.add(id(run))
    unexpected_bold = sum(
        1 for run in document.findall(".//w:r", NS)
        if id(run) not in heading_run_ids
        for tag in ("b", "bCs")
        if run.find(f"w:rPr/w:{tag}", NS) is not None
    )
    italic_count = sum(
        len(root.findall(f".//w:{tag}", NS))
        for root in (document, styles)
        for tag in ("i", "iCs")
    )
    bold_italic_count = unexpected_bold + italic_count
    marker_left = "__DOCX_TABLE_" in text
    latex_spec_left = any(fragment in text for fragment in (">p(", "p(-", "* 0."))
    old_code_left = any(
        fragment in text
        for fragment in ("bbox = polygon.bounds", "def greedy_nn_tsp", "STATES: NORMAL", "FastAPI startup")
    )

    errors: list[str] = []
    if table_count != len(tables):
        errors.append(f"expected {len(tables)} tables, got {table_count}")
    if bold_italic_count:
        errors.append(f"bold/italic tags remain: {bold_italic_count}")
    if marker_left:
        errors.append("table marker remained in DOCX")
    if latex_spec_left:
        errors.append("LaTeX table column spec remained in DOCX")
    if old_code_left:
        errors.append("old pseudocode/code block text remained in DOCX")

    missing_cells: list[str] = []
    for table in tables:
        for row in table.rows:
            for cell in row:
                normalized_cell = normalize_space(cell)
                if len(normalized_cell) > 2 and normalized_cell not in text:
                    missing_cells.append(normalized_cell[:80])
                    if len(missing_cells) >= 5:
                        break
            if len(missing_cells) >= 5:
                break
        if len(missing_cells) >= 5:
            break
    if missing_cells:
        errors.append("table cell text missing: " + "; ".join(missing_cells))

    if errors:
        raise RuntimeError(f"{docx_path.name}: " + "; ".join(errors))


def build_doc(doc_key: str, *, keep_temp: bool = False) -> None:
    tex_name, docx_name = DOCS[doc_key]
    source_tex = LATEX_DIR / tex_name
    output_docx = DOCX_DIR / docx_name

    tex = source_tex.read_text(encoding="utf-8")
    patched_tex, tables = extract_tables(tex, doc_key)
    DOCX_DIR.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_tex = Path(temp_dir) / tex_name
        temp_tex.write_text(patched_tex, encoding="utf-8")
        run_pandoc(temp_tex, output_docx)
        if keep_temp:
            debug_tex = LATEX_DIR / f".{source_tex.stem}.docx-build.tex"
            shutil.copyfile(temp_tex, debug_tex)

    patch_docx(output_docx, tables)
    verify_docx(output_docx, tables)
    print(f"built {output_docx.relative_to(BASE_DIR)} ({len(tables)} tables)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build diploma DOCX files from LaTeX.")
    parser.add_argument(
        "targets",
        nargs="*",
        choices=sorted(DOCS) + ["all"],
        default=["all"],
        help="Documents to build. Defaults to all.",
    )
    parser.add_argument("--keep-temp", action="store_true", help="Keep preprocessed LaTeX next to sources.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if shutil.which("pandoc") is None:
        print("pandoc is not installed or not available in PATH", file=sys.stderr)
        return 1

    targets = list(DOCS) if "all" in args.targets else args.targets
    for target in targets:
        build_doc(target, keep_temp=args.keep_temp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
