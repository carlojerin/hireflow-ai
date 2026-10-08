import re


def extract_skills(text: str) -> list[str]:
    known_skills = [
        "Python",
        "Java",
        "JavaScript",
        "HTML",
        "CSS",
        "SQL",
        "Flask",
        "FastAPI",
        "Django",
        "Machine Learning",
        "Deep Learning",
        "Artificial Intelligence",
        "Generative AI",
        "Gemini API",
        "OpenAI API",
        "SQLite",
        "MySQL",
        "PostgreSQL",
        "Git",
        "GitHub",
        "Docker",
        "AWS",
        "React",
        "Node.js",
        "Pandas",
        "NumPy",
        "Scikit-learn",
        "TensorFlow",
        "PyTorch"
    ]

    text_lower = text.lower()

    found_skills = []

    for skill in known_skills:
        if skill.lower() in text_lower:
            found_skills.append(skill)

    return found_skills


def extract_email(text: str) -> str | None:
    pattern = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

    match = re.search(pattern, text)

    if match:
        return match.group(0)

    return None


def extract_phone(text: str) -> str | None:
    pattern = r"\b\d{10}\b"

    match = re.search(pattern, text)

    if match:
        return match.group(0)

    return None


def extract_name(text: str) -> str | None:
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        return None

    return lines[0]


def analyze_resume(text: str) -> dict:
    skills = extract_skills(text)

    return {
        "name": extract_name(text),
        "email": extract_email(text),
        "phone": extract_phone(text),
        "skills": skills,
        "resume_text": text
    }