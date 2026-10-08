from datetime import date, timedelta

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from backend.app.database.database import SessionLocal
from backend.app.models.company import Company
from backend.app.models.job import Job


pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


# =========================================================
# DEMO COMPANIES
# =========================================================

DEMO_COMPANIES = [
    {
        "company_name": "TechNova Solutions",
        "legal_name": "TechNova Solutions Private Limited",
        "email": "hr@technova-demo.com",
        "mobile": "9000000001",
        "company_type": "Private Limited",
        "industry": "Information Technology",
        "website": "https://technova.example.com",
        "location": "Chennai, Tamil Nadu",
        "company_size": "201-500",
        "founded_year": 2018,
        "description": "A technology company building cloud, software and AI solutions for modern businesses."
    },
    {
        "company_name": "CodeBridge Technologies",
        "legal_name": "CodeBridge Technologies Private Limited",
        "email": "hr@codebridge-demo.com",
        "mobile": "9000000002",
        "company_type": "Private Limited",
        "industry": "Information Technology",
        "website": "https://codebridge.example.com",
        "location": "Bengaluru, Karnataka",
        "company_size": "51-200",
        "founded_year": 2020,
        "description": "A software engineering company focused on scalable web, backend and cloud applications."
    },
    {
        "company_name": "DataPeak Analytics",
        "legal_name": "DataPeak Analytics Private Limited",
        "email": "hr@datapeak-demo.com",
        "mobile": "9000000003",
        "company_type": "Private Limited",
        "industry": "Data & Analytics",
        "website": "https://datapeak.example.com",
        "location": "Hyderabad, Telangana",
        "company_size": "51-200",
        "founded_year": 2019,
        "description": "An analytics company helping organizations make better decisions through data and AI."
    },
    {
        "company_name": "CloudSphere Systems",
        "legal_name": "CloudSphere Systems Private Limited",
        "email": "hr@cloudsphere-demo.com",
        "mobile": "9000000004",
        "company_type": "Private Limited",
        "industry": "Cloud Computing",
        "website": "https://cloudsphere.example.com",
        "location": "Pune, Maharashtra",
        "company_size": "201-500",
        "founded_year": 2017,
        "description": "A cloud technology company delivering infrastructure, DevOps and platform engineering solutions."
    },
    {
        "company_name": "FinEdge Technologies",
        "legal_name": "FinEdge Technologies Private Limited",
        "email": "hr@finedge-demo.com",
        "mobile": "9000000005",
        "company_type": "Private Limited",
        "industry": "Financial Technology",
        "website": "https://finedge.example.com",
        "location": "Mumbai, Maharashtra",
        "company_size": "501-1000",
        "founded_year": 2016,
        "description": "A fintech organization developing secure digital products for payments, lending and financial services."
    },
    {
        "company_name": "NextGen Digital",
        "legal_name": "NextGen Digital Solutions Private Limited",
        "email": "hr@nextgen-demo.com",
        "mobile": "9000000006",
        "company_type": "Private Limited",
        "industry": "Digital Services",
        "website": "https://nextgen.example.com",
        "location": "Chennai, Tamil Nadu",
        "company_size": "51-200",
        "founded_year": 2021,
        "description": "A digital product company creating customer-focused web and mobile experiences."
    },
    {
        "company_name": "InnovaSoft Labs",
        "legal_name": "InnovaSoft Labs Private Limited",
        "email": "hr@innovasoft-demo.com",
        "mobile": "9000000007",
        "company_type": "Private Limited",
        "industry": "Software Development",
        "website": "https://innovasoft.example.com",
        "location": "Bengaluru, Karnataka",
        "company_size": "11-50",
        "founded_year": 2022,
        "description": "A software startup building developer tools, SaaS products and automation platforms."
    },
    {
        "company_name": "AIWorks India",
        "legal_name": "AIWorks India Private Limited",
        "email": "hr@aiworks-demo.com",
        "mobile": "9000000008",
        "company_type": "Private Limited",
        "industry": "Artificial Intelligence",
        "website": "https://aiworks.example.com",
        "location": "Hyderabad, Telangana",
        "company_size": "51-200",
        "founded_year": 2019,
        "description": "An AI-focused company working on machine learning, NLP and intelligent automation solutions."
    },
    {
        "company_name": "WebCraft Studios",
        "legal_name": "WebCraft Studios Private Limited",
        "email": "hr@webcraft-demo.com",
        "mobile": "9000000009",
        "company_type": "Private Limited",
        "industry": "Web Development",
        "website": "https://webcraft.example.com",
        "location": "Coimbatore, Tamil Nadu",
        "company_size": "11-50",
        "founded_year": 2020,
        "description": "A web development studio creating modern e-commerce, SaaS and enterprise web applications."
    },
    {
        "company_name": "SecureNet Systems",
        "legal_name": "SecureNet Systems Private Limited",
        "email": "hr@securenet-demo.com",
        "mobile": "9000000010",
        "company_type": "Private Limited",
        "industry": "Cybersecurity",
        "website": "https://securenet.example.com",
        "location": "Pune, Maharashtra",
        "company_size": "201-500",
        "founded_year": 2015,
        "description": "A cybersecurity company delivering security monitoring, cloud protection and application security solutions."
    },
    {
        "company_name": "GreenGrid Technologies",
        "legal_name": "GreenGrid Technologies Private Limited",
        "email": "hr@greengrid-demo.com",
        "mobile": "9000000011",
        "company_type": "Private Limited",
        "industry": "Clean Technology",
        "website": "https://greengrid.example.com",
        "location": "Delhi, India",
        "company_size": "51-200",
        "founded_year": 2018,
        "description": "A technology company developing digital products for energy efficiency and sustainable operations."
    },
    {
        "company_name": "HealthStack Digital",
        "legal_name": "HealthStack Digital Private Limited",
        "email": "hr@healthstack-demo.com",
        "mobile": "9000000012",
        "company_type": "Private Limited",
        "industry": "Health Technology",
        "website": "https://healthstack.example.com",
        "location": "Bengaluru, Karnataka",
        "company_size": "51-200",
        "founded_year": 2019,
        "description": "A health technology company building digital platforms for healthcare providers and patients."
    },
    {
        "company_name": "RetailFlow Technologies",
        "legal_name": "RetailFlow Technologies Private Limited",
        "email": "hr@retailflow-demo.com",
        "mobile": "9000000013",
        "company_type": "Private Limited",
        "industry": "E-commerce",
        "website": "https://retailflow.example.com",
        "location": "Mumbai, Maharashtra",
        "company_size": "201-500",
        "founded_year": 2017,
        "description": "An e-commerce technology company building scalable retail, inventory and customer experience platforms."
    },
    {
        "company_name": "EduSphere Technologies",
        "legal_name": "EduSphere Technologies Private Limited",
        "email": "hr@edusphere-demo.com",
        "mobile": "9000000014",
        "company_type": "Private Limited",
        "industry": "EdTech",
        "website": "https://edusphere.example.com",
        "location": "Chennai, Tamil Nadu",
        "company_size": "51-200",
        "founded_year": 2020,
        "description": "An education technology company building learning platforms and digital classroom solutions."
    },
    {
        "company_name": "LogiTrack Systems",
        "legal_name": "LogiTrack Systems Private Limited",
        "email": "hr@logitrack-demo.com",
        "mobile": "9000000015",
        "company_type": "Private Limited",
        "industry": "Logistics Technology",
        "website": "https://logitrack.example.com",
        "location": "Bengaluru, Karnataka",
        "company_size": "201-500",
        "founded_year": 2016,
        "description": "A logistics technology company using software and analytics to improve supply-chain operations."
    },
    {
        "company_name": "MediaPulse Interactive",
        "legal_name": "MediaPulse Interactive Private Limited",
        "email": "hr@mediapulse-demo.com",
        "mobile": "9000000016",
        "company_type": "Private Limited",
        "industry": "Media Technology",
        "website": "https://mediapulse.example.com",
        "location": "Mumbai, Maharashtra",
        "company_size": "51-200",
        "founded_year": 2018,
        "description": "A digital media company creating content technology, audience analytics and interactive platforms."
    },
    {
        "company_name": "AutoMatrix Technologies",
        "legal_name": "AutoMatrix Technologies Private Limited",
        "email": "hr@automatrix-demo.com",
        "mobile": "9000000017",
        "company_type": "Private Limited",
        "industry": "Automation",
        "website": "https://automatrix.example.com",
        "location": "Pune, Maharashtra",
        "company_size": "51-200",
        "founded_year": 2019,
        "description": "An automation technology company building intelligent software and industrial automation products."
    },
    {
        "company_name": "BrightCore Solutions",
        "legal_name": "BrightCore Solutions Private Limited",
        "email": "hr@brightcore-demo.com",
        "mobile": "9000000018",
        "company_type": "Private Limited",
        "industry": "IT Services",
        "website": "https://brightcore.example.com",
        "location": "Kochi, Kerala",
        "company_size": "11-50",
        "founded_year": 2021,
        "description": "An IT services company delivering software development, consulting and cloud transformation solutions."
    },
    {
        "company_name": "QuantumByte Technologies",
        "legal_name": "QuantumByte Technologies Private Limited",
        "email": "hr@quantumbyte-demo.com",
        "mobile": "9000000019",
        "company_type": "Private Limited",
        "industry": "Technology",
        "website": "https://quantumbyte.example.com",
        "location": "Hyderabad, Telangana",
        "company_size": "201-500",
        "founded_year": 2016,
        "description": "A technology company focused on software engineering, cloud platforms and advanced computing."
    },
    {
        "company_name": "Skyline Data Systems",
        "legal_name": "Skyline Data Systems Private Limited",
        "email": "hr@skyline-demo.com",
        "mobile": "9000000020",
        "company_type": "Private Limited",
        "industry": "Data Engineering",
        "website": "https://skyline.example.com",
        "location": "Noida, Uttar Pradesh",
        "company_size": "51-200",
        "founded_year": 2018,
        "description": "A data engineering company building modern data platforms, pipelines and business intelligence solutions."
    }
]


# =========================================================
# JOB TEMPLATES
# =========================================================

JOB_TEMPLATES = [
    {
        "title": "Python Developer",
        "experience": "1-3 years",
        "education": "Bachelor's degree in Computer Science or related field",
        "required_skills": "Python, SQL, REST API, Git",
        "preferred_skills": "FastAPI, Django, Docker",
        "technologies": "Python, FastAPI, PostgreSQL, Git, Docker",
        "salary_min": 500000,
        "salary_max": 900000,
        "employment_type": "Full Time"
    },
    {
        "title": "Java Developer",
        "experience": "2-4 years",
        "education": "Bachelor's degree in Computer Science or related field",
        "required_skills": "Java, Spring Boot, SQL, Git",
        "preferred_skills": "Microservices, Docker, AWS",
        "technologies": "Java, Spring Boot, MySQL, Docker, AWS",
        "salary_min": 600000,
        "salary_max": 1000000,
        "employment_type": "Full Time"
    },
    {
        "title": "React Developer",
        "experience": "1-3 years",
        "education": "Bachelor's degree or equivalent",
        "required_skills": "JavaScript, React, HTML, CSS",
        "preferred_skills": "TypeScript, Redux, Next.js",
        "technologies": "React, JavaScript, TypeScript, HTML, CSS",
        "salary_min": 450000,
        "salary_max": 850000,
        "employment_type": "Full Time"
    },
    {
        "title": "Full Stack Developer",
        "experience": "2-5 years",
        "education": "Bachelor's degree in Computer Science or related field",
        "required_skills": "JavaScript, Python, SQL, REST API",
        "preferred_skills": "React, Node.js, Docker, AWS",
        "technologies": "React, Node.js, Python, PostgreSQL, AWS",
        "salary_min": 700000,
        "salary_max": 1200000,
        "employment_type": "Full Time"
    },
    {
        "title": "Data Analyst",
        "experience": "1-3 years",
        "education": "Bachelor's degree in Data Science, Statistics, Mathematics or related field",
        "required_skills": "SQL, Excel, Data Analysis, Python",
        "preferred_skills": "Power BI, Tableau, Pandas",
        "technologies": "Python, Pandas, SQL, Power BI, Excel",
        "salary_min": 450000,
        "salary_max": 800000,
        "employment_type": "Full Time"
    },
    {
        "title": "Data Scientist",
        "experience": "2-5 years",
        "education": "Bachelor's or Master's degree in Data Science, Statistics or Computer Science",
        "required_skills": "Python, SQL, Machine Learning, Statistics",
        "preferred_skills": "Scikit-learn, TensorFlow, NLP",
        "technologies": "Python, Scikit-learn, TensorFlow, Pandas, SQL",
        "salary_min": 800000,
        "salary_max": 1500000,
        "employment_type": "Full Time"
    },
    {
        "title": "Machine Learning Engineer",
        "experience": "2-5 years",
        "education": "Bachelor's or Master's degree in Computer Science or AI",
        "required_skills": "Python, Machine Learning, SQL, Statistics",
        "preferred_skills": "TensorFlow, PyTorch, NLP",
        "technologies": "Python, PyTorch, TensorFlow, MLflow, Docker",
        "salary_min": 900000,
        "salary_max": 1600000,
        "employment_type": "Full Time"
    },
    {
        "title": "DevOps Engineer",
        "experience": "2-5 years",
        "education": "Bachelor's degree in Computer Science or related field",
        "required_skills": "Linux, Docker, CI/CD, Git",
        "preferred_skills": "Kubernetes, AWS, Terraform",
        "technologies": "Linux, Docker, Kubernetes, AWS, Terraform",
        "salary_min": 700000,
        "salary_max": 1300000,
        "employment_type": "Full Time"
    },
    {
        "title": "Cloud Engineer",
        "experience": "2-5 years",
        "education": "Bachelor's degree in Computer Science or related field",
        "required_skills": "AWS, Linux, Networking, Cloud",
        "preferred_skills": "Azure, Terraform, Kubernetes",
        "technologies": "AWS, Linux, Terraform, Kubernetes",
        "salary_min": 750000,
        "salary_max": 1400000,
        "employment_type": "Full Time"
    },
    {
        "title": "QA Automation Engineer",
        "experience": "1-4 years",
        "education": "Bachelor's degree or equivalent",
        "required_skills": "Selenium, Python, Testing, Git",
        "preferred_skills": "PyTest, Playwright, CI/CD",
        "technologies": "Python, Selenium, PyTest, Playwright",
        "salary_min": 450000,
        "salary_max": 850000,
        "employment_type": "Full Time"
    },
    {
        "title": "Backend Developer",
        "experience": "2-4 years",
        "education": "Bachelor's degree in Computer Science or related field",
        "required_skills": "Python, APIs, SQL, Git",
        "preferred_skills": "FastAPI, PostgreSQL, Redis",
        "technologies": "Python, FastAPI, PostgreSQL, Redis, Docker",
        "salary_min": 600000,
        "salary_max": 1100000,
        "employment_type": "Full Time"
    },
    {
        "title": "Frontend Developer",
        "experience": "1-3 years",
        "education": "Bachelor's degree or equivalent",
        "required_skills": "JavaScript, HTML, CSS, Git",
        "preferred_skills": "React, TypeScript, Bootstrap",
        "technologies": "JavaScript, React, TypeScript, HTML, CSS",
        "salary_min": 400000,
        "salary_max": 800000,
        "employment_type": "Full Time"
    },
    {
        "title": "UI/UX Designer",
        "experience": "1-4 years",
        "education": "Bachelor's degree or design qualification",
        "required_skills": "Figma, UI Design, UX Research, Prototyping",
        "preferred_skills": "Design Systems, HTML, CSS",
        "technologies": "Figma, Adobe XD, Photoshop",
        "salary_min": 400000,
        "salary_max": 750000,
        "employment_type": "Full Time"
    },
    {
        "title": "Cybersecurity Analyst",
        "experience": "1-4 years",
        "education": "Bachelor's degree in Cybersecurity, IT or Computer Science",
        "required_skills": "Network Security, Linux, SIEM, Security Analysis",
        "preferred_skills": "AWS Security, Python, SOC",
        "technologies": "Linux, SIEM, Python, AWS",
        "salary_min": 550000,
        "salary_max": 1000000,
        "employment_type": "Full Time"
    },
    {
        "title": "Business Analyst",
        "experience": "2-5 years",
        "education": "Bachelor's degree in Business, IT or related field",
        "required_skills": "Requirements Gathering, SQL, Excel, Communication",
        "preferred_skills": "Power BI, Agile, Jira",
        "technologies": "SQL, Excel, Power BI, Jira",
        "salary_min": 500000,
        "salary_max": 950000,
        "employment_type": "Full Time"
    },
    {
        "title": "Product Manager",
        "experience": "4-7 years",
        "education": "Bachelor's degree or MBA",
        "required_skills": "Product Management, Agile, Analytics, Communication",
        "preferred_skills": "SaaS, AI Products, SQL",
        "technologies": "Jira, SQL, Analytics, Figma",
        "salary_min": 1000000,
        "salary_max": 1800000,
        "employment_type": "Full Time"
    },
    {
        "title": "Mobile App Developer",
        "experience": "1-4 years",
        "education": "Bachelor's degree or equivalent",
        "required_skills": "Flutter, Dart, REST API, Git",
        "preferred_skills": "Firebase, Android, iOS",
        "technologies": "Flutter, Dart, Firebase",
        "salary_min": 500000,
        "salary_max": 950000,
        "employment_type": "Full Time"
    },
    {
        "title": "Cloud Support Engineer",
        "experience": "1-3 years",
        "education": "Bachelor's degree in Computer Science or IT",
        "required_skills": "Linux, AWS, Networking, Troubleshooting",
        "preferred_skills": "Azure, Docker, Python",
        "technologies": "AWS, Linux, Docker, Python",
        "salary_min": 450000,
        "salary_max": 800000,
        "employment_type": "Full Time"
    },
    {
        "title": "NLP Engineer",
        "experience": "2-5 years",
        "education": "Bachelor's or Master's degree in AI, Data Science or Computer Science",
        "required_skills": "Python, NLP, Machine Learning, Transformers",
        "preferred_skills": "LLMs, PyTorch, Hugging Face",
        "technologies": "Python, PyTorch, Transformers, Hugging Face",
        "salary_min": 900000,
        "salary_max": 1700000,
        "employment_type": "Full Time"
    },
    {
        "title": "Software Engineer",
        "experience": "2-5 years",
        "education": "Bachelor's degree in Computer Science or related field",
        "required_skills": "Programming, SQL, Git, Problem Solving",
        "preferred_skills": "Python, Java, Cloud, Docker",
        "technologies": "Python, Java, SQL, Git, Docker",
        "salary_min": 600000,
        "salary_max": 1100000,
        "employment_type": "Full Time"
    }
]


# =========================================================
# HELPERS
# =========================================================

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def company_exists(
    db: Session,
    email: str,
    mobile: str
):
    return (
        db.query(Company)
        .filter(
            (Company.email == email)
            | (Company.mobile == mobile)
        )
        .first()
    )


# =========================================================
# CREATE DEMO COMPANIES
# =========================================================

def create_demo_companies(
    db: Session
):
    companies = []

    for company_data in DEMO_COMPANIES:

        existing = company_exists(
            db,
            company_data["email"],
            company_data["mobile"]
        )

        if existing:
            companies.append(existing)
            continue

        company = Company(
            company_name=company_data["company_name"],
            legal_name=company_data["legal_name"],
            email=company_data["email"],
            mobile=company_data["mobile"],
            hashed_password=hash_password("Company@123"),
            company_type=company_data["company_type"],
            industry=company_data["industry"],
            website=company_data["website"],
            location=company_data["location"],
            description=company_data["description"],
            company_size=company_data["company_size"],
            founded_year=company_data["founded_year"],
            verification_status="verified",
            account_active=True,
            email_verified=False,
            mobile_verified=False,
            admin_verified=True,
            company_logo=None,
            role="company"
        )

        db.add(company)
        db.flush()

        companies.append(company)

    return companies


# =========================================================
# CREATE 100 JOBS
# =========================================================

def create_demo_jobs(
    db: Session,
    companies: list[Company]
):

    existing_internal_jobs = (
        db.query(Job)
        .filter(
            Job.source == "internal"
        )
        .count()
    )

    if existing_internal_jobs >= 100:

        print(
            "[SEED] 100 internal jobs already exist."
        )

        return 0

    # If fewer than 100 exist, create only enough
    # jobs to reach 100.

    jobs_needed = (
        100 - existing_internal_jobs
    )

    created_count = 0

    start_date = date.today()

    for company_index, company in enumerate(companies):

        for job_offset in range(5):

            if created_count >= jobs_needed:
                return created_count

            template_index = (
                (company_index * 5 + job_offset)
                % len(JOB_TEMPLATES)
            )

            template = JOB_TEMPLATES[
                template_index
            ]

            days_ago = (
                company_index +
                job_offset
            ) % 20

            posted_date = (
                start_date -
                timedelta(
                    days=days_ago
                )
            )

            description = (
                f"{company.company_name} is looking for "
                f"an experienced {template['title']} "
                f"to join our growing technology team. "
                f"The successful candidate will work "
                f"on real-world products, collaborate "
                f"with cross-functional teams and "
                f"contribute to scalable solutions."
            )

            responsibilities = (
                f"Design and develop solutions related "
                f"to the {template['title']} role; "
                f"collaborate with engineering and "
                f"product teams; write maintainable code; "
                f"participate in testing, code reviews "
                f"and continuous improvement."
            )

            requirements = (
                f"Strong knowledge of "
                f"{template['required_skills']}. "
                f"Good communication and problem-solving "
                f"skills are expected."
            )

            benefits = (
                "Competitive salary, learning opportunities, "
                "health benefits, flexible work environment "
                "and career growth opportunities."
            )

            work_modes = [
                "Hybrid",
                "Remote",
                "On-site"
            ]

            job = Job(
                company_id=company.id,
                title=template["title"],
                company=company.company_name,
                location=company.location,
                work_mode=work_modes[
                    job_offset % 3
                ],
                employment_type=template["employment_type"],
                salary=(
                    f"₹{template['salary_min']:,} - "
                    f"₹{template['salary_max']:,}"
                ),
                salary_min=template["salary_min"],
                salary_max=template["salary_max"],
                salary_currency="INR",
                experience=template["experience"],
                education=template["education"],
                required_skills=template["required_skills"],
                preferred_skills=template["preferred_skills"],
                technologies=template["technologies"],
                description=description,
                responsibilities=responsibilities,
                requirements=requirements,
                benefits=benefits,
                openings=1 + (job_offset % 4),
                status="active",
                source="internal",
                posted_date=posted_date.isoformat(),
                application_deadline=(
                    start_date +
                    timedelta(
                        days=30 + job_offset
                    )
                ).isoformat(),
                job_url="",
                application_url=None
            )

            db.add(job)

            created_count += 1

    return created_count


# =========================================================
# SEED DEMO DATA
# =========================================================

def seed_demo_data():

    db = SessionLocal()

    try:

        print(
            "[SEED] Starting JobPilot demo data..."
        )

        companies = create_demo_companies(
            db
        )

        created_jobs = create_demo_jobs(
            db,
            companies
        )

        db.commit()

        total_companies = (
            db.query(Company).count()
        )

        total_internal_jobs = (
            db.query(Job)
            .filter(
                Job.source == "internal"
            )
            .count()
        )

        print(
            f"[SEED] Companies available: "
            f"{total_companies}"
        )

        print(
            f"[SEED] Internal jobs available: "
            f"{total_internal_jobs}"
        )

        print(
            f"[SEED] Jobs created in this run: "
            f"{created_jobs}"
        )

        print(
            "[SEED] Demo data completed successfully."
        )

    except Exception as error:

        db.rollback()

        print(
            "[SEED] ERROR:",
            error
        )

        raise

    finally:

        db.close()


# =========================================================
# RUN DIRECTLY
# =========================================================

if __name__ == "__main__":

    seed_demo_data()