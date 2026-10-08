from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, ListFlowable, ListItem,
)
from reportlab.lib.colors import HexColor

OUT_PATH = r"D:\jobpilot-ai\Arjun_Mehta_Web_Developer_Resume.pdf"

INK = HexColor("#111827")
MUTED = HexColor("#4b5563")
ACCENT = HexColor("#635bff")

styles = getSampleStyleSheet()

name_style = ParagraphStyle(
    "NameStyle", parent=styles["Title"], fontSize=22, leading=26,
    textColor=INK, alignment=TA_LEFT, spaceAfter=2,
)
contact_style = ParagraphStyle(
    "ContactStyle", parent=styles["Normal"], fontSize=10, leading=14,
    textColor=MUTED, spaceAfter=10,
)
section_style = ParagraphStyle(
    "SectionStyle", parent=styles["Heading2"], fontSize=12.5, leading=16,
    textColor=ACCENT, spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold",
)
body_style = ParagraphStyle(
    "BodyStyle", parent=styles["Normal"], fontSize=10, leading=14.5,
    textColor=INK, spaceAfter=4,
)
job_title_style = ParagraphStyle(
    "JobTitleStyle", parent=styles["Normal"], fontSize=10.5, leading=14,
    textColor=INK, fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=1,
)
job_meta_style = ParagraphStyle(
    "JobMetaStyle", parent=styles["Normal"], fontSize=9.5, leading=13,
    textColor=MUTED, spaceAfter=4, fontName="Helvetica-Oblique",
)
bullet_style = ParagraphStyle(
    "BulletStyle", parent=styles["Normal"], fontSize=10, leading=14,
    textColor=INK,
)

doc = SimpleDocTemplate(
    OUT_PATH, pagesize=letter,
    leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    topMargin=0.7 * inch, bottomMargin=0.7 * inch,
)

story = []

story.append(Paragraph("Arjun Mehta", name_style))
story.append(Paragraph(
    "Web Developer &nbsp;|&nbsp; carlojerin1@gmail.com &nbsp;|&nbsp; +91 98765 43210 "
    "&nbsp;|&nbsp; Bengaluru, India &nbsp;|&nbsp; github.com/arjunmehta-dev "
    "&nbsp;|&nbsp; linkedin.com/in/arjunmehta-dev",
    contact_style,
))
story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#e5e7eb"), spaceAfter=6))

story.append(Paragraph("SUMMARY", section_style))
story.append(Paragraph(
    "Web Developer with 3+ years of experience building responsive, production-grade web "
    "applications using React, Node.js, and modern JavaScript tooling. Comfortable across "
    "the full stack -- from REST API design and PostgreSQL schemas to pixel-accurate, "
    "accessible front-end UI. Shipped and maintained customer-facing products serving "
    "50,000+ monthly active users.",
    body_style,
))

story.append(Paragraph("TECHNICAL SKILLS", section_style))
story.append(Paragraph(
    "<b>Languages:</b> JavaScript (ES2022), TypeScript, HTML5, CSS3, Python, SQL<br/>"
    "<b>Frontend:</b> React, Redux, Next.js, Tailwind CSS, Bootstrap, Vite, Webpack<br/>"
    "<b>Backend:</b> Node.js, Express, REST APIs, FastAPI (Python)<br/>"
    "<b>Databases:</b> PostgreSQL, MongoDB, Redis<br/>"
    "<b>Tools &amp; Practices:</b> Git/GitHub, Docker, CI/CD (GitHub Actions), Jest, "
    "Cypress, Agile/Scrum, REST API design, Responsive &amp; accessible UI (WCAG 2.1 AA)",
    body_style,
))

story.append(Paragraph("EXPERIENCE", section_style))

story.append(Paragraph("Web Developer &mdash; Nimbus Softworks Pvt. Ltd.", job_title_style))
story.append(Paragraph("Bengaluru, India &nbsp;|&nbsp; June 2023 &ndash; Present", job_meta_style))
story.append(ListFlowable([
    ListItem(Paragraph(
        "Built and shipped a customer-facing React + TypeScript dashboard used by 50,000+ "
        "monthly active users, cutting page load time by 38% through code-splitting and "
        "lazy loading.", bullet_style)),
    ListItem(Paragraph(
        "Designed and implemented 20+ REST API endpoints in Node.js/Express backing the "
        "dashboard, with PostgreSQL schemas and Redis caching for high-traffic routes.",
        bullet_style)),
    ListItem(Paragraph(
        "Introduced a Jest + Cypress test suite (0% to 72% coverage on core flows), "
        "reducing production regressions reported by QA by roughly half.", bullet_style)),
    ListItem(Paragraph(
        "Set up a GitHub Actions CI/CD pipeline (lint, test, build, deploy) that cut "
        "average deploy time from 25 minutes to under 6.", bullet_style)),
], bulletType="bullet", start="circle", leftIndent=16, spaceAfter=8))

story.append(Paragraph("Junior Web Developer &mdash; Brightlane Digital", job_title_style))
story.append(Paragraph("Pune, India &nbsp;|&nbsp; July 2021 &ndash; May 2023", job_meta_style))
story.append(ListFlowable([
    ListItem(Paragraph(
        "Developed responsive marketing sites and landing pages for 15+ client "
        "engagements using React and Tailwind CSS, consistently hitting Lighthouse "
        "performance scores above 90.", bullet_style)),
    ListItem(Paragraph(
        "Collaborated directly with designers to translate Figma mockups into "
        "pixel-accurate, accessible HTML/CSS components.", bullet_style)),
    ListItem(Paragraph(
        "Maintained and extended a shared internal component library adopted across "
        "6 client projects, reducing new-project setup time by roughly 30%.",
        bullet_style)),
], bulletType="bullet", start="circle", leftIndent=16, spaceAfter=8))

story.append(Paragraph("PROJECTS", section_style))
story.append(Paragraph("<b>TaskFlow</b> &mdash; Open-source Kanban board (React, Node.js, MongoDB)", job_title_style))
story.append(Paragraph(
    "Full-stack task management app with drag-and-drop boards, real-time updates via "
    "WebSockets, and JWT-based auth. 300+ GitHub stars.", bullet_style,
))
story.append(Paragraph("<b>DevRate</b> &mdash; Code review analytics tool (Next.js, PostgreSQL)", job_title_style))
story.append(Paragraph(
    "Side project analyzing GitHub pull-request data to surface team code-review "
    "turnaround metrics; built and deployed solo end-to-end.", bullet_style,
))

story.append(Paragraph("EDUCATION", section_style))
story.append(Paragraph(
    "<b>B.Tech in Computer Science</b> &mdash; Visvesvaraya Technological University, "
    "Karnataka &nbsp;|&nbsp; 2017 &ndash; 2021 &nbsp;|&nbsp; CGPA: 8.4/10",
    body_style,
))

story.append(Paragraph("CERTIFICATIONS", section_style))
story.append(Paragraph(
    "Meta Front-End Developer Professional Certificate (Coursera, 2022) &nbsp;|&nbsp; "
    "AWS Certified Cloud Practitioner (2023)",
    body_style,
))

doc.build(story)
print("Wrote", OUT_PATH)
