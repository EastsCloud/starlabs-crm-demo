STUDENT_TASK_CATEGORIES = ["申请", "高中GPA", "高中推荐信", "文书", "标化考试", "项目和活动", "其他"]
ADVISOR_TASK_CATEGORIES = ["申请系统", "内容审阅", "沟通", "其他"]
CATEGORIES = STUDENT_TASK_CATEGORIES + [c for c in ADVISOR_TASK_CATEGORIES if c not in STUDENT_TASK_CATEGORIES]

STUDENT_CATEGORY_SLUGS = {
    "application": "申请",
    "gpa": "高中GPA",
    "recommendation": "高中推荐信",
    "essay": "文书",
    "exam": "标化考试",
    "project": "项目和活动",
    "other": "其他",
}

ADVISOR_CATEGORY_SLUGS = {
    "application-system": "申请系统",
    "review": "内容审阅",
    "communication": "沟通",
    "other": "其他",
}

PRIORITIES = ["低", "中", "高"]
TASK_STATUSES = ["WIP", "待确认", "已完成"]
OWNERS = ["学生", "顾问", "家长", "老师", "其他"]
PROJECT_TYPES = ["科研", "公益", "竞赛", "文书作品集", "访校", "其他"]
PROJECT_STATUSES = ["未开始", "进行中", "已完成", "暂停", "取消"]
APPLICATION_TYPES = ["大学", "夏校", "其他"]
APPLICATION_STATUSES = ["WIP", "Submitted", "Accepted", "Conditional", "Waitlisted", "Unsuccessful", "Withdrawn", "Deferred"]
APPLICATION_FORM_STATUSES = ["WIP", "待提交", "已完成"]
PORTAL_PROGRESS_OPTIONS = ["0%", "25%", "50%", "75%", "100%"]
SCORE_DELIVERY_STATUSES = ["WIP", "已完成"]
EXAM_NAMES = ["TOEFL", "SAT", "ACT", "AP"]
IMPORTANT_ITEM_TYPES = ["申请", "任务", "会议", "考试", "项目", "材料", "推荐信", "奖学金", "其他"]
PUBLIC_EVENT_CATEGORIES = ["科研", "竞赛", "考试", "其他"]
PUBLIC_EVENT_REVIEW_STATUSES = ["待确认", "已确认", "已忽略"]
TIMELINE_CATEGORIES = CATEGORIES + [item for item in PUBLIC_EVENT_CATEGORIES if item not in CATEGORIES]
EXAM_STATUSES = ["未开始", "备考中", "已报名", "已考试", "已出分", "取消"]
CONTACT_PEOPLE = ["学生", "家长", "老师", "顾问", "其他"]
METHODS = ["微信", "邮件", "电话", "会议", "其他"]
TIME_NODES = [
    "未添加时间信息",
    "10年级前",
    "10年级上学期",
    "10年级寒假",
    "10年级下学期",
    "10-11暑假",
    "11年级上学期",
    "11年级寒假",
    "11年级下学期",
    "11-12暑假",
    "12年级上学期",
    "12年级上学期后",
]
