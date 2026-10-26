import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path


# ==================================================
# DATABASE
# ==================================================

from backend.app.database.database import (
    Base,
    engine,
    initialize_database
)


# ==================================================
# MODELS
# ==================================================

from backend.app.models.user import User

from backend.app.models.company import Company

from backend.app.models.password_reset import (
    PasswordResetOTP
)

from backend.app.models.resume_analysis import (
    ResumeAnalysis
)

from backend.app.models.user_activity import (
    UserActivity
)

# New recruiter/application models
from backend.app.models.job_application import (
    JobApplication
)

from backend.app.models.application_answer import (
    ApplicationAnswer
)

# Server-side session authentication
from backend.app.models.auth_session import (
    AuthSession
)


# ==================================================
# ROUTES
# ==================================================

from backend.app.api.routes.health import (
    router as health_router
)

from backend.app.api.routes.users import (
    router as users_router
)

from backend.app.api.routes.companies import (
    router as companies_router
)

from backend.app.api.routes.candidate_profiles import (
    router as candidate_profiles_router
)

from backend.app.api.routes.resumes import (
    router as resumes_router
)

from backend.app.api.routes.jobs import (
    router as jobs_router
)

from backend.app.api.routes.company_jobs import (
    router as company_jobs_router
)

# New candidate internal JobPilot jobs route
from backend.app.api.routes.candidate_jobs import (
    router as candidate_jobs_router
)

# New candidate applications route
from backend.app.api.routes.applications import (
    router as applications_router
)

# New company applicants/recruiter applications route
from backend.app.api.routes.company_applications import (
    router as company_applications_router
)

# HR-sourced candidate invite (Phase 1): HR uploads a resume, candidate is
# emailed a magic link that auto-creates their account and application.
from backend.app.api.routes.company_invites import (
    router as company_invites_router
)
from backend.app.api.routes.candidate_invites import (
    router as candidate_invites_router
)

# Admin console: platform-level HR/company management, candidate-invite
# visibility, and a live system-connectors status check.
from backend.app.api.routes.admin import (
    router as admin_router
)

# Candidate aptitude tests
from backend.app.api.routes.aptitude_tests import (
    router as aptitude_tests_router
)

from backend.app.api.routes.password_reset import (
    router as password_reset_router
)

from backend.app.api.routes.activity import (
    router as activity_router
)

# Deterministic assessment blueprint engine
from backend.app.api.routes.assessment import (
    router as assessment_router
)

# Session authentication (/me, /logout)
from backend.app.api.routes.auth import (
    router as auth_router
)

# Assessment session foundation + self-introduction
from backend.app.api.routes.assessment_sessions import (
    router as assessment_sessions_router
)

# Assessment stage video recordings (Task 4)
from backend.app.api.routes.assessment_videos import (
    router as assessment_videos_router
)

# Assessment instructions consent (tick box)
from backend.app.api.routes.assessment_consents import (
    router as assessment_consents_router
)

# Camera proctoring snapshots
from backend.app.api.routes.assessment_snapshots import (
    router as assessment_snapshots_router
)

# AI Interview V2 voice layer (Task 5I) — TTS/STT foundation
# around the existing, unmodified Task 5H interview brain
from backend.app.api.routes.interview_voice_v2 import (
    router as interview_voice_v2_router
)

# AI Interview V2 avatar layer (Task 5K) — fixed AI avatar
# presentation foundation, additive on top of Task 5H/5I/5J
from backend.app.api.routes.interview_avatar_v2 import (
    router as interview_avatar_v2_router
)

# Task 9: realtime conversational interview (selectable provider)
from backend.app.api.routes.interview_realtime_v2 import (
    router as interview_realtime_v2_router
)

# NOTE: the v1 programming assessment stage (programming_tests.py)
# is no longer mounted here -- confirmed unreachable from the live
# frontend (stage_progression_service.py's _PAGES only ever points
# at programming-v3.html, which calls the v2 routes in
# assessment_sessions.py). The file itself is left on disk, unused,
# per this project's own "archive, don't delete" convention.

# AI interview assessment stage
from backend.app.api.routes.interview_tests import (
    router as interview_tests_router
)

# Strict Assessment Mode integrity events (Task 4D-STRICT-1)
from backend.app.api.routes.assessment_integrity import (
    router as assessment_integrity_router
)

# HR assessment review (Task 4D-STRICT-2)
from backend.app.api.routes.hr_assessment_review import (
    router as hr_assessment_review_router
)
from backend.app.api.routes.offer_letters import router as offer_letters_router
from backend.app.api.routes.roleplay_v2 import router as roleplay_router


# ==================================================
# CREATE FASTAPI APPLICATION
# ==================================================

app = FastAPI(

    title="JobPilot AI",

    description=(
        "AI-powered job discovery and "
        "candidate matching platform"
    ),

    version="1.0.0"
)


_validation_logger = logging.getLogger("jobpilot.request_validation")


# FastAPI's default 422 handler returns the validation detail to the CLIENT but never logs it server-side, so the
# terminal only ever shows "422 Unprocessable Entity" with no way to tell which field/value actually failed without
# separately inspecting the browser's network tab. This logs the same detail FastAPI already computed, so a 422 is
# diagnosable from the server terminal alone -- the response sent to the client is completely unchanged.
@app.exception_handler(RequestValidationError)
async def _log_and_reraise_validation_error(request: Request, exc: RequestValidationError):

    _validation_logger.warning("422 on %s %s: %s", request.method, request.url.path, exc.errors())

    return JSONResponse(status_code=422, content={"detail": exc.errors()})


# Task 6: leave no AI-audio publisher participant behind in a LiveKit room
# when the server stops.
@app.on_event("shutdown")
async def _close_interview_audio_publishers():
    from backend.app.services.assessment.livekit_audio_transport_v2 import close_all_publishers
    from backend.app.services.assessment.realtime_interview_v2 import close_all_sessions
    await close_all_sessions()
    await close_all_publishers()


# Assessment-deadline email reminders (24h / 2h before an invite expires) --
# an in-process periodic job since this codebase has no Celery/Redis.
@app.on_event("startup")
async def _apply_admin_settings():
    # model / provider choices the admin saved in the console win over .env (see services/admin_settings.py)
    try:
        from backend.app.database.database import SessionLocal
        from backend.app.services import admin_settings
        db = SessionLocal()
        try:
            admin_settings.apply_all(db)
        finally:
            db.close()
    except Exception as exc:
        print(f"[admin-settings] could not apply saved settings: {exc}")


@app.on_event("startup")
async def _assemble_partial_recordings():
    # chunks left by a candidate who left mid-stage become a "partial" recording (see assessment_videos.assemble_stale_partials)
    try:
        from backend.app.database.database import SessionLocal
        from backend.app.api.routes.assessment_videos import assemble_stale_partials
        db = SessionLocal()
        try:
            assemble_stale_partials(db)
        finally:
            db.close()
    except Exception as exc:
        print(f"[assessment-videos] startup sweep skipped: {exc}")


@app.on_event("startup")
async def _start_reminder_scheduler():
    from backend.app.services.assessment.reminder_scheduler import start_reminder_scheduler
    start_reminder_scheduler()


@app.on_event("shutdown")
async def _stop_reminder_scheduler():
    from backend.app.services.assessment.reminder_scheduler import stop_reminder_scheduler
    stop_reminder_scheduler()


# ==================================================
# CORS
# ==================================================

app.add_middleware(

    CORSMiddleware,

    allow_origins=[

        # Plain HTML frontend
        "http://localhost:5500",

        "http://127.0.0.1:5500",

        # a second frontend port: Windows can reserve 5500 after a restart (WinError 10013), so the frontend can run on 5600 instead
        "http://localhost:5600",

        "http://127.0.0.1:5600",

        # Previous Vite frontend ports
        "http://localhost:5173",

        "http://127.0.0.1:5173",

        "http://localhost:5174",

        "http://127.0.0.1:5174",
    ],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],
)


# ==================================================
# UPLOADS
# ==================================================

BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]

UPLOADS_DIRECTORY = (
    BACKEND_DIRECTORY / "uploads"
)

PROFILE_PHOTOS_DIRECTORY = (
    UPLOADS_DIRECTORY / "profile_photos"
)

COMPANY_LOGOS_DIRECTORY = (
    UPLOADS_DIRECTORY / "company_logos"
)

UPLOADS_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True
)

PROFILE_PHOTOS_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True
)

COMPANY_LOGOS_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True
)

app.mount(
    "/uploads",
    StaticFiles(
        directory=str(UPLOADS_DIRECTORY)
    ),
    name="uploads"
)


# ==================================================
# INITIALIZE DATABASE
# ==================================================

initialize_database()


# ==================================================
# REGISTER API ROUTES
# ==================================================

app.include_router(
    health_router
)

app.include_router(
    activity_router
)

app.include_router(
    users_router
)

app.include_router(
    companies_router
)

app.include_router(
    candidate_profiles_router
)

app.include_router(
    resumes_router
)

# Existing candidate/Jooble job search
app.include_router(
    jobs_router
)

# Internal JobPilot candidate job search
app.include_router(
    candidate_jobs_router
)

# Candidate applications
app.include_router(
    applications_router
)

# Company job management
app.include_router(
    company_jobs_router
)

# Company recruiter/applicants
app.include_router(
    company_applications_router
)

# HR-sourced candidate invite (Phase 1)
app.include_router(
    company_invites_router
)

# Admin console
app.include_router(
    admin_router
)

# records every Gemini request (tokens, rate-limit errors) for the admin console's LLM usage tab
from backend.app.services.llm_usage import install_gemini_usage_hook
install_gemini_usage_hook()

app.include_router(
    candidate_invites_router
)

# Candidate aptitude tests
app.include_router(
    aptitude_tests_router
)

app.include_router(
    password_reset_router
)

# Deterministic assessment blueprint engine
app.include_router(
    assessment_router
)

# Session authentication (/me, /logout)
app.include_router(
    auth_router
)

# Assessment session foundation + self-introduction
app.include_router(
    assessment_sessions_router
)

# Assessment stage video recordings (Task 4)
app.include_router(
    assessment_videos_router
)

app.include_router(
    assessment_snapshots_router
)

app.include_router(
    assessment_consents_router
)

# AI Interview V2 voice layer (Task 5I)
app.include_router(
    interview_voice_v2_router
)

# AI Interview V2 avatar layer (Task 5K)
app.include_router(
    interview_avatar_v2_router
)

app.include_router(
    interview_realtime_v2_router
)

# AI interview assessment stage
app.include_router(
    interview_tests_router
)

# Strict Assessment Mode integrity events (Task 4D-STRICT-1)
app.include_router(
    assessment_integrity_router
)

# HR assessment review (Task 4D-STRICT-2)
app.include_router(
    hr_assessment_review_router
)

# Offer letter email from the candidate report
app.include_router(
    offer_letters_router
)

# Practical role-play call (voice) for the stage-3 practical test
app.include_router(
    roleplay_router
)


# ==================================================
# ROOT ENDPOINT
# ==================================================

@app.get("/")
def root():

    return {

        "message":
            "Welcome to JobPilot AI",

        "status":
            "running"
    }