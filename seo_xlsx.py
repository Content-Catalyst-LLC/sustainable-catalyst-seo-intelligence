"""Minimal, dependency-free Office Open XML (.xlsx) writer for SEO Studio exports.

Standard library only (``zipfile`` + XML), so the Studio image needs no new package. Sheets are streamed row by
row into the archive, so a whole-run export does not hold every row in memory at once.

Safety properties (tested in ``tests/test_studio_export.py``):

* No cell is ever a formula: every value is an inline string or a number, and no ``<f>`` element is written.
  Text that a spreadsheet would read as a formula when retyped (leading ``= + - @``, tab or CR, also full-width
  forms, after leading spaces; the rule ``seo_siteone.csv_safe`` uses) gets Excel's ``quotePrefix`` style, which
  keeps the text byte-for-byte and keeps it text if a reviewer edits the cell.
* Hyperlinks are added only for absolute ``http``/``https`` URLs, as external relationships (never ``HYPERLINK``
  formulas), at most ``MAX_LINKS_PER_SHEET`` per sheet and ``MAX_URL`` characters long.
* Characters that XML 1.0 cannot carry are removed; cells longer than Excel's 32,767-character limit are cut and
  marked.
"""
import re
import zipfile
from xml.sax.saxutils import escape

MAX_CELL = 32767
MAX_URL = 2079
MAX_LINKS_PER_SHEET = 65000
MAX_ROWS = 1048576
TRUNCATED = " …[truncated in export]"
_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")
_URL = re.compile(r"^https?://[^\s<>\"]+$", re.IGNORECASE)

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# cellXfs indices (see STYLES)
S_DEFAULT, S_HEAD, S_HEAD_REVIEW, S_LINK, S_NOTICE, S_TEXT_SAFE, S_REVIEW, S_WRAP, S_REVIEW_SAFE, S_WRAP_SAFE = range(10)


def clean(value):
    """Text as it will be stored: XML-legal and within Excel's cell limit."""
    text = _ILLEGAL.sub("", "" if value is None else str(value))
    if len(text) > MAX_CELL:
        text = text[:MAX_CELL - len(TRUNCATED)] + TRUNCATED
    return text


def formula_like(text):
    return text.lstrip(" ").startswith(_FORMULA_LEAD)


def col_letter(index):
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


class Column:
    """One worksheet column. kind: text | wrap | int | url. review=True marks an editable reviewer column."""

    def __init__(self, name, width=14, kind="text", review=False, choices=None):
        self.name, self.width, self.kind, self.review, self.choices = name, width, kind, review, choices


class Sheet:
    """A table with a one-row notice (row 1), a header (row 2, frozen, filtered) and streamed data rows."""

    def __init__(self, name, columns, rows, notice=""):
        if len(name) > 31 or any(c in name for c in "[]:*?/\\"):
            raise ValueError(f"invalid sheet name {name!r}")
        self.name, self.columns, self.rows, self.notice = name, columns, rows, notice
        self.count = 0


def _cell(ref, value, column, links):
    if value is None or value == "":
        return f'<c r="{ref}" s="{S_REVIEW}"/>' if column.review else ""
    if isinstance(value, bool):
        value = "yes" if value else "no"
    if isinstance(value, (int, float)) and column.kind == "int":
        style = f' s="{S_REVIEW}"' if column.review else ""
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = clean(value)
    safe = formula_like(text)
    if column.review:
        style = S_REVIEW_SAFE if safe else S_REVIEW
    elif column.kind == "url" and _URL.match(text) and len(text) <= MAX_URL and len(links) < MAX_LINKS_PER_SHEET:
        links.append((ref, text))
        style = S_LINK
    elif column.kind == "wrap":
        style = S_WRAP_SAFE if safe else S_WRAP
    else:
        style = S_TEXT_SAFE if safe else S_DEFAULT
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t{space}>{escape(text)}</t></is></c>'


def _write_sheet(zf, path, sheet, selected):
    """Stream one worksheet; returns its hyperlink relationships."""
    cols = sheet.columns
    last = col_letter(len(cols) - 1)
    links = []
    with zf.open(path, "w", force_zip64=True) as raw:
        w = lambda s: raw.write(s.encode("utf-8"))  # noqa: E731
        w(HEAD + f'<worksheet xmlns="{NS}" xmlns:r="{NS_R}">')
        tab = ' tabSelected="1"' if selected else ""
        w(f'<sheetViews><sheetView workbookViewId="0"{tab}>'
          '<pane ySplit="2" topLeftCell="A3" activePane="bottomLeft" state="frozen"/>'
          '<selection pane="bottomLeft" activeCell="A3" sqref="A3"/></sheetView></sheetViews>')
        w('<sheetFormatPr defaultRowHeight="15"/><cols>')
        for i, c in enumerate(cols, 1):
            w(f'<col min="{i}" max="{i}" width="{c.width}" customWidth="1"/>')
        w('</cols><sheetData>')
        w(f'<row r="1" ht="48" customHeight="1"><c r="A1" s="{S_NOTICE}" t="inlineStr"><is><t>'
          f'{escape(clean(sheet.notice))}</t></is></c></row>')
        w('<row r="2">' + "".join(
            f'<c r="{col_letter(i)}2" s="{S_HEAD_REVIEW if c.review else S_HEAD}" t="inlineStr"><is><t>'
            f'{escape(clean(c.name))}</t></is></c>' for i, c in enumerate(cols)) + '</row>')
        n = 2
        for values in sheet.rows:
            n += 1
            if n > MAX_ROWS:
                raise ValueError(f"sheet {sheet.name!r} exceeds Excel's row limit")
            w(f'<row r="{n}">' + "".join(_cell(f"{col_letter(i)}{n}", v, c, links)
                                         for i, (v, c) in enumerate(zip(values, cols))) + '</row>')
        sheet.count = n - 2
        w('</sheetData>')
        w(f'<autoFilter ref="A2:{last}{max(n, 2)}"/>')
        w(f'<mergeCells count="1"><mergeCell ref="A1:{last}1"/></mergeCells>')
        checks = [(i, c) for i, c in enumerate(cols) if c.choices]
        if checks and n > 2:
            w(f'<dataValidations count="{len(checks)}">')
            for i, c in checks:
                ref = f"{col_letter(i)}3:{col_letter(i)}{n}"
                options = escape(",".join(c.choices).replace('"', ""))
                w(f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" errorStyle="warning" '
                  f'errorTitle="Not a listed value" error="Choose a listed value or leave it blank." sqref="{ref}">'
                  f'<formula1>"{options}"</formula1></dataValidation>')
            w('</dataValidations>')
        if links:
            w('<hyperlinks>' + "".join(f'<hyperlink ref="{ref}" r:id="rId{k}"/>' for k, (ref, _) in
                                       enumerate(links, 1)) + '</hyperlinks>')
        w('<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>')
        w('<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>')
    return links


STYLES = (HEAD + f'<styleSheet xmlns="{NS}">'
          '<fonts count="4"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
          '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
          '<font><u/><sz val="11"/><color rgb="FF0563C1"/><name val="Calibri"/><family val="2"/></font>'
          '<font><b/><sz val="11"/><color rgb="FF7A2E00"/><name val="Calibri"/><family val="2"/></font></fonts>'
          '<fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
          '<fill><patternFill patternType="solid"><fgColor rgb="FFE7E9E8"/><bgColor indexed="64"/></patternFill></fill>'
          '<fill><patternFill patternType="solid"><fgColor rgb="FFFFE699"/><bgColor indexed="64"/></patternFill></fill>'
          '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF8DC"/><bgColor indexed="64"/></patternFill></fill>'
          '</fills>'
          '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
          '<border><left/><right/><top/><bottom style="thin"><color rgb="FF9AA6A1"/></bottom><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="10">'
          '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" '
          'applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
          '<xf numFmtId="0" fontId="1" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" '
          'applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
          '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
          '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
          '<alignment vertical="top" wrapText="1"/></xf>'
          '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" quotePrefix="1"/>'
          '<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1" applyAlignment="1">'
          '<alignment vertical="top" wrapText="1"/></xf>'
          '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
          '<alignment vertical="top" wrapText="1"/></xf>'
          '<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1" applyAlignment="1" quotePrefix="1">'
          '<alignment vertical="top" wrapText="1"/></xf>'
          '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1" quotePrefix="1">'
          '<alignment vertical="top" wrapText="1"/></xf>'
          '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
          '</styleSheet>')


def _quote_sheet(name):
    return "'" + name.replace("'", "''") + "'"


def write(stream, sheets, title, created_iso, deferred=()):
    """Write ``sheets`` (Sheet objects, tab order) as an .xlsx to a binary ``stream``.

    Sheets are streamed in tab order, except those whose index is in ``deferred``: they are streamed last, so their
    rows may be produced lazily from what the other sheets wrote (e.g. an overview that reports row counts). Every
    sheet given is written; the caller drops empty ones first. Returns the sheets with their final ``count``.
    """
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        order = [i for i in range(len(sheets)) if i not in deferred] + [i for i in deferred]
        for index in order:
            i, sheet = index + 1, sheets[index]
            links = _write_sheet(zf, f"xl/worksheets/sheet{i}.xml", sheet, i == 1)
            if links:
                zf.writestr(f"xl/worksheets/_rels/sheet{i}.xml.rels", HEAD + f'<Relationships xmlns="{NS_PKG}">' + "".join(
                    f'<Relationship Id="rId{k}" Type="{NS_R}/hyperlink" Target="{escape(url, {chr(34): "&quot;"})}" '
                    f'TargetMode="External"/>' for k, (_, url) in enumerate(links, 1)) + '</Relationships>')
        n = len(sheets)
        zf.writestr("[Content_Types].xml", HEAD +
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                    'spreadsheetml.sheet.main+xml"/>' + "".join(
                        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-'
                        f'officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, n + 1)) +
                    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                    'spreadsheetml.styles+xml"/>'
                    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.'
                    'core-properties+xml"/>'
                    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                    'extended-properties+xml"/></Types>')
        zf.writestr("_rels/.rels", HEAD + f'<Relationships xmlns="{NS_PKG}">'
                    f'<Relationship Id="rId1" Type="{NS_R}/officeDocument" Target="xl/workbook.xml"/>'
                    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
                    'metadata/core-properties" Target="docProps/core.xml"/>'
                    f'<Relationship Id="rId3" Type="{NS_R}/extended-properties" Target="docProps/app.xml"/>'
                    '</Relationships>')
        zf.writestr("docProps/core.xml", HEAD +
                    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                    f'<dc:title>{escape(clean(title))}</dc:title><dc:creator>SEO Studio</dc:creator>'
                    f'<dcterms:created xsi:type="dcterms:W3CDTF">{escape(created_iso)}</dcterms:created>'
                    '</cp:coreProperties>')
        zf.writestr("docProps/app.xml", HEAD +
                    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                    '<Application>SEO Studio</Application></Properties>')
        zf.writestr("xl/styles.xml", STYLES)
        zf.writestr("xl/_rels/workbook.xml.rels", HEAD + f'<Relationships xmlns="{NS_PKG}">' + "".join(
            f'<Relationship Id="rId{i}" Type="{NS_R}/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, n + 1)) + f'<Relationship Id="rId{n + 1}" Type="{NS_R}/styles" Target="styles.xml"/>'
            '</Relationships>')
        names = "".join(
            f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
            f'{escape(_quote_sheet(s.name))}!$A$2:${col_letter(len(s.columns) - 1)}${max(s.count + 2, 2)}</definedName>'
            for i, s in enumerate(sheets))
        zf.writestr("xl/workbook.xml", HEAD + f'<workbook xmlns="{NS}" xmlns:r="{NS_R}">'
                    '<bookViews><workbookView activeTab="0"/></bookViews><sheets>' + "".join(
                        f'<sheet name="{escape(s.name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                        for i, s in enumerate(sheets, 1)) + f'</sheets><definedNames>{names}</definedNames></workbook>')
    return sheets
