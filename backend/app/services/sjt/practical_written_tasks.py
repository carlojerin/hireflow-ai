"""
PRACTICAL "WRITTEN" ENVIRONMENT: the candidate does a real piece of the job in writing (reply to a customer, write a campaign brief, explain a
variance to a manager) and an AI scores it against a rubric.

* The tasks are written for THIS job by the AI planner's model (one small LLM call per candidate), with a curated bank per role family as the
  fallback, so a candidate always gets a test.
* The grading is AI-assisted, never the only judge: HR always sees the candidate's own text next to the AI's per-criterion notes. When the AI
  is unavailable a plain length/structure check is used and the result is labelled "needs human review".
"""

import json
import logging
import re

logger = logging.getLogger("jobpilot.sjt.practical")

WRITTEN_TASKS = 2
MIN_WORDS_FLOOR, MAX_WORDS_CEILING = 20, 600
GENERATE_TIMEOUT_SECONDS = 25
GRADE_TIMEOUT_SECONDS = 25


def _clean(text, limit: int) -> str:
    text = re.sub(r"[‐-―−]", "-", str(text or ""))
    return re.sub(r"[ \t]+", " ", text).strip()[:limit]


# ------------------------------------------------------------------ curated fallback bank (two tasks per role family)

def _task(title, scenario, instructions, rubric, min_words=60, max_words=250, category="Practical task"):
    return {"title": title, "scenario": scenario, "instructions": instructions, "rubric": rubric, "min_words": min_words, "max_words": max_words, "category": category}


_BANK = {
    "support": [
        _task("Reply to an upset customer",
              "A customer writes: \"I ordered a blender 9 days ago and it still has not arrived. Your tracking page says 'in transit' for 6 days. I paid extra for express delivery. "
              "This is the second time you have let me down. I want my money back or I will leave a bad review.\" The order is real, the courier has confirmed a delay at their hub, "
              "and an express fee of Rs 199 can be refunded.",
              "Write the email you would send to this customer.",
              [{"criterion": "Acknowledges the problem and apologises sincerely, without making excuses", "weight": 3},
               {"criterion": "Offers a clear solution (refund of the express fee and/or a firm delivery date) within the facts given", "weight": 3},
               {"criterion": "Calm, professional and warm tone with correct grammar", "weight": 2},
               {"criterion": "States next steps and what the customer can expect, concisely", "weight": 2}]),
        _task("Escalation note to your manager",
              "Three customers this morning reported being charged twice for the same subscription renewal. All three paid by the same payment gateway between 9:00 and 9:40 AM. "
              "You have refunded none of them yet.",
              "Write a short message to your manager that raises this issue and says what you need from them.",
              [{"criterion": "States the problem and its scope (who, when, how many) clearly", "weight": 3},
               {"criterion": "Proposes or asks for a concrete next step and says what is needed", "weight": 3},
               {"criterion": "Concise and well structured, easy to act on", "weight": 2},
               {"criterion": "Professional tone and correct grammar", "weight": 2}], min_words=40, max_words=180),
    ],
    "sales": [
        _task("Cold outreach email",
              "You sell a cloud-based attendance and payroll tool to small manufacturing companies (50 to 300 employees). Your prospect is Mr. Rao, HR Manager at a 120-person "
              "auto-parts factory. His company posted three payroll-related job openings this month and still runs payroll in spreadsheets.",
              "Write the first email you would send to Mr. Rao. Keep it short.",
              [{"criterion": "Personalised to the prospect's situation, not a generic template", "weight": 3},
               {"criterion": "Clear value for him (time saved, errors avoided) without exaggerated claims", "weight": 3},
               {"criterion": "One clear, low-pressure call to action", "weight": 2},
               {"criterion": "Concise, professional, correct grammar", "weight": 2}], min_words=50, max_words=170),
        _task("Handle a price objection",
              "A prospect says: \"We like the product, but your price is 25% higher than your competitor's. Why should we pay more?\" Your product has free onboarding, "
              "a dedicated support person and no setup fee. The competitor charges separately for onboarding and support.",
              "Write what you would reply to the prospect.",
              [{"criterion": "Acknowledges the concern without becoming defensive", "weight": 3},
               {"criterion": "Reframes the comparison around total value using only the facts given", "weight": 3},
               {"criterion": "Moves the conversation forward with a sensible next step or question", "weight": 2},
               {"criterion": "Professional tone and clear writing", "weight": 2}], min_words=50, max_words=180),
    ],
    "hr": [
        _task("Shortlisting decision",
              "Role: Customer Support Executive (2 years experience preferred, good written English, night shifts). Candidate A: 3 years in a call centre, average written English, open to night shifts. "
              "Candidate B: 1 year in support chat, excellent written English, cannot work nights. Candidate C: fresher, strong communication, open to nights, completed a customer-service course.",
              "Which candidate would you move forward first, and why? Mention one risk with your choice and what you would check in the interview.",
              [{"criterion": "Makes a clear decision and ties it to the stated job requirements", "weight": 3},
               {"criterion": "Identifies a real risk and a relevant check for the interview", "weight": 3},
               {"criterion": "Fair, evidence-based reasoning (no assumptions about the person)", "weight": 2},
               {"criterion": "Clear, concise professional writing", "weight": 2}], min_words=60, max_words=220),
        _task("Rejection email",
              "You interviewed a candidate named Priya for an Accounts Executive role. She was friendly and well prepared but the team selected a candidate with more Tally experience. "
              "The company would be glad to keep her in mind for a future opening.",
              "Write the email you would send to Priya.",
              [{"criterion": "Clear that she was not selected, without false hope or vague wording", "weight": 3},
               {"criterion": "Respectful, warm and specific (thanks her for something real)", "weight": 3},
               {"criterion": "Leaves the door open appropriately", "weight": 2},
               {"criterion": "Professional tone and correct grammar", "weight": 2}], min_words=50, max_words=170),
    ],
    "finance": [
        _task("Explain a variance to the manager",
              "Monthly report: travel expenses were Rs 1,80,000 against a budget of Rs 1,20,000 (50% over). The extra Rs 60,000 came from two client visits that were added mid-month "
              "(Rs 45,000) and an airfare price rise on a booking made late (Rs 15,000).",
              "Write a short email to your finance manager explaining the variance and what you suggest.",
              [{"criterion": "Explains the variance accurately using the figures given", "weight": 3},
               {"criterion": "Suggests a practical next step (approval process, forecasting, budget revision)", "weight": 3},
               {"criterion": "Clear, concise and well organised", "weight": 2},
               {"criterion": "Professional tone and correct grammar", "weight": 2}], min_words=50, max_words=180),
        _task("Chase a late payment",
              "A customer, Sharma Traders, has an invoice of Rs 85,000 that was due 21 days ago. They have paid on time for two years and have not replied to your first reminder.",
              "Write the second reminder email.",
              [{"criterion": "Firm but polite, appropriate for a good long-term customer", "weight": 3},
               {"criterion": "States the invoice facts clearly (amount, days overdue) and a specific request with a date", "weight": 3},
               {"criterion": "Offers to help resolve any problem", "weight": 2},
               {"criterion": "Professional tone and correct grammar", "weight": 2}], min_words=50, max_words=160),
    ],
    "marketing": [
        _task("Campaign brief",
              "A local coaching institute wants to get 200 new student enquiries in 30 days for its new weekend data-skills course. Budget: Rs 50,000. Audience: working professionals and final-year students in one city.",
              "Write a short campaign brief: goal, audience, 2 or 3 channels with a reason for each, and how you would measure success.",
              [{"criterion": "Sets a clear goal and describes the audience specifically", "weight": 3},
               {"criterion": "Channel choices are sensible for the audience and budget, with reasons", "weight": 3},
               {"criterion": "Defines measurable success metrics", "weight": 2},
               {"criterion": "Clear structure and professional writing", "weight": 2}], min_words=80, max_words=280),
        _task("Write the social post",
              "A cafe is launching a monsoon menu with a hot masala chai and pakoda combo for Rs 149, available from 4 PM to 7 PM for two weeks.",
              "Write one Instagram caption and one short email subject line for this launch.",
              [{"criterion": "Engaging, on-brand copy that includes the key offer details accurately", "weight": 3},
               {"criterion": "A clear call to action", "weight": 2},
               {"criterion": "Tone suited to the platform; concise", "weight": 3},
               {"criterion": "Correct grammar and spelling", "weight": 2}], min_words=20, max_words=120),
    ],
    "writing": [
        _task("Edit this paragraph",
              "Original: \"Our company are providing a wide variety of innovative solutions which are helping the businesses to grow up faster. We have been in market since 2015 and our clients "
              "is very happy with us. Contact to us today for getting a free quotation.\"",
              "Rewrite this paragraph so it is correct, clear and professional. Keep the same facts.",
              [{"criterion": "Fixes all grammar and usage errors", "weight": 3},
               {"criterion": "Clear, concise and natural wording", "weight": 3},
               {"criterion": "Keeps the facts accurate (no invented claims)", "weight": 2},
               {"criterion": "Professional, consistent tone", "weight": 2}], min_words=30, max_words=120),
        _task("Short article intro",
              "Topic: \"Five habits that help remote teams communicate better\". Audience: managers at small companies.",
              "Write the introduction (the first paragraph) of this article. It should make the reader want to continue.",
              [{"criterion": "Engaging opening that fits the audience", "weight": 3},
               {"criterion": "Clear structure and flow", "weight": 2},
               {"criterion": "Correct grammar, varied sentences, no filler", "weight": 3},
               {"criterion": "Stays on the topic and sets up the article", "weight": 2}], min_words=60, max_words=200),
    ],
    "operations": [
        _task("Daily status update",
              "Today: 140 of 150 orders were dispatched on time. 10 were delayed because a courier pickup truck broke down at 2 PM. The truck was replaced at 5 PM, and the 10 orders will leave tomorrow morning. "
              "Tomorrow's expected volume is 160 orders.",
              "Write the status update you would send to your operations manager.",
              [{"criterion": "Accurate and complete with the figures given (what happened, how many, what is next)", "weight": 3},
               {"criterion": "States the risk for tomorrow and an action", "weight": 3},
               {"criterion": "Concise and easy to scan", "weight": 2},
               {"criterion": "Professional tone and correct grammar", "weight": 2}], min_words=40, max_words=170),
        _task("Prioritise your day",
              "You have five tasks and four hours: (1) a vendor invoice due for payment today, (2) a client asks for a delivery update by noon, (3) update the weekly report due Friday, "
              "(4) book a meeting room for next week, (5) your manager asks for a quick figure for a 3 PM review.",
              "Put the tasks in the order you would do them and explain your reasoning briefly.",
              [{"criterion": "A sensible order based on deadlines and impact", "weight": 4},
               {"criterion": "Reasoning is clear and mentions trade-offs", "weight": 3},
               {"criterion": "Concise, well-organised writing", "weight": 3}], min_words=40, max_words=160),
    ],
    "mis": [
        _task("Explain the numbers",
              "Weekly report: sales of Rs 12.4 lakh against a target of Rs 15 lakh (83%). Last week was Rs 14.2 lakh. The drop came mostly from the West region (down 35%) because two key customers delayed their orders to next week. "
              "Other regions were flat.",
              "Write a short summary for the management team: what happened, why, and what to watch next week.",
              [{"criterion": "Accurate, correctly interprets the figures", "weight": 3},
               {"criterion": "Identifies the cause and a sensible watch-point", "weight": 3},
               {"criterion": "Clear, short and structured for busy readers", "weight": 2},
               {"criterion": "Professional tone and correct grammar", "weight": 2}], min_words=50, max_words=170),
        _task("Request missing data",
              "You are preparing the monthly MIS report and the East region has not sent its numbers, the deadline is tomorrow 11 AM, and the regional manager has been slow to respond before.",
              "Write the message you would send to the regional manager.",
              [{"criterion": "Clear and specific request (what, by when, in what format)", "weight": 3},
               {"criterion": "Polite but firm, mentions the deadline and its importance", "weight": 3},
               {"criterion": "Concise", "weight": 2},
               {"criterion": "Professional tone and correct grammar", "weight": 2}], min_words=30, max_words=140),
    ],
    "design": [
        _task("Critique a design brief",
              "Brief: \"Make our app look modern and fun.\" The client is a bank launching an app for students. No other information was given.",
              "List the questions you would ask the client before starting, and explain in a few sentences how the answers would change the design.",
              [{"criterion": "Asks relevant, specific questions (audience, brand, constraints, goals)", "weight": 4},
               {"criterion": "Links answers to design decisions", "weight": 3},
               {"criterion": "Clear, organised writing", "weight": 3}], min_words=60, max_words=220),
        _task("Give design feedback",
              "A junior designer shows a landing page with light grey text on a white background, six different fonts and a tiny call-to-action button at the bottom.",
              "Write the feedback you would give them: what is wrong and how to fix it.",
              [{"criterion": "Identifies the real issues (contrast, typography, hierarchy, call to action)", "weight": 4},
               {"criterion": "Gives actionable, kind and constructive suggestions", "weight": 3},
               {"criterion": "Clear, concise writing", "weight": 3}], min_words=50, max_words=200),
    ],
    "admin": [
        _task("Calendar conflict",
              "You support a director. On Thursday she has: a client call 10:00 to 11:00 (confirmed with an external client), a team review 10:30 to 11:30 (she chairs it), and a dentist appointment at 12:00 "
              "she cannot move. The board pack that she must approve is due at 5 PM and she has not seen it. Your own tasks that day include booking a meeting room for Friday.",
              "Write a short message to the director explaining the conflict and proposing how to solve it, including how you would protect time for the board pack.",
              [{"criterion": "Spots the real conflict and states it clearly", "weight": 3},
               {"criterion": "Proposes a sensible, specific fix and protects the board pack", "weight": 3},
               {"criterion": "Concise, polite and easy to act on", "weight": 2},
               {"criterion": "Professional tone and correct grammar", "weight": 2}], min_words=50, max_words=180),
        _task("Draft a reply on someone's behalf",
              "A vendor emails your manager: \"Our invoice INV-2207 for Rs 48,000 is 15 days overdue. Please confirm payment today.\" Your manager tells you: payment was delayed because the purchase order number was missing "
              "on the invoice; finance will pay within 5 working days once the PO number is added; apologise for the delay.",
              "Write the reply that will be sent to the vendor.",
              [{"criterion": "Includes every point the manager asked for (reason, 5 working days, PO number, apology)", "weight": 4},
               {"criterion": "Polite, professional and not defensive", "weight": 3},
               {"criterion": "Clear and concise", "weight": 3}], min_words=40, max_words=150),
    ],
    "legal": [
        _task("Summarise a clause",
              "Clause 7: \"Either party may terminate this Agreement on 30 days' written notice. The Client shall pay all fees accrued up to the termination date. Fees for work not yet started are not payable. "
              "The Supplier shall return all Client data within 14 days of termination and may not retain copies.\"",
              "Summarise this clause in plain English for a business manager who is not a lawyer, and point out one thing they should watch for.",
              [{"criterion": "Accurate summary of the notice period, fees and data return", "weight": 4},
               {"criterion": "Plain, clear language without distorting the meaning", "weight": 3},
               {"criterion": "Identifies a sensible point to watch (for example the 30-day notice or the 14-day data deadline)", "weight": 3}], min_words=50, max_words=180),
        _task("Flag a risk",
              "A sales manager asks you to send a customer a contract with this sentence added by hand: \"We guarantee 100% uptime and will compensate any loss.\" Your company's standard terms promise 99.5% uptime "
              "and cap liability at the fees paid.",
              "Write a short note to the sales manager explaining the issue and what you recommend.",
              [{"criterion": "Identifies the conflict with the standard terms (uptime and liability)", "weight": 4},
               {"criterion": "Recommends a practical way forward (approval route, alternative wording)", "weight": 3},
               {"criterion": "Clear, professional and respectful tone", "weight": 3}], min_words=50, max_words=170),
    ],
    "product": [
        _task("Write user stories",
              "Messy brief from a manager: \"Customers keep asking where their orders are. Can we let them see the status somewhere? Maybe also send them something when it ships. Mobile mostly. Not urgent but the support team is overloaded.\"",
              "Write three user stories (As a ..., I want ..., so that ...) with one acceptance criterion each, and list one question you would ask the manager.",
              [{"criterion": "Well-formed stories that capture the real needs in the brief", "weight": 3},
               {"criterion": "Testable acceptance criteria", "weight": 3},
               {"criterion": "A relevant clarifying question", "weight": 2},
               {"criterion": "Clear, concise writing", "weight": 2}], min_words=70, max_words=260),
        _task("Prioritise the backlog",
              "You have four items and room for two this sprint: (A) fix a checkout bug that fails for 3% of payments, (B) add dark mode, requested by 40 users, (C) export orders to Excel, requested by 2 large customers worth 15% of revenue, "
              "(D) redesign the settings page.",
              "Choose two items and explain your reasoning, including what you would say to the owners of the items you leave out.",
              [{"criterion": "Sensible choice grounded in impact and risk", "weight": 4},
               {"criterion": "Clear reasoning and trade-offs", "weight": 3},
               {"criterion": "Handles the stakeholder message well", "weight": 3}], min_words=60, max_words=220),
    ],
    "training": [
        _task("Training outline",
              "New customer-support hires need to learn the refund policy, the ticketing tool and how to handle angry customers. You have one half-day (4 hours) and 12 hires.",
              "Write a short outline for the half-day: sessions, timing and one way you would check that people learned something.",
              [{"criterion": "Realistic structure that fits 4 hours and covers all three topics", "weight": 4},
               {"criterion": "Uses practice, not only talking, and includes a check of learning", "weight": 3},
               {"criterion": "Clear and well organised", "weight": 3}], min_words=60, max_words=220),
        _task("Feedback after a session",
              "A trainer received this feedback: \"Too much slides, too fast, and the examples were from a different industry.\" The same trainer will run the session again next month.",
              "Write the message you would send to the trainer: what you noticed and what you suggest.",
              [{"criterion": "Constructive, specific and kind", "weight": 3},
               {"criterion": "Practical suggestions that answer each point of the feedback", "weight": 4},
               {"criterion": "Professional tone and clear writing", "weight": 3}], min_words=50, max_words=170),
    ],
    "management": [
        _task("Project plan and risks",
              "You must deliver a customer portal in 8 weeks with 3 developers, 1 designer and a part-time tester. The customer may change requirements and the payment provider's test account takes up to 2 weeks to get.",
              "Write a short plan: the main phases with timing, the two biggest risks, and what you would do about each.",
              [{"criterion": "A realistic plan that fits the time and team", "weight": 3},
               {"criterion": "Identifies the real risks given in the scenario (changing requirements, the provider's delay)", "weight": 4},
               {"criterion": "Concrete mitigations and clear writing", "weight": 3}], min_words=70, max_words=250),
        _task("Status update to a worried stakeholder",
              "Your project is 1 week behind because a supplier delivered late. The sponsor, who dislikes surprises, asks: \"Are we still on track for the launch on the 30th?\" You can recover 3 days by working in parallel; the rest needs the sponsor's decision.",
              "Write your reply.",
              [{"criterion": "Honest and clear about the delay and the recovery", "weight": 4},
               {"criterion": "Asks for the specific decision needed with options", "weight": 3},
               {"criterion": "Calm, professional tone; concise", "weight": 3}], min_words=50, max_words=170),
    ],
    "implementation": [
        _task("Onboarding plan",
              "A new customer, a 40-person logistics company, buys your scheduling software. They want to go live in 3 weeks, have data in spreadsheets, and two people who will be the main users.",
              "Write a short onboarding plan for the customer: steps, timing and what you need from them.",
              [{"criterion": "A realistic step-by-step plan that fits 3 weeks", "weight": 4},
               {"criterion": "States clearly what is needed from the customer (data, people, decisions)", "weight": 3},
               {"criterion": "Clear, friendly and professional writing", "weight": 3}], min_words=60, max_words=220),
        _task("Setup instructions",
              "A customer admin must connect their email to the software: open Settings, choose Integrations, click Email, sign in with their work account, and press Allow. Many customers get stuck at the last step because their IT blocks it.",
              "Write short instructions for the customer, including what to do if they get stuck.",
              [{"criterion": "Accurate, complete steps in the right order", "weight": 4},
               {"criterion": "Covers the likely problem and how to get help", "weight": 3},
               {"criterion": "Clear, simple language", "weight": 3}], min_words=40, max_words=170),
    ],
    "qa": [
        _task("Write test cases",
              "A login page has an email field, a password field, a 'Remember me' checkbox and a 'Sign in' button. Rules: the email must be valid; the password must be at least 8 characters; after 5 wrong "
              "attempts the account is locked for 15 minutes; 'Remember me' keeps the user signed in for 30 days.",
              "Write at least eight test cases for this page. For each give a short title, the steps or input, and the expected result. Include negative and boundary cases.",
              [{"criterion": "Covers the stated rules (email, password length, lockout, remember me)", "weight": 3},
               {"criterion": "Includes boundary and negative cases (7 vs 8 characters, 5th vs 6th attempt, invalid emails)", "weight": 3},
               {"criterion": "Each case has clear steps and a specific expected result", "weight": 2},
               {"criterion": "Well organised, no duplicates, easy for another tester to run", "weight": 2}], min_words=100, max_words=450),
        _task("Write a bug report",
              "While testing an online store you notice: add 2 units of a product to the cart, open the cart in a second browser tab, remove one unit there, then go back to the first tab and press "
              "'Checkout'. The checkout page still shows 2 units and charges for 2, although the cart now holds 1. This happens on Chrome 126 on Windows 11, but not on Firefox.",
              "Write the bug report you would file for the developers.",
              [{"criterion": "A precise title and a summary a developer understands at once", "weight": 2},
               {"criterion": "Numbered, reproducible steps with the environment (browser, version, system)", "weight": 3},
               {"criterion": "States expected versus actual behaviour and the impact", "weight": 3},
               {"criterion": "Sensible severity and useful extra information (frequency, what was tried)", "weight": 2}], min_words=70, max_words=300),
    ],
    "qa_lead": [
        _task("Test strategy for a release",
              "Your team releases a new payment feature in two weeks. There are 3 testers, a weak automated test suite (about 20% coverage), a mobile app and a website, and the feature touches money, so "
              "mistakes are costly. Developers will finish coding in 8 working days.",
              "Write a short test strategy: what you will test first and why, what you will automate, how you will use the 3 testers, and your release criteria.",
              [{"criterion": "Prioritises by risk (money, core flows) and says why", "weight": 3},
               {"criterion": "A realistic plan for the time and people available, including automation choices", "weight": 3},
               {"criterion": "Clear, measurable release criteria", "weight": 2},
               {"criterion": "Clear, organised writing", "weight": 2}], min_words=100, max_words=400),
        _task("Handle a quality disagreement",
              "Two days before release, a tester finds a serious bug that happens for about 2% of users. The product manager wants to ship on time and fix it next week. The developers say the fix needs 3 days.",
              "Write what you would say to the product manager and what you would recommend.",
              [{"criterion": "Presents the risk with facts (scale, impact) not opinion", "weight": 3},
               {"criterion": "Gives options with trade-offs and a clear recommendation", "weight": 3},
               {"criterion": "Professional, calm and persuasive tone", "weight": 2},
               {"criterion": "Concise and clear", "weight": 2}], min_words=70, max_words=280),
    ],
    "architecture": [
        _task("Design a system",
              "Design the backend for a food-delivery app that must handle 50,000 orders per hour at lunch time, show the delivery partner's live location to the customer, and stay available if one server fails.",
              "Describe your design: the main components, how data is stored, how live location works, and how you handle failure and growth. State any assumptions.",
              [{"criterion": "A sensible set of components and how they talk to each other", "weight": 3},
               {"criterion": "Fitting choices for storage and for real-time location, with reasons", "weight": 3},
               {"criterion": "Addresses availability, scaling and failure explicitly", "weight": 3},
               {"criterion": "States assumptions and trade-offs clearly", "weight": 1}], min_words=120, max_words=500),
        _task("Review a risky decision",
              "A team wants to rewrite their working 6-year-old application from scratch in a new framework over 9 months, because 'the code is messy'. The business wants new features during that time.",
              "Write your recommendation to the engineering director, with the risks and an alternative.",
              [{"criterion": "Identifies the real risks of a full rewrite (time, features, regressions)", "weight": 3},
               {"criterion": "Proposes a credible alternative (incremental refactor, strangler approach) with steps", "weight": 3},
               {"criterion": "Balances technical and business needs", "weight": 2},
               {"criterion": "Clear, persuasive, well organised", "weight": 2}], min_words=100, max_words=380),
    ],
    "engmanagement": [
        _task("Handle an underperforming engineer",
              "A mid-level engineer on your team of 8 has missed three deadlines this quarter, and teammates are starting to complain. In the past the engineer was reliable. You do not know the reason yet.",
              "Describe how you would handle this over the next month: what you would do first, how you would talk to the engineer, and how you would protect the team's delivery.",
              [{"criterion": "Starts by understanding the cause with a private, respectful conversation", "weight": 3},
               {"criterion": "A fair, structured plan with clear expectations and support", "weight": 3},
               {"criterion": "Protects delivery and the team while the issue is handled", "weight": 2},
               {"criterion": "Clear, thoughtful and professional", "weight": 2}], min_words=100, max_words=380),
        _task("Plan a quarter",
              "Your team has capacity for about 60 work units next quarter. Requests: a customer-facing feature worth a big contract (40 units), paying down technical debt that causes weekly outages (25 units), "
              "and a promised integration for a partner (20 units).",
              "Explain what you would do and how you would communicate it to the stakeholders who lose out.",
              [{"criterion": "A reasoned choice that respects the 60-unit limit and weighs impact", "weight": 3},
               {"criterion": "Explains trade-offs and how to reduce the impact on what is cut or delayed", "weight": 3},
               {"criterion": "Plans clear stakeholder communication", "weight": 2},
               {"criterion": "Concise and clear", "weight": 2}], min_words=90, max_words=340),
    ],
    "ux_research": [
        _task("Plan a user study",
              "A banking app's users often abandon the 'add a new payee' flow halfway. The team does not know why. You have two weeks and access to 10 users.",
              "Write a short research plan: your research questions, the method(s) you would use and why, how you would recruit, and what you would deliver at the end.",
              [{"criterion": "Clear research questions tied to the problem", "weight": 3},
               {"criterion": "Suitable methods for the time and users, with reasons", "weight": 3},
               {"criterion": "Realistic plan for recruiting and for outputs the team can act on", "weight": 2},
               {"criterion": "Clear, organised writing", "weight": 2}], min_words=90, max_words=350),
        _task("Turn findings into recommendations",
              "Findings from 8 interviews: 6 users did not notice the 'Save' button below the fold; 5 said they feared sending money to a wrong payee; 3 wanted to paste account details from messages.",
              "Write a short summary for the product team: key insights and three prioritised recommendations.",
              [{"criterion": "Accurate insights drawn from the evidence given", "weight": 3},
               {"criterion": "Specific, prioritised and actionable recommendations", "weight": 3},
               {"criterion": "Links each recommendation to the finding that supports it", "weight": 2},
               {"criterion": "Clear and concise", "weight": 2}], min_words=70, max_words=260),
    ],
    "presales": [
        _task("Answer a customer's requirement",
              "A prospect's technical manager writes: 'We have 3,000 employees in 4 countries. We need single sign-on with our existing directory, data stored in India only, and 99.9% uptime. Can your product do this, and how long would setup take?' "
              "Facts: your product supports SSO with common directories, offers an India data region, guarantees 99.9% uptime, and typical setup for this size takes 3 to 4 weeks.",
              "Write the reply you would send to the technical manager.",
              [{"criterion": "Answers each of the three requirements and the timeline accurately", "weight": 4},
               {"criterion": "Clear, confident and honest, with no invented claims", "weight": 3},
               {"criterion": "Proposes a sensible next step (a technical session or a trial)", "weight": 2},
               {"criterion": "Professional tone and clear writing", "weight": 1}], min_words=80, max_words=260),
        _task("Plan a demo",
              "You have a 30-minute demo for a retail company's operations head who is worried about stock-outs and manual reports. Your product has inventory alerts, automated reports, a mobile app and an analytics dashboard.",
              "Outline how you would run the demo: the order of what you show, why, and how you would handle questions.",
              [{"criterion": "Focuses the demo on the customer's stated problems (stock-outs, reports)", "weight": 4},
               {"criterion": "A clear structure that fits 30 minutes, with a strong opening and close", "weight": 3},
               {"criterion": "A sensible approach to questions and a next step", "weight": 2},
               {"criterion": "Clear, concise writing", "weight": 1}], min_words=70, max_words=260),
    ],
    "recruiter": [
        _task("Screen against a job description",
              "Role: Backend Developer, 3 or more years, Python and PostgreSQL, has built REST APIs, can join within 30 days. Candidate A: 5 years Java and MySQL, learning Python, 60-day notice. "
              "Candidate B: 3 years Python and PostgreSQL, built REST APIs, 30-day notice, salary slightly above budget. Candidate C: 2 years Python and MongoDB, strong projects, immediate joiner.",
              "Rank the three candidates, explain your reasoning, and say what you would check with each on a first call.",
              [{"criterion": "A clear ranking tied to the stated requirements", "weight": 3},
               {"criterion": "Notices the trade-offs (skills, experience, notice period, salary)", "weight": 3},
               {"criterion": "Relevant questions to ask each candidate", "weight": 2},
               {"criterion": "Fair, evidence-based and clearly written", "weight": 2}], min_words=90, max_words=320),
        _task("Write outreach to a passive candidate",
              "You want to approach a senior engineer you found on a professional network. She has 7 years of experience in payments, currently works at a large bank, and has not applied anywhere. "
              "Your role: Senior Engineer at a growing payments startup, remote-friendly, strong engineering culture.",
              "Write the first message you would send her.",
              [{"criterion": "Personalised to her background, not a template", "weight": 3},
               {"criterion": "Gives a clear, honest reason to be interested", "weight": 3},
               {"criterion": "Short, respectful with a low-pressure next step", "weight": 2},
               {"criterion": "Professional tone and correct grammar", "weight": 2}], min_words=50, max_words=170),
    ],
    "generic": [
        _task("Write a clear update",
              "Your team was meant to deliver a customer report today, but a data source was delayed by half a day. You will deliver it tomorrow at 11 AM. The customer is expecting it this evening.",
              "Write the message you would send to the customer.",
              [{"criterion": "Clear about what happened and the new time, without excuses", "weight": 3},
               {"criterion": "Takes responsibility and reassures with a concrete commitment", "weight": 3},
               {"criterion": "Concise and professional", "weight": 2},
               {"criterion": "Correct grammar", "weight": 2}], min_words=40, max_words=150),
        _task("Solve a team problem",
              "Two teammates keep disagreeing in meetings and the work is slowing down. Both are good performers. You are the team lead.",
              "Describe how you would handle this, step by step.",
              [{"criterion": "A sensible, fair approach (talk to each, find the cause, agree rules)", "weight": 4},
               {"criterion": "Considers the team's goals and relationships", "weight": 3},
               {"criterion": "Clear, organised writing", "weight": 3}], min_words=60, max_words=220),
    ],
}


def _with_ids(tasks: list[dict]) -> list[dict]:
    return [{**t, "id": index + 1, "type": "written", "question": t["title"]} for index, t in enumerate(tasks)]


def fallback_written_tasks(family: str | None) -> list[dict]:
    return _with_ids([dict(t) for t in _BANK.get(family or "", _BANK["generic"])][:WRITTEN_TASKS])


# ------------------------------------------------------------------ AI-written tasks

_GENERATE_SYSTEM = """You write practical hiring tasks. For ONE job you write {count} short WRITTEN work-sample tasks that a candidate does in about \
{minutes} minutes, so that the answers show how well the candidate would do the real work.

Rules:
- Each task is a realistic situation from THIS job, with every fact the candidate needs INSIDE the scenario (names, figures, constraints). \
Nothing may require outside knowledge of a company or product.
- The candidate must produce a piece of writing (an email, a message, a short brief, a plan, a summary, a decision with reasons).
- Give a rubric of 3-5 criteria with weights 1-4 (higher = more important) that a fair grader can apply to ANY candidate's answer.
- No trick questions, no personal data, no real company names. Plain English.

Return ONLY this JSON object:
{{"tasks": [{{"title": "short name", "scenario": "the situation with all facts", "instructions": "exactly what to write", \
"min_words": 40, "max_words": 200, "rubric": [{{"criterion": "what good looks like", "weight": 3}}]}}]}}"""


def _provider(timeout):
    from backend.app.services.aptitude.ai_question_generator import _make_provider

    provider = _make_provider()
    provider.timeout = timeout
    return provider


def _normalise_task(raw) -> dict | None:

    if not isinstance(raw, dict):
        return None

    rubric = []
    for item in raw.get("rubric") or []:
        if isinstance(item, dict) and _clean(item.get("criterion"), 160):
            rubric.append({"criterion": _clean(item["criterion"], 160), "weight": max(1, min(4, int(item.get("weight") or 2)))})

    title, scenario, instructions = _clean(raw.get("title"), 80), _clean(raw.get("scenario"), 1400), _clean(raw.get("instructions"), 400)

    if not (title and len(scenario) >= 60 and instructions and len(rubric) >= 3):
        return None

    minimum = max(MIN_WORDS_FLOOR, min(300, int(raw.get("min_words") or 40)))
    maximum = max(minimum + 20, min(MAX_WORDS_CEILING, int(raw.get("max_words") or 200)))

    return {"title": title, "scenario": scenario, "instructions": instructions, "rubric": rubric[:5], "min_words": minimum, "max_words": maximum, "category": "Practical task"}


def generate_written_tasks(job, focus: list[str] | None = None, family: str | None = None) -> tuple[list[dict], str]:
    """(tasks, source): the AI's tasks for this job, else the curated bank for the role family."""

    try:
        prompt = (
            f"JOB TITLE: {getattr(job, 'title', '') or 'Not specified'}\n"
            f"REQUIREMENTS / DESCRIPTION: {(getattr(job, 'requirements', '') or getattr(job, 'description', '') or 'Not specified')[:900]}\n"
            f"RESPONSIBILITIES: {(getattr(job, 'responsibilities', '') or 'Not specified')[:500]}\n"
            f"FOCUS (what the tasks should exercise): {', '.join(focus or []) or 'the most important written work of this job'}\n\n"
            "Write the tasks now."
        )

        provider = _provider(GENERATE_TIMEOUT_SECONDS)
        raw = json.loads(provider.generate_json(prompt, system=_GENERATE_SYSTEM.format(count=WRITTEN_TASKS, minutes=12), max_tokens=2200, temperature=0.6))
        tasks = [t for t in (_normalise_task(item) for item in (raw.get("tasks") or [])) if t][:WRITTEN_TASKS]

        if len(tasks) == WRITTEN_TASKS:
            return _with_ids(tasks), "ai"

        logger.warning("AI written tasks unusable (%d valid): using the curated bank.", len(tasks))
    except Exception as exc:  # noqa: BLE001 - any failure falls back to the curated bank
        logger.warning("AI written tasks could not be generated (%s): using the curated bank.", exc)

    return fallback_written_tasks(family), "fallback"


# ------------------------------------------------------------------ grading

PASS_PERCENT = 60

_GRADE_SYSTEM = """You are a fair, consistent hiring grader. For each task you get the scenario, the instructions, a rubric and the candidate's written answer. \
Score EVERY rubric criterion from 0 to 10 for this answer using ONLY what the candidate wrote (never reward length for its own sake, never invent content the \
candidate did not write). A blank, off-topic or copied-from-the-scenario answer scores 0-2.
Give one short, specific comment per criterion and a one-sentence summary.

Return ONLY this JSON: {"tasks": [{"id": 1, "criteria": [{"criterion": "...", "score": 7, "comment": "..."}], "summary": "..."}]}"""


def words(text) -> int:
    return len(re.findall(r"\b[\w'-]+\b", str(text or "")))


def _rules_grade(task: dict, answer: str) -> dict:
    """No AI available: a plain length / structure check, honestly labelled. It cannot judge quality, so it never marks a real attempt as poor."""

    count = words(answer)
    minimum, maximum = task.get("min_words", 40), task.get("max_words", 200)

    if count == 0:
        pct = 0
    elif count < minimum:
        pct = round(40 * count / max(1, minimum))
    elif count > maximum * 1.5:
        pct = 60
    else:
        pct = 70

    return {
        "id": task["id"], "percent": pct, "source": "rules", "needs_review": True,
        "summary": "Automatic grading was unavailable: scored on length and completeness only. Please read the answer.",
        "criteria": [{"criterion": c["criterion"], "score": None, "max": 10, "comment": ""} for c in task.get("rubric", [])],
    }


def grade_written(tasks: list[dict], answers_by_id: dict) -> dict:
    """{task id: {"percent", "source", "summary", "criteria", "needs_review"}} for every task."""

    results = {}
    to_grade = []

    for task in tasks:
        answer = str(answers_by_id.get(task["id"]) or "").strip()
        if words(answer) < 3:
            results[task["id"]] = {
                "id": task["id"], "percent": 0, "source": "rules", "needs_review": False,
                "summary": "No answer was written.", "criteria": [{"criterion": c["criterion"], "score": 0, "max": 10, "comment": ""} for c in task.get("rubric", [])],
            }
        else:
            to_grade.append((task, answer))

    if not to_grade:
        return results

    try:
        payload = [
            {"id": t["id"], "scenario": t["scenario"], "instructions": t["instructions"], "rubric": t["rubric"], "candidate_answer": a[:4000]}
            for t, a in to_grade
        ]
        provider = _provider(GRADE_TIMEOUT_SECONDS)
        raw = None

        for attempt in (1, 2):
            try:
                raw = json.loads(provider.generate_json(json.dumps({"tasks": payload}), system=_GRADE_SYSTEM, max_tokens=2000, temperature=0.1))
                break
            except Exception as exc:  # noqa: BLE001 - an occasional invalid-JSON reply: one more try
                if attempt == 2:
                    raise
                logger.warning("AI grading of written tasks failed once (%s): trying again.", exc)

        graded = {int(item["id"]): item for item in raw.get("tasks", []) if isinstance(item, dict) and "id" in item}

        for task, _answer in to_grade:
            item = graded.get(task["id"])
            if not item:
                results[task["id"]] = _rules_grade(task, _answer)
                continue

            ai_criteria = [c for c in item.get("criteria", []) if isinstance(c, dict)]
            by_name = {_clean(c.get("criterion"), 160): c for c in ai_criteria}
            criteria, earned, possible = [], 0.0, 0.0

            for position, crit in enumerate(task["rubric"]):
                found = by_name.get(crit["criterion"]) or (ai_criteria[position] if position < len(ai_criteria) else {})
                score = max(0, min(10, int(float(found.get("score", 0) or 0))))
                criteria.append({"criterion": crit["criterion"], "score": score, "max": 10, "comment": _clean(found.get("comment"), 240)})
                earned += score * crit["weight"]
                possible += 10 * crit["weight"]

            results[task["id"]] = {
                "id": task["id"], "percent": round(100 * earned / possible) if possible else 0, "source": "ai", "needs_review": False,
                "summary": _clean(item.get("summary"), 300), "criteria": criteria,
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI grading of written tasks failed (%s): using the length check.", exc)
        for task, answer in to_grade:
            results.setdefault(task["id"], _rules_grade(task, answer))

    return results
