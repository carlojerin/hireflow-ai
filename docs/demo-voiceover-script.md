# HireFlow AI walkthrough: voice-over script

For the home page "Watch the demo" film (`frontend/demo/walkthrough.html`).
Make **one audio file per chapter** with the names below and put them in `frontend/demo/voice/`. The film will be retimed so each chapter lasts exactly as long as its audio, plus a short breath.

## ElevenLabs settings

- **Voice:** a warm, calm narrator (mid-pitch, unhurried). Indian English suits the audience; neutral English also works. Use the same voice for every file.
- **Model:** Eleven Multilingual v2 (or v3).
- **Settings:** stability about 50%, similarity about 75%, style 0–15%, speaker boost on, speed 1.0.
- **Export:** MP3, 44.1 kHz, 128 kbps or better.
- **Delivery:**
  - Leave about half a second of silence at the start and end of each file.
  - Read "HireFlow AI" as "Hire-Flow A-I".
  - Read "AI" as "A-I".
- **Optional sound effects (ElevenLabs Sound Effects):** `knock.mp3` (two knocks on a glass door), `door.mp3` (soft door opening), `whoosh.mp3` (light swoosh for the invite and offer letter), `tap.mp3` (phone tap), `music.mp3` (90 s calm, modern corporate ambient, no vocals, loopable).

## Script

| File | Chapter | Narration | Target |
|---|---|---|---|
| `00-intro.mp3` | Title | This is HireFlow AI. Let's follow one candidate, from the front door to the offer letter. | ~5 s |
| `01-arrival.mp3` | Arrival | Meet Arjun. He's applying for an AI and machine learning role at TechNova. No forms to fill, no password to remember. He just knocks. | ~10 s |
| `02-hr-desk.mp3` | HR desk | Inside, HR describes the role in plain words and uploads Arjun's resume. HireFlow AI reads it against the job, plans the right tests for this role, and sends him a single link. | ~13 s |
| `03-invite.mp3` | Invite | The invite lands on his phone. One tap, and HireFlow AI builds his profile straight from the resume. He's ready to begin. | ~9 s |
| `04-self-intro.mp3` | Self introduction | First, a short self introduction on camera. Arjun talks about himself and his work, while the camera makes sure his face is clearly visible, and even helps with the lighting. | ~11 s |
| `05-role-test.mp3` | Role test | Next, a role test. Not a generic quiz: the questions are written for this job and this resume. For an AI role, that means real machine learning problems. | ~10 s |
| `06-hands-on.mp3` | Hands-on task | Then, real work. Developers write code that runs against hidden tests. Other roles get a lab, a data task, a written brief or a design task, matched to the job. | ~11 s |
| `07-interview.mp3` | AI interview | Finally, a live spoken interview. Ten questions, built from Arjun's own resume and the job. The interviewer listens, follows up, and keeps things on track. Watch the smoke: it moves with its voice. | ~14 s |
| `08-security.mp3` | Security | And all the while, HireFlow AI keeps every stage fair. The camera notices a phone, a second face, or eyes that keep leaving the screen. The test stays locked in full screen. Copy and paste are blocked, and switching windows is recorded. It even notices another voice in the room, or text that appears too fast to be typed. | ~19 s |
| `09-strikes.mp3` | Strikes and flags | Serious problems count as strikes, and repeated strikes end the test. Smaller signals become flags for HR to review, each with photos and the moment it happened. | ~10 s |
| `10-report.mp3` | HR report | Back at HR, every candidate is in one place, with exactly where they are. Each one gets a single report: scores, an AI summary, integrity signals, and a face check, with the evidence behind them. | ~13 s |
| `11-decision.mp3` | Decision | HireFlow AI never makes the hiring decision. HR does. And when the answer is yes, the offer letter is one click away. | ~8 s |
| `12-outro.mp3` | End card | HireFlow AI. Hire on evidence, not on guesswork. | ~4 s |

Total: about 2 minutes 20 seconds. The copy-button version is at `frontend/demo/voice/script.html`.

### Wording rules followed
- No claims the product cannot back up: no "bias-free", "guaranteed" or "verifies identity". The face check is described as "makes sure his face is clearly visible".
- The AI never decides; HR does.
- No timings or limits shown to candidates.

## What each room shows

| # | Room | What happens | Product feature it explains | On the screens |
|---|---|---|---|---|
| 1 | Building entrance | The candidate walks up and knocks; the door opens | The candidate's journey starts with no sign-up | 3D scene |
| 2 | HR desk | HR uploads the resume; an invite flies to the candidate | Invite form, resume analysis, AI assessment plan | Real screenshots: invite page, HR dashboard |
| 3 | Phone close-up | Tap "Start assessment"; profile builds step by step | Magic link, no password, auto profile from resume | Drawn phone UI |
| 4 | Self introduction room | Candidate on camera with face guide and voice bars | Self intro, face visibility, lighting coach | Drawn camera screen |
| 5 | Role test room | A role-specific question; an answer is chosen | Questions generated per role and resume | Drawn quiz screen |
| 6 | Hands-on room | Code types itself; hidden tests turn green | Docker code sandbox and per-role practical tasks | Drawn code editor |
| 7 | AI interview room | The smoke globe speaks; "You" camera tile on the wall | Live 10-question voice interview, the real globe | Real globe shader; drawn camera tile |
| 8 | Security view | The candidate seen through the proctoring camera: face box, a phone and a second face caught; full-screen lock and blocked paste on the laptop; then strike and flag cards | Face, phone and multi-face detection, full-screen lock, copy/paste block, window-switch, voice and typing checks, strikes vs flags | Drawn camera HUD and laptop screen |
| 9 | HR report room | Three walls of reports; the offer letter flies out | Tracking, one report per candidate, offer letter | Real screenshots (names, emails and photos replaced with samples) |
