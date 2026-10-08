from backend.app.services.resume_parser import extract_resume_text


file_path = "data/resumes/Jerin_R_Resume(1).pdf"

text = extract_resume_text(file_path)

print("\n========== RESUME TEXT ==========\n")
print(text)
print("\n========== END RESUME TEXT ==========\n")