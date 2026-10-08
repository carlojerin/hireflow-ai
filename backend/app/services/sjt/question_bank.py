import copy


# ============================================================
# JOBPILOT AI - SITUATIONAL JUDGMENT TEST QUESTION BANK
#
# A fixed, deterministic set of workplace-scenario questions —
# the non-technical stage's equivalent of aptitude_question_bank's
# APTITUDE_QUESTION_BANK, same shape (id, category, question,
# options, correct_answer), same fallback role: used whenever
# AI generation (sjt_ai_generator.py) is unavailable or invalid.
#
# Categories mirror what real SJT providers (SHL, TestGorilla)
# actually test: leadership, communication, time management,
# conflict resolution, customer service, ethics/integrity,
# teamwork, adaptability — deliberately role-agnostic so the same
# bank serves Digital Marketing, Sales, Finance, HR, Support,
# Operations, Admin, etc. "Correct" answers reflect the generally
# best-practice professional response, not a trick answer.
# ============================================================

SJT_QUESTION_BANK = [

    {
        "id": 1,
        "category": "communication",
        "question": (
            "A client emails you clearly frustrated about a delay, using a sharp tone. "
            "What is the best first step?"
        ),
        "options": [
            "Reply immediately in the same tone to set boundaries",
            "Acknowledge their frustration, explain the cause factually, and give a clear next step with a timeline",
            "Forward the email to your manager without responding",
            "Wait a day before replying so emotions cool down",
        ],
        "correct_answer": "Acknowledge their frustration, explain the cause factually, and give a clear next step with a timeline",
    },

    {
        "id": 2,
        "category": "time_management",
        "question": (
            "You have two deadlines due today: one from your direct manager and one from a senior "
            "leader in another department, and you cannot finish both on time. What should you do?"
        ),
        "options": [
            "Prioritize the senior leader's request since they are more senior",
            "Quietly do your best on both and say nothing unless asked",
            "Proactively tell both parties as soon as you realize the conflict, and ask your manager to help prioritize",
            "Finish whichever one is easier first",
        ],
        "correct_answer": "Proactively tell both parties as soon as you realize the conflict, and ask your manager to help prioritize",
    },

    {
        "id": 3,
        "category": "conflict_resolution",
        "question": (
            "A coworker takes credit for an idea you contributed during a team meeting, in front of your manager. "
            "What's the best response?"
        ),
        "options": [
            "Say nothing in the meeting, but complain to other coworkers afterward",
            "Interrupt the coworker immediately to correct them in front of everyone",
            "Calmly clarify your contribution in the moment if natural, or follow up with the coworker privately afterward",
            "Email your manager immediately accusing the coworker of stealing credit",
        ],
        "correct_answer": "Calmly clarify your contribution in the moment if natural, or follow up with the coworker privately afterward",
    },

    {
        "id": 4,
        "category": "ethics_integrity",
        "question": (
            "You notice a small error in a report that has already been sent to a client, and the error makes "
            "your team's numbers look slightly better than they actually are. No one else has noticed. What do you do?"
        ),
        "options": [
            "Say nothing since the client hasn't noticed and it's a minor difference",
            "Flag the error to your manager and propose sending a correction to the client",
            "Quietly fix it in your own records only, without telling anyone",
            "Wait to see if the client brings it up before doing anything",
        ],
        "correct_answer": "Flag the error to your manager and propose sending a correction to the client",
    },

    {
        "id": 5,
        "category": "customer_service",
        "question": (
            "A customer asks for a refund outside your company's official policy, and seems like a genuine, "
            "long-standing customer having a one-off bad experience. What is the best approach?"
        ),
        "options": [
            "Refuse immediately and quote the policy word for word",
            "Approve it yourself without telling anyone to keep the customer happy",
            "Explain the policy honestly, acknowledge their situation, and check with a supervisor if an exception is possible",
            "Ignore the request and hope the customer stops asking",
        ],
        "correct_answer": "Explain the policy honestly, acknowledge their situation, and check with a supervisor if an exception is possible",
    },

    {
        "id": 6,
        "category": "teamwork",
        "question": (
            "A teammate on your project consistently misses their agreed deadlines, which is now affecting "
            "your own ability to deliver on time. What should you do first?"
        ),
        "options": [
            "Immediately escalate to your manager without talking to the teammate",
            "Quietly take over their work yourself to avoid confrontation",
            "Talk to the teammate directly to understand what's blocking them and agree on a plan",
            "Miss your own deadline too, since it isn't your fault",
        ],
        "correct_answer": "Talk to the teammate directly to understand what's blocking them and agree on a plan",
    },

    {
        "id": 7,
        "category": "adaptability",
        "question": (
            "Midway through a project, your manager tells you the whole approach needs to change based on new "
            "information, meaning some of your completed work won't be used. How should you react?"
        ),
        "options": [
            "Push back and insist on finishing the original plan since work is already done",
            "Accept the change, ask clarifying questions about the new direction, and adjust your plan",
            "Agree verbally but quietly keep working on the old plan anyway",
            "Express frustration to other teammates about the wasted effort",
        ],
        "correct_answer": "Accept the change, ask clarifying questions about the new direction, and adjust your plan",
    },

    {
        "id": 8,
        "category": "leadership",
        "question": (
            "You're asked to lead a small project for the first time. One team member has much more experience "
            "than you and seems skeptical of your leadership. What's the best way to earn their trust?"
        ),
        "options": [
            "Assert authority firmly so they know who is in charge",
            "Avoid giving them any direction so they don't feel managed",
            "Involve them early, genuinely ask for their input, and be transparent about decisions",
            "Go around them and assign their tasks to someone else",
        ],
        "correct_answer": "Involve them early, genuinely ask for their input, and be transparent about decisions",
    },

    {
        "id": 9,
        "category": "communication",
        "question": (
            "You need to explain a technical or complex topic (e.g. a budget constraint, a process change) to "
            "a stakeholder who doesn't have background in that area. What's the best approach?"
        ),
        "options": [
            "Use the same technical language you'd use with your own team, to be precise",
            "Simplify the explanation, use relatable analogies, and check they've understood before moving on",
            "Send a long detailed document and let them read it on their own",
            "Avoid the topic until someone else with more expertise can explain it",
        ],
        "correct_answer": "Simplify the explanation, use relatable analogies, and check they've understood before moving on",
    },

    {
        "id": 10,
        "category": "time_management",
        "question": (
            "You're assigned a new, high-priority task in the middle of the day while you're already behind on "
            "existing work. What should you do?"
        ),
        "options": [
            "Drop everything immediately without saying anything",
            "Ignore the new task until existing work is done",
            "Quickly assess the real urgency, communicate your current workload, and agree on a realistic plan with whoever assigned it",
            "Try to do all of it at once without re-prioritizing anything",
        ],
        "correct_answer": "Quickly assess the real urgency, communicate your current workload, and agree on a realistic plan with whoever assigned it",
    },

    {
        "id": 11,
        "category": "conflict_resolution",
        "question": (
            "Two of your teammates are in an ongoing disagreement that is starting to affect team morale, and "
            "neither has asked for your help. What is the most appropriate action?"
        ),
        "options": [
            "Stay out of it completely since it's not your business",
            "Take one person's side openly to show support",
            "Privately check in with each person, and if appropriate, suggest they talk directly or involve a manager",
            "Raise it publicly in the next team meeting to force a resolution",
        ],
        "correct_answer": "Privately check in with each person, and if appropriate, suggest they talk directly or involve a manager",
    },

    {
        "id": 12,
        "category": "ethics_integrity",
        "question": (
            "A manager asks you to present results in a way that is technically true but likely to mislead "
            "senior leadership about how well a project is actually going. What should you do?"
        ),
        "options": [
            "Do exactly as asked since it's your manager's call",
            "Refuse outright and report your manager immediately",
            "Raise your concern directly with your manager first, explaining why the framing could mislead leadership",
            "Present it as asked, but quietly tell a colleague about your concerns",
        ],
        "correct_answer": "Raise your concern directly with your manager first, explaining why the framing could mislead leadership",
    },

    {
        "id": 13,
        "category": "customer_service",
        "question": (
            "A customer is on the phone and clearly confused about how a product works, asking the same question "
            "in different ways without understanding your answers. What should you do?"
        ),
        "options": [
            "Repeat the same explanation again, just slower",
            "Tell them to read the manual themselves",
            "Try a different explanation style (e.g. a simple analogy or step-by-step walkthrough) and confirm understanding as you go",
            "Transfer them to someone else immediately",
        ],
        "correct_answer": "Try a different explanation style (e.g. a simple analogy or step-by-step walkthrough) and confirm understanding as you go",
    },

    {
        "id": 14,
        "category": "teamwork",
        "question": (
            "Your team is divided on which of two approaches to take for an upcoming project, and the "
            "disagreement is slowing the team down. You don't feel strongly either way. What's the best move?"
        ),
        "options": [
            "Stay silent and let others decide since you don't have a strong preference",
            "Help the group define clear decision criteria (cost, time, risk) so the choice is made on merit, not opinion",
            "Pick whichever side has more people to end the debate quickly",
            "Suggest doing both approaches partially to avoid choosing",
        ],
        "correct_answer": "Help the group define clear decision criteria (cost, time, risk) so the choice is made on merit, not opinion",
    },

    {
        "id": 15,
        "category": "adaptability",
        "question": (
            "You've been using a particular tool or process for years, and your company suddenly switches to a "
            "new system that feels less efficient at first. How should you respond?"
        ),
        "options": [
            "Keep using the old system quietly since it works better for you",
            "Complain to coworkers about how much better the old way was",
            "Give the new system a genuine try, note specific issues, and raise constructive feedback through the right channel",
            "Comply publicly but do the minimum to learn it",
        ],
        "correct_answer": "Give the new system a genuine try, note specific issues, and raise constructive feedback through the right channel",
    },

    {
        "id": 16,
        "category": "leadership",
        "question": (
            "A member of your team delivers consistently strong results but is dismissive toward newer, less "
            "experienced teammates in meetings. How should you address this as their lead?"
        ),
        "options": [
            "Ignore it since their output is strong",
            "Publicly call them out in the next team meeting",
            "Privately give specific, direct feedback about the behavior and its impact on the team",
            "Quietly move the newer teammates to a different project",
        ],
        "correct_answer": "Privately give specific, direct feedback about the behavior and its impact on the team",
    },

    {
        "id": 17,
        "category": "communication",
        "question": (
            "You made a mistake that affected a deliverable your manager already shared with their own boss. "
            "What is the best way to handle it?"
        ),
        "options": [
            "Hope no one notices and quietly fix it going forward",
            "Tell your manager promptly, explain what happened, and propose how to fix or mitigate it",
            "Blame the mistake on unclear instructions",
            "Wait until your manager asks before bringing it up",
        ],
        "correct_answer": "Tell your manager promptly, explain what happened, and propose how to fix or mitigate it",
    },

    {
        "id": 18,
        "category": "time_management",
        "question": (
            "You notice partway through a multi-week project that the current pace means you'll likely miss the "
            "final deadline. The deadline is still two weeks away. What should you do?"
        ),
        "options": [
            "Wait until closer to the deadline to see if things improve on their own",
            "Raise the risk early with specifics on why, and propose options (more time, more help, reduced scope)",
            "Quietly work overtime without telling anyone there's a risk",
            "Assume it will work out since there's still time left",
        ],
        "correct_answer": "Raise the risk early with specifics on why, and propose options (more time, more help, reduced scope)",
    },

    {
        "id": 19,
        "category": "customer_service",
        "question": (
            "A long-time client threatens to take their business elsewhere after a service issue, even though "
            "the issue was a one-time mistake on your company's side. What's the best response?"
        ),
        "options": [
            "Let them go since they're being unreasonable",
            "Promise something your company can't actually deliver just to keep them",
            "Sincerely acknowledge the mistake, explain what's being done to fix it, and offer a reasonable, honest resolution",
            "Avoid the conversation and let a manager handle it entirely",
        ],
        "correct_answer": "Sincerely acknowledge the mistake, explain what's being done to fix it, and offer a reasonable, honest resolution",
    },

    {
        "id": 20,
        "category": "ethics_integrity",
        "question": (
            "You discover that a widely-used shortcut on your team technically violates a minor company policy, "
            "though it hasn't caused any real harm so far. What should you do?"
        ),
        "options": [
            "Keep using it since everyone else does and nothing bad has happened",
            "Report it anonymously without raising it with the team first",
            "Raise it with your manager or the relevant policy owner so it can be reviewed and corrected properly",
            "Stop doing it yourself quietly but say nothing to others",
        ],
        "correct_answer": "Raise it with your manager or the relevant policy owner so it can be reviewed and corrected properly",
    },

    {
        "id": 21,
        "category": "teamwork",
        "question": (
            "You're assigned to a cross-functional project with people from departments you don't normally work "
            "with, and early meetings feel disorganized with unclear ownership. What should you do?"
        ),
        "options": [
            "Wait for someone else to take charge of organizing things",
            "Suggest establishing clear roles, a simple shared plan, and regular check-ins",
            "Do your own part in isolation and ignore the disorganization",
            "Complain to your own manager about the other departments",
        ],
        "correct_answer": "Suggest establishing clear roles, a simple shared plan, and regular check-ins",
    },

    {
        "id": 22,
        "category": "adaptability",
        "question": (
            "Your company announces a reorganization that changes your reporting line and some of your "
            "responsibilities with little advance notice. How should you approach the first few weeks after the change?"
        ),
        "options": [
            "Resist the new responsibilities until things go back to normal",
            "Stay disengaged until the situation feels more stable",
            "Proactively clarify expectations with your new manager and look for ways to add value in the new structure",
            "Complain openly to former teammates about the disruption",
        ],
        "correct_answer": "Proactively clarify expectations with your new manager and look for ways to add value in the new structure",
    },

    {
        "id": 23,
        "category": "leadership",
        "question": (
            "A project you're responsible for is going to miss its deadline due to factors outside your control "
            "(a vendor delay). Senior leadership is asking for an update. What's the best way to communicate it?"
        ),
        "options": [
            "Downplay the delay and hope the vendor catches up before anyone notices",
            "Clearly explain the cause, the impact, and a revised realistic timeline with a mitigation plan",
            "Blame the vendor publicly without offering a path forward",
            "Avoid giving a clear update until the full picture is certain",
        ],
        "correct_answer": "Clearly explain the cause, the impact, and a revised realistic timeline with a mitigation plan",
    },

    {
        "id": 24,
        "category": "communication",
        "question": (
            "During a meeting, you strongly disagree with a decision your manager is about to make in front of "
            "other senior people. What is the most professional way to handle it?"
        ),
        "options": [
            "Stay silent in the meeting and complain about it afterward to coworkers",
            "Openly argue with your manager in front of the senior people to make your point",
            "Raise your concern respectfully in the meeting if appropriate, or request a private follow-up conversation",
            "Say nothing at all, even afterward",
        ],
        "correct_answer": "Raise your concern respectfully in the meeting if appropriate, or request a private follow-up conversation",
    },
]


def get_sjt_question_bank() -> list[dict]:
    """Returns a deep copy — callers must never mutate the shared bank."""
    return copy.deepcopy(SJT_QUESTION_BANK)
