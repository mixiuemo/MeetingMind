from datetime import datetime
from html import escape
from io import BytesIO
import re
from zipfile import ZIP_DEFLATED, ZipFile
from zoneinfo import ZoneInfo


CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")
INVALID_XML_CHARACTERS = re.compile(
    "[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]"
)


def _safe_text(text: object) -> str:
    return escape(INVALID_XML_CHARACTERS.sub("", str(text)))


def _format_duration(milliseconds: int) -> str:
    total_seconds = max(0, int(milliseconds or 0) // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_datetime(value: str | None) -> str:
    if not value:
        return "-"
    return datetime.fromisoformat(value).astimezone(CHINA_TIMEZONE).strftime(
        "%Y年%m月%d日 %H:%M"
    )


def _run(
    text: object,
    *,
    bold: bool = False,
    color: str | None = None,
    size: int | None = None,
) -> str:
    properties = ['<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/>']
    if bold:
        properties.extend(["<w:b/>", "<w:bCs/>"])
    if color:
        properties.append(f'<w:color w:val="{color}"/>')
    if size:
        properties.append(f'<w:sz w:val="{size}"/>')
    return (
        f"<w:r><w:rPr>{''.join(properties)}</w:rPr>"
        f'<w:t xml:space="preserve">{_safe_text(text)}</w:t></w:r>'
    )


def _hyperlink(anchor: str, text: str) -> str:
    return (
        f'<w:hyperlink w:anchor="{anchor}" w:history="1">'
        f'{_run(text, color="526FA8", size=19)}</w:hyperlink>'
    )


def _paragraph(
    content: str,
    style: str | None = None,
    *,
    keep_next: bool = False,
    page_break_before: bool = False,
    shade: str | None = None,
) -> str:
    properties = []
    if style:
        properties.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        properties.append("<w:keepNext/>")
    if page_break_before:
        properties.append("<w:pageBreakBefore/>")
    if shade:
        properties.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
        properties.append('<w:ind w:left="180" w:right="180"/>')
        properties.append('<w:spacing w:before="160" w:after="160"/>')
    return f"<w:p><w:pPr>{''.join(properties)}</w:pPr>{content}</w:p>"


def _text_paragraph(text: object, style: str | None = None, **kwargs) -> str:
    return _paragraph(_run(text), style, **kwargs)


def _heading(text: str, level: int = 1, *, page_break_before: bool = False) -> str:
    return _text_paragraph(
        text,
        f"Heading{level}",
        keep_next=True,
        page_break_before=page_break_before,
    )


def _source_links(source_ids: list[str], bookmarks: dict[str, tuple[str, int]]) -> str:
    links = []
    for source_id in source_ids or []:
        bookmark = bookmarks.get(source_id)
        if bookmark:
            anchor, index = bookmark
            links.append(_hyperlink(anchor, f"原文 {index:02d}"))
    if not links:
        return ""
    return _run("来源：", color="6B7280", size=19) + _run(" · ", color="A0A7B2", size=19).join(links)


def _insight_item(
    index: int,
    text: str,
    source_ids: list[str],
    bookmarks: dict[str, tuple[str, int]],
) -> list[str]:
    source_content = _source_links(source_ids, bookmarks)
    result = [
        _paragraph(
            _run(f"{index:02d}  ", bold=True, color="526FA8", size=21)
            + _run(text, size=21),
            "InsightItem",
        )
    ]
    if source_content:
        result.append(_paragraph(source_content, "SourceLink"))
    return result


def _analysis_sections(meeting: dict, bookmarks: dict[str, tuple[str, int]]) -> list[str]:
    analysis = meeting.get("analysis")
    if meeting.get("analysis_status") != "completed" or not analysis:
        return [
            _heading("AI 会议纪要"),
            _text_paragraph(
                "本次导出时尚未生成 AI 会议纪要，以下仅包含完整会议原文。",
                "Note",
            ),
        ]

    paragraphs = [
        _heading("AI 会议纪要"),
        _text_paragraph("AI 生成内容仅供参考，请结合会议原文核实。", "Note"),
        _heading("会议摘要", 2),
        _text_paragraph(
            analysis.get("summary") or "本次会议没有足够内容可供总结。",
            "Summary",
            shade="EEF3FA",
        ),
    ]
    sections = [
        ("核心要点", analysis.get("key_points") or [], "未提取到明确核心要点。"),
        ("会议结论", analysis.get("decisions") or [], "会议中没有形成明确结论。"),
        ("待办事项", analysis.get("action_items") or [], "本次会议未识别到明确待办事项。"),
        ("未决问题", analysis.get("open_questions") or [], "未识别到尚未解决的问题。"),
    ]
    for title, items, empty_text in sections:
        paragraphs.append(_heading(title, 2))
        if not items:
            paragraphs.append(_text_paragraph(empty_text, "EmptyState"))
            continue
        for index, item in enumerate(items, start=1):
            if title == "待办事项":
                text = str(item.get("task") or "")
                owner = item.get("owner") or "未指定"
                deadline = item.get("deadline") or "未指定"
                paragraphs.extend(
                    _insight_item(index, f"☐ {text}", item.get("source_segment_ids", []), bookmarks)
                )
                paragraphs.append(
                    _paragraph(
                        _run(f"负责人：{owner}    截止时间：{deadline}", color="6B7280", size=19),
                        "TaskMeta",
                    )
                )
            else:
                paragraphs.extend(
                    _insight_item(
                        index,
                        str(item.get("text") or ""),
                        item.get("source_segment_ids", []),
                        bookmarks,
                    )
                )
    return paragraphs


def build_meeting_docx(meeting: dict) -> BytesIO:
    segments = meeting.get("segments", [])
    title = meeting.get("title") or "未命名会议"
    bookmarks = {
        segment["id"]: (f"segment_{index}", index)
        for index, segment in enumerate(segments, start=1)
    }
    paragraphs = [
        _text_paragraph("HUIYI INTELLIGENCE", "Kicker"),
        _text_paragraph(title, "Title"),
        _text_paragraph("会议智能记录 · AI 纪要与完整转写", "Subtitle"),
        _paragraph(
            _run("开始时间  ", bold=True, color="52658F")
            + _run(_format_datetime(meeting.get("started_at")))
            + _run("    会议时长  ", bold=True, color="52658F")
            + _run(_format_duration(meeting.get("duration_ms", 0)))
            + _run("    转写段落  ", bold=True, color="52658F")
            + _run(f"{len(segments)} 段"),
            "Metadata",
        ),
    ]
    paragraphs.extend(_analysis_sections(meeting, bookmarks))
    paragraphs.append(_heading("完整会议原文", page_break_before=True))
    paragraphs.append(
        _text_paragraph("点击 AI 纪要中的原文来源，可跳转到对应转写段落。", "Note")
    )
    for index, segment in enumerate(segments, start=1):
        start = _format_duration(segment.get("start_ms", 0))
        end = _format_duration(segment.get("end_ms", 0))
        speaker = segment.get("speaker") or "发言人"
        anchor, _ = bookmarks[segment["id"]]
        bookmark_id = index
        paragraphs.append(
            _paragraph(
                f'<w:bookmarkStart w:id="{bookmark_id}" w:name="{anchor}"/>'
                + _run(f"{index:02d}  {speaker}  ·  {start} - {end}", bold=True, color="52658F")
                + f'<w:bookmarkEnd w:id="{bookmark_id}"/>',
                "TranscriptHeading",
                keep_next=True,
            )
        )
        paragraphs.append(_text_paragraph(segment.get("text") or "", "TranscriptBody"))

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {''.join(paragraphs)}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1080" w:right="1260" w:bottom="1080" w:left="1260" w:header="708" w:footer="708"/>
    </w:sectPr>
  </w:body>
</w:document>"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="160" w:line="320" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/><w:color w:val="273244"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Kicker"><w:name w:val="Kicker"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="80"/></w:pPr><w:rPr><w:b/><w:color w:val="687B9F"/><w:sz w:val="18"/><w:spacing w:val="30"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="80"/></w:pPr><w:rPr><w:b/><w:color w:val="172033"/><w:sz w:val="44"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="260"/></w:pPr><w:rPr><w:color w:val="6B7280"/><w:sz w:val="20"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Metadata"><w:name w:val="Metadata"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="420"/><w:shd w:val="clear" w:color="auto" w:fill="F4F6F9"/><w:ind w:left="180" w:right="180"/></w:pPr><w:rPr><w:sz w:val="19"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:before="440" w:after="180"/><w:keepNext/></w:pPr><w:rPr><w:b/><w:color w:val="253551"/><w:sz w:val="31"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:before="300" w:after="120"/><w:keepNext/></w:pPr><w:rPr><w:b/><w:color w:val="52658F"/><w:sz w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="180"/></w:pPr><w:rPr><w:color w:val="7B8493"/><w:sz w:val="19"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Summary"><w:name w:val="Summary"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="220" w:line="340" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="23"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="InsightItem"><w:name w:val="Insight Item"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="100" w:after="40" w:line="320" w:lineRule="auto"/><w:keepNext/></w:pPr><w:rPr><w:sz w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="SourceLink"><w:name w:val="Source Link"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="130"/><w:ind w:left="500"/></w:pPr><w:rPr><w:sz w:val="19"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="TaskMeta"><w:name w:val="Task Meta"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="130"/><w:ind w:left="500"/></w:pPr><w:rPr><w:sz w:val="19"/><w:color w:val="6B7280"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="EmptyState"><w:name w:val="Empty State"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="180"/><w:ind w:left="300"/></w:pPr><w:rPr><w:color w:val="7B8493"/><w:sz w:val="20"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="TranscriptHeading"><w:name w:val="Transcript Heading"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="240" w:after="80"/><w:keepNext/></w:pPr><w:rPr><w:b/><w:color w:val="52658F"/><w:sz w:val="20"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="TranscriptBody"><w:name w:val="Transcript Body"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="210" w:line="340" w:lineRule="auto"/><w:ind w:left="300"/></w:pPr><w:rPr><w:sz w:val="21"/></w:rPr></w:style>
</w:styles>"""

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        )
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles_xml)
    output.seek(0)
    return output
