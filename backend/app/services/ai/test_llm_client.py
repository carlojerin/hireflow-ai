from backend.app.services.ai.llm_client import ask_gemini


print("\n========== GEMINI TEST ==========\n")

try:
    response = ask_gemini(
        "Reply with exactly these words: "
        "JobPilot AI connection successful"
    )

    print("Gemini response:")
    print(response)

    print("\n========== GEMINI CONNECTION SUCCESS ==========\n")

except Exception as e:

    print("Gemini connection failed.")
    print("Error:", type(e).__name__)
    print("Details:", str(e))

    print("\n========== GEMINI CONNECTION FAILED ==========\n")