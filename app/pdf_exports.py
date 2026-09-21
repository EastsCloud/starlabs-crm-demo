"""Private PDF exports built from explicit business-field allowlists."""
from io import BytesIO
from pathlib import Path
from datetime import datetime, date, timezone
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, LongTable, TableStyle, PageBreak, Image
from app.communication_pdf import SECTIONS
from app import school_fields

ROOT = Path(__file__).resolve().parent
FONT = 'NotoSansSC'
BLUE = colors.HexColor('#2874ad')
LIGHT = colors.HexColor('#eaf2f9')
WIDTH = A4[0] - 64


def setup_font():
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, str(ROOT / 'assets/fonts/NotoSansSC.ttf')))


def para(value, size=9, color=colors.HexColor('#172033')):
    setup_font()
    if value is None or value == '':
        value = '-'
    if isinstance(value, (date, datetime)):
        value = value.isoformat(sep=' ', timespec='minutes') if isinstance(value, datetime) else value.isoformat()
    return Paragraph(escape(str(value)).replace('\n', '<br/>'), ParagraphStyle('body', fontName=FONT,
        fontSize=size, leading=size*1.55, textColor=color, wordWrap='CJK', spaceAfter=0))


def section(title):
    bar = Table([[para(title, 11, colors.white)]], colWidths=[WIDTH])
    bar.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),BLUE),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    bar.keepWithNext = True
    return bar


def grid(rows, widths, header=True):
    cells = [[para(value) for value in row] for row in rows]
    table = LongTable(cells, colWidths=widths, repeatRows=1 if header else 0, splitByRow=1, splitInRow=30, hAlign='CENTER')
    style = [('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#afbdcc')),
        ('LINEBELOW',(0,'splitlast'),(-1,'splitlast'),.4,colors.HexColor('#afbdcc')),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7)]
    if header:
        style.append(('BACKGROUND',(0,0),(-1,0),LIGHT))
    table.setStyle(TableStyle(style))
    return table


def header(title):
    logo = Image(str(ROOT/'static/starlabs.jpg'), width=52, height=52, kind='proportional')
    table = Table([[logo, para(title, 21)]], colWidths=[72, WIDTH-72])
    table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0)]))
    return [table, Spacer(1,16)]


def build(story, title):
    output = BytesIO()
    def footer(canvas, doc):
        canvas.setFont(FONT, 8)
        canvas.setFillColor(colors.HexColor('#697386'))
        canvas.drawRightString(A4[0]-32, 17, f'第 {doc.page} 页')
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=32, rightMargin=32, topMargin=30, bottomMargin=36,
        title=title, author='Star Labs Education', pageCompression=1)
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def communication_payload(record, student):
    return dict(student_name=student.name, school_snapshot=record.school_snapshot or '',
        grade_snapshot=record.grade_snapshot or student.grade or '', date=record.date.isoformat() if record.date else '',
        duration=record.duration or '', method=record.method or '', notes=record.notes or '',
        summary=record.summary or '', contact_person=record.contact_person or '',
        date_time_node=record.date_time_node if record.date_time_node != '未添加时间信息' else '',
        follow_up_time_node=record.follow_up_time_node if record.follow_up_time_node != '未添加时间信息' else '',
        next_follow_up=record.next_follow_up.isoformat() if record.next_follow_up else '', generated_tasks=record.generated_tasks or '',
        items=[dict(section=item.section, item_name=item.item_name, feedback=item.feedback) for item in record.items])


def communication_story(payload):
    story = header('学生沟通记录与反馈')
    story += [section('学员基础信息'), Spacer(1,9), grid([
        [f"学生姓名：{payload.get('student_name','')}", f"来自学校：{payload.get('school_snapshot','')}", f"所在年级：{payload.get('grade_snapshot','')}"],
        [f"沟通日期：{payload.get('date','')}", f"沟通时长：{payload.get('duration','')}", f"沟通方式：{payload.get('method','')}"],
    ], [WIDTH/3]*3, False), Spacer(1,15)]
    for key,title in SECTIONS.items():
        rows = [[item.get('item_name',''),item.get('feedback','')] for item in payload.get('items',[]) if item['section']==key]
        story += [section(title), Spacer(1,8), grid([['项目','反馈']]+(rows or [['暂无记录','']]),[WIDTH*.2, WIDTH*.8]),Spacer(1,15)]
    notes = payload.get('notes','')
    for key,label in [('summary','原记录摘要'),('date_time_node','沟通节点'),('follow_up_time_node','跟进节点'),('contact_person','联系人'),('next_follow_up','下次跟进'),('generated_tasks','生成任务')]:
        if payload.get(key):
            notes += '\n' + label + '：' + payload[key]
    story += [section('备注'), Spacer(1,8), para(notes.strip() or '暂无记录')]
    return story


def communication_pdf(payload):
    return build(communication_story(payload), '学生沟通记录与反馈')


def records_section(title, records, fields, title_field=None):
    story = [PageBreak(), section(title), Spacer(1,12)]
    if not records:
        return story+[para('暂无记录')]
    for index, record in enumerate(records, 1):
        if title_field:
            heading = para(f"{index}. {getattr(record,title_field) or '记录'}", 12)
            heading.keepWithNext=True
            story += [heading,Spacer(1,6)]
        story += [grid([['字段','内容']]+[[label,getattr(record,field,None)] for field,label in fields],[110,WIDTH-110]),Spacer(1,12)]
    return story


def archive_pdf(student, timelines, archive_id, exported_at):
    story=header('学生综合归档')
    story += [para(student.name,24),Spacer(1,14),para(f'归档编号：{archive_id}'),para(f'导出时间（UTC）：{exported_at:%Y-%m-%d %H:%M:%S}'),Spacer(1,18),section('基本信息'),Spacer(1,10)]
    basics=[('name','学生姓名'),('grade','年级'),('target_school','目标学校'),('target_major','目标方向'),('final_school','录取学校'),('advisor','顾问'),('priority_level','优先级'),('overall_status','整体状态'),('notes','备注')]
    if student.student_type == "school":
        basics = [(key, label) for key, label in basics if key not in ("grade", "target_school", "target_major")]
        basics.insert(1, ("enrollment_year", "入学年份"))
        basics.extend((f.name, f.label) for f in school_fields.BASIC_FIELDS if not f.private)
    story += [grid([[label,getattr(student,key,None)] for key,label in basics],[110,WIDTH-110],False),Spacer(1,12),para('此归档不含证件号码、银行卡资料、账号密码、Portal密钥或安全问答。下载不代表已保存至NAS。',8)]
    applications=sorted(student.applications,key=lambda r:(r.deadline or date.max,r.id))
    if student.student_type == 'school':
        story += records_section('申请信息', applications, [(f.name, f.label) for f in school_fields.APPLICATION_FIELDS if not f.private], 'program_name')
    else:
        story += records_section('申请信息',applications,[('program_name','学校/项目'),('program_type','类型'),('country','国家'),('batch','批次'),('status','状态'),('deadline','截止日期'),('deadline_time_node','截止节点'),('result_date','结果日期'),('result_time_node','结果节点'),('result','结果'),('materials_status','材料状态'),('next_step','下一步'),('form_status','网申填表'),('online_application_status','网申状态'),('submission_date','提交日期'),('supplemental_essay','补充文书'),('transcript_required','成绩单'),('application_system','网申系统'),('portal_material_progress','Portal进度'),('portal_status','Portal状态'),('score_delivery_status','送分状态'),('ceeb_code','CEEB Code'),('language_delivery','语言送分'),('sat_delivery','SAT送分'),('act_code','ACT Code'),('act_delivery','ACT送分'),('ap_delivery','AP送分'),('other_delivery','其他递送'),('delivery_date','递送日期'),('notes','备注')],'program_name')
    if student.student_type != 'school' or student.courses:
        story += records_section('选课信息',sorted(student.courses,key=lambda r:(r.semester or '',r.id)),[('semester','学期'),('category','类别'),('name','课程'),('level','等级'),('grade','成绩'),('credits','学分')],'name')
    story += records_section('标化记录',sorted(student.exams,key=lambda r:(r.exam_date or date.min,r.id),reverse=True),[(k,v) for k,v in [('exam_name','考试'),('exam_date','日期'),('exam_time_node','时间节点'),('subject','科目'),('score','成绩'),('total','总分'),('target_score','目标成绩'),('reading','阅读'),('listening','听力'),('speaking','口语'),('writing','写作'),('math','数学'),('science','科学'),('english','英语'),('language','Language'),('verbal','SSAT V'),('quantitative','SSAT Q'),('analytical','SSAT A'),('component_score','分项成绩'),('appointment_number','预约编号'),('record_locator','考试记录编号'),('status','状态'),('next_action','后续行动'),('notes','备注')]],'exam_name')
    if student.student_type != 'school' or student.projects:
        story += records_section('项目与活动',sorted(student.projects,key=lambda r:(r.start_date or date.max,r.id)),[('project_name','项目'),('project_type','类型'),('status','状态'),('start_date','开始日期'),('start_time_node','开始节点'),('end_date','结束日期'),('end_time_node','结束节点'),('outcome','成果'),('risk_level','风险'),('notes','备注')],'project_name')
    if student.student_type == 'school':
        for kind in ['activities','third-party-interviews','school-meetings','training-records']:
            title, fields = school_fields.RECORD_FIELDS[kind]
            records = list(getattr(student, school_fields.RELATIONS[kind]))
            story += records_section(title, records, [(f.name, f.label) for f in fields if not f.private])
    story += records_section('全部任务（含已完成）',sorted(student.tasks,key=lambda r:(r.due_date or date.max,r.id)),[('title','任务'),('category','类别'),('owner','负责人'),('status','状态'),('priority','优先级'),('due_date','日期'),('time_node','时间节点'),('description','说明')],'title')
    story += records_section('时间点与时间线',sorted(timelines,key=lambda r:(r.date or date.max,r.id)),[('content','内容'),('owner','负责人'),('period','时段'),('category','类别'),('status','状态'),('date','日期'),('time_node','节点'),('notes','备注')],'content')
    story += [PageBreak(),section('全部沟通记录'),Spacer(1,12)]
    communications=sorted(student.communications,key=lambda r:(r.date or date.min,r.id),reverse=True)
    if not communications: story.append(para('暂无记录'))
    for index,record in enumerate(communications):
        if index: story.append(PageBreak())
        story += communication_story(communication_payload(record,student))
    return build(story, '学生综合归档')
