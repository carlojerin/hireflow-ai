from backend.app.services.resume_parser import extract_resume_text
from backend.app.services.resume_analyzer import analyze_resume


file_path = "data/resumes/Jerin_R_Resume(1).pdf"

resume_text = extract_resume_text(file_path)

result = analyze_resume(resume_text)

print("\n========== AI RESUME ANALYSIS ==========\n")

print("Name:")
print(result["name"])

print("\nEmail:")
print(result["email"])

print("\nPhone:")
print(result["phone"])

print("\nSkills:")
for skill in result["skills"]:
    print(f"- {skill}")

print("\n========== END ANALYSIS ==========\n")