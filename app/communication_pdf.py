"""Fixed-layout, text PDF import. No OCR and no original-file retention."""
import re
from io import BytesIO
import pdfplumber
from pypdf import PdfReader

SECTIONS = {'academic': '学术成就与建议', 'standardized': '标准化考试目标', 'extracurricular': '课外活动、实习与科研'}
LABELS = {'student_name': '学生姓名', 'school_snapshot': '来自学校', 'grade_snapshot': '所在年级', 'date': '沟通日期', 'duration': '沟通时长', 'method': '沟通方式'}
MAX_PDF_BYTES = 5 * 1024 * 1024


def compact(value):
    return re.sub(r'\s+', '', value or '')


def lines(words, tolerance=4):
    groups = []
    for word in sorted(words, key=lambda w: (w['top'], w['x0'])):
        if not groups or abs(word['top'] - groups[-1][0]) > tolerance:
            groups.append((word['top'], [word]))
        else:
            groups[-1][1].append(word)
    return [(top, ''.join(w['text'] for w in sorted(group, key=lambda w: w['x0']))) for top, group in groups]


def parse_communication_pdf(content):
    if not content.startswith(b'%PDF-') or len(content) > MAX_PDF_BYTES:
        raise ValueError('请上传不超过5MB的文本型沟通PDF。')
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ValueError('不支持加密PDF，请先导出未加密版本。')
        if not 1 <= len(reader.pages) <= 10:
            raise ValueError('沟通PDF必须为1至10页。')
        result = {key: '' for key in LABELS}
        result.update(notes='', items=[])
        seen = set()
        active_section = None
        with pdfplumber.open(BytesIO(content)) as pdf:
            first_text = pdf.pages[0].extract_text() or ''
            if not first_text.strip():
                raise ValueError('无法读取文字，扫描件暂不支持，请上传文本型PDF。')
            # Read each labelled value only from the basic-information block.
            header = first_text.split('学术')[0]
            label_patterns = [r'\s*'.join(re.escape(c) for c in label) for label in LABELS.values()]
            boundary = '|'.join(label_patterns)
            for (key, label), pattern in zip(LABELS.items(), label_patterns):
                match = re.search(pattern + r'\s*[:：]\s*(.*?)(?=(?:' + boundary + r')\s*[:：]|$)', header, re.S)
                if match:
                    result[key] = ' '.join(match[1].split())
            # Generated forms use one labelled value per cell, including wrapped values.
            for table in pdf.pages[0].find_tables():
                for row in table.extract():
                    for cell in row:
                        if not cell: continue
                        for key, label in LABELS.items():
                            match = re.match(re.escape(label) + r'\s*[:：]\s*(.*)', cell, re.S)
                            if match: result[key] = ' '.join(match[1].split())
            if not result['student_name'] or not result['date']:
                raise ValueError('未识别到学生姓名或沟通日期，请使用沟通记录模板。')
            date_match = re.search(r'(\d{4})\s*[/年.\-]\s*(\d{1,2})\s*[/月.\-]\s*(\d{1,2})', result['date'])
            if not date_match:
                raise ValueError('沟通日期无法识别，请使用年/月/日格式。')
            from datetime import date
            result['date'] = date(*map(int, date_match.groups())).isoformat()
            notes_started = False
            for page in pdf.pages:
                page_lines = lines(page.extract_words())
                headings = []
                for top, text in page_lines:
                    for section, title in SECTIONS.items():
                        if compact(title) in compact(text):
                            headings.append((top, section)); seen.add(section)
                    if compact(text) == '备注':
                        headings.append((top, 'notes'))
                # Split table rows can end at a page boundary without a bottom rule.
                # Close those visible vertical edges for extraction only.
                bottoms = sorted({edge['bottom'] for edge in page.edges if edge.get('orientation') == 'v' and edge['height'] > 20})
                settings = {'explicit_horizontal_lines': bottoms} if bottoms else {}
                for table in page.find_tables(settings):
                    preceding = [h for h in headings if h[0] < table.bbox[1]]
                    if preceding:
                        active_section = max(preceding)[1]
                    if active_section not in SECTIONS:
                        continue
                    extracted = table.extract()
                    if not extracted or compact(extracted[0][0]) != '项目' or compact(extracted[0][-1]) != '反馈':
                        continue
                    for row in table.rows[1:]:
                        cells = row.cells
                        if len(cells) != 2 or not all(cells):
                            raise ValueError('项目表格结构不匹配，请使用固定沟通模板。')
                        left, right = cells
                        names = lines(page.crop(left).extract_words())
                        names = [(y, name) for y, name in names if name.strip()]
                        if not names:
                            continuation = page.crop(right).extract_text() or ''
                            if result['items'] and result['items'][-1]['section'] == active_section:
                                result['items'][-1]['feedback'] += '\n' + continuation
                            elif continuation.strip():
                                result['items'].append(dict(section=active_section, item_name='', feedback=continuation))
                            continue
                        for index, (top, name) in enumerate(names):
                            bottom = names[index+1][0] - 1 if index+1 < len(names) else right[3]
                            feedback = page.crop((right[0], max(right[1], top-2), right[2], bottom)).extract_text() or ''
                            if name == '暂无记录' and feedback.strip() in ('', '-'):
                                continue
                            result['items'].append(dict(section=active_section, item_name=name, feedback=feedback))
                note_headings = [top for top, section in headings if section == 'notes']
                if note_headings or notes_started:
                    top = note_headings[-1] + 15 if note_headings else 25
                    note_words = [w for w in page.extract_words() if w['top'] > top and w['bottom'] < page.height - 25]
                    result['notes'] += ('\n' if result['notes'] else '') + '\n'.join(text for _, text in lines(note_words))
                    notes_started = True
            if seen != set(SECTIONS):
                raise ValueError('缺少学术、标化或课外活动栏目，请使用固定沟通模板。')
            if len(result['items']) > 100:
                raise ValueError('项目条目超过100条，请拆分记录。')
        return result
    except ValueError:
        raise
    except Exception:
        raise ValueError('PDF结构无法解析，请重新导出固定版式的文本型PDF。') from None
