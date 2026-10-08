from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


# =========================================================
# DATABASE CONFIGURATION
# =========================================================

DATABASE_URL = "sqlite:///./jobpilot.db"


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        "timeout": 30,
    },
)


# A candidate's test writes constantly (video chunks, autosave, integrity events). With SQLite's defaults a recruiter login at the same moment
# could fail with "database is locked" after 5 s. WAL lets reads and writes overlap, and the busy timeout makes a write wait instead of failing.
@event.listens_for(engine, "connect")
def _tune_sqlite(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


# =========================================================
# DATABASE SESSION
# =========================================================

def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


# =========================================================
# SAFE DATABASE MIGRATION
# =========================================================

def migrate_database():
    """
    Safely add newly introduced columns to existing tables
    without deleting the existing JobPilot database.
    """

    with engine.begin() as connection:

        # =====================================================
        # USERS TABLE
        # =====================================================

        users_columns = {

            "employment_status": (
                "ALTER TABLE users "
                "ADD COLUMN employment_status VARCHAR(100)"
            ),

            "current_role": (
                "ALTER TABLE users "
                "ADD COLUMN current_role VARCHAR(150)"
            ),

            "industry": (
                "ALTER TABLE users "
                "ADD COLUMN industry VARCHAR(150)"
            ),

            "is_active": (
                "ALTER TABLE users "
                "ADD COLUMN is_active BOOLEAN DEFAULT 1"
            ),
        }

        users_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='users'"
            )
        ).fetchone()

        if users_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(users)"
                    )
                ).fetchall()
            }

            for column_name, alter_sql in users_columns.items():

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )

            current_user_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(users)"
                    )
                ).fetchall()
            }

            if "is_active" in current_user_columns:

                connection.execute(
                    text(
                        "UPDATE users "
                        "SET is_active = 1 "
                        "WHERE is_active IS NULL"
                    )
                )

        # =====================================================
        # CANDIDATE PROFILES TABLE
        # =====================================================

        candidate_profile_columns = {

            "preferred_technologies": (
                "ALTER TABLE candidate_profiles "
                "ADD COLUMN preferred_technologies TEXT"
            ),

            "job_type": (
                "ALTER TABLE candidate_profiles "
                "ADD COLUMN job_type VARCHAR(100)"
            ),

            "skills": (
                "ALTER TABLE candidate_profiles "
                "ADD COLUMN skills TEXT"
            ),

            "projects": (
                "ALTER TABLE candidate_profiles "
                "ADD COLUMN projects TEXT"
            ),

            "certifications": (
                "ALTER TABLE candidate_profiles "
                "ADD COLUMN certifications TEXT"
            ),

            "achievements": (
                "ALTER TABLE candidate_profiles "
                "ADD COLUMN achievements TEXT"
            ),
        }

        candidate_profiles_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name='candidate_profiles'"
            )
        ).fetchone()

        if candidate_profiles_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(candidate_profiles)"
                    )
                ).fetchall()
            }

            for column_name, alter_sql in (
                candidate_profile_columns.items()
            ):

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )

        # =====================================================
        # SITUATIONAL JUDGMENT TESTS: practical (work-sample) content
        # =====================================================

        sjt_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='situational_judgment_tests'"
            )
        ).fetchone()

        if sjt_exists:

            sjt_existing = {
                row[1]
                for row in connection.execute(
                    text("PRAGMA table_info(situational_judgment_tests)")
                ).fetchall()
            }

            sjt_columns = {
                "kind": "ALTER TABLE situational_judgment_tests ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'mcq'",
                "payload_json": "ALTER TABLE situational_judgment_tests ADD COLUMN payload_json TEXT",
                "result_json": "ALTER TABLE situational_judgment_tests ADD COLUMN result_json TEXT",
            }

            for column_name, alter_sql in sjt_columns.items():

                if column_name not in sjt_existing:

                    connection.execute(text(alter_sql))

        # =====================================================
        # JOBS TABLE
        # =====================================================

        jobs_columns = {

            "assessment_plan_json": (
                "ALTER TABLE jobs "
                "ADD COLUMN assessment_plan_json TEXT"
            ),

            "company_id": (
                "ALTER TABLE jobs "
                "ADD COLUMN company_id INTEGER"
            ),

            "employment_type": (
                "ALTER TABLE jobs "
                "ADD COLUMN employment_type VARCHAR(50)"
            ),

            "salary_min": (
                "ALTER TABLE jobs "
                "ADD COLUMN salary_min INTEGER"
            ),

            "salary_max": (
                "ALTER TABLE jobs "
                "ADD COLUMN salary_max INTEGER"
            ),

            "salary_currency": (
                "ALTER TABLE jobs "
                "ADD COLUMN salary_currency VARCHAR(10)"
            ),

            "education": (
                "ALTER TABLE jobs "
                "ADD COLUMN education VARCHAR(255)"
            ),

            "requirements": (
                "ALTER TABLE jobs "
                "ADD COLUMN requirements TEXT"
            ),

            "benefits": (
                "ALTER TABLE jobs "
                "ADD COLUMN benefits TEXT"
            ),

            "openings": (
                "ALTER TABLE jobs "
                "ADD COLUMN openings INTEGER"
            ),

            "status": (
                "ALTER TABLE jobs "
                "ADD COLUMN status VARCHAR(30)"
            ),

            "application_deadline": (
                "ALTER TABLE jobs "
                "ADD COLUMN application_deadline VARCHAR(50)"
            ),

            "role_shape_override": (
                "ALTER TABLE jobs "
                "ADD COLUMN role_shape_override VARCHAR(20)"
            ),
        }

        jobs_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='jobs'"
            )
        ).fetchone()

        if jobs_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(jobs)"
                    )
                ).fetchall()
            }

            for column_name, alter_sql in jobs_columns.items():

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )

            # -------------------------------------------------
            # SAFE DEFAULTS FOR EXISTING JOBS
            # -------------------------------------------------

            current_job_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(jobs)"
                    )
                ).fetchall()
            }

            if "status" in current_job_columns:

                connection.execute(
                    text(
                        "UPDATE jobs "
                        "SET status = 'active' "
                        "WHERE status IS NULL "
                        "OR status = ''"
                    )
                )

            if "source" in current_job_columns:

                connection.execute(
                    text(
                        "UPDATE jobs "
                        "SET source = 'internal' "
                        "WHERE source IS NULL "
                        "OR source = ''"
                    )
                )

            if "salary_currency" in current_job_columns:

                connection.execute(
                    text(
                        "UPDATE jobs "
                        "SET salary_currency = 'INR' "
                        "WHERE salary_currency IS NULL "
                        "OR salary_currency = ''"
                    )
                )

        # =====================================================
        # JOB APPLICATIONS TABLE
        # =====================================================

        job_application_columns = {

            "ai_match_score": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_match_score FLOAT"
            ),

            "ai_shortlist": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_shortlist BOOLEAN DEFAULT 0"
            ),

            "ai_recommendation": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_recommendation VARCHAR(100)"
            ),

            "ai_skill_score": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_skill_score FLOAT"
            ),

            "ai_experience_score": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_experience_score FLOAT"
            ),

            "ai_role_score": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_role_score FLOAT"
            ),

            "ai_location_score": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_location_score FLOAT"
            ),

            "ai_technology_score": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_technology_score FLOAT"
            ),

            "ai_education_score": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_education_score FLOAT"
            ),

            "ai_matched_skills": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_matched_skills TEXT"
            ),

            "ai_missing_skills": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_missing_skills TEXT"
            ),

            "ai_analyzed_at": (
                "ALTER TABLE job_applications "
                "ADD COLUMN ai_analyzed_at DATETIME"
            ),

            "recruiter_shortlisted": (
                "ALTER TABLE job_applications "
                "ADD COLUMN recruiter_shortlisted BOOLEAN DEFAULT 0"
            ),

            "recruiter_notes": (
                "ALTER TABLE job_applications "
                "ADD COLUMN recruiter_notes VARCHAR(2000)"
            ),

            "is_archived": (
                "ALTER TABLE job_applications "
                "ADD COLUMN is_archived BOOLEAN DEFAULT 0"
            ),

            "archived_at": (
                "ALTER TABLE job_applications "
                "ADD COLUMN archived_at DATETIME"
            ),
        }

        job_applications_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name='job_applications'"
            )
        ).fetchone()

        if job_applications_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(job_applications)"
                    )
                ).fetchall()
            }

            for column_name, alter_sql in (
                job_application_columns.items()
            ):

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )

            # -------------------------------------------------
            # SAFE DEFAULTS
            # -------------------------------------------------

            current_application_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(job_applications)"
                    )
                ).fetchall()
            }

            if (
                "ai_shortlist"
                in current_application_columns
            ):

                connection.execute(
                    text(
                        "UPDATE job_applications "
                        "SET ai_shortlist = 0 "
                        "WHERE ai_shortlist IS NULL"
                    )
                )

            if (
                "recruiter_shortlisted"
                in current_application_columns
            ):

                connection.execute(
                    text(
                        "UPDATE job_applications "
                        "SET recruiter_shortlisted = 0 "
                        "WHERE recruiter_shortlisted IS NULL"
                    )
                )

            if (
                "is_archived"
                in current_application_columns
            ):

                connection.execute(
                    text(
                        "UPDATE job_applications "
                        "SET is_archived = 0 "
                        "WHERE is_archived IS NULL"
                    )
                )

        # =====================================================
        # APTITUDE TESTS TABLE
        # =====================================================

        aptitude_test_columns = {

            "question_source": (
                "ALTER TABLE aptitude_tests "
                "ADD COLUMN question_source VARCHAR(20)"
            ),

            "assigned_at": (
                "ALTER TABLE aptitude_tests "
                "ADD COLUMN assigned_at DATETIME"
            ),

            "expires_at": (
                "ALTER TABLE aptitude_tests "
                "ADD COLUMN expires_at DATETIME"
            ),

            "exam_expires_at": (
                "ALTER TABLE aptitude_tests "
                "ADD COLUMN exam_expires_at DATETIME"
            ),
        }

        aptitude_tests_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name='aptitude_tests'"
            )
        ).fetchone()

        if aptitude_tests_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(aptitude_tests)"
                    )
                ).fetchall()
            }

            for column_name, alter_sql in (
                aptitude_test_columns.items()
            ):

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )

            connection.execute(
                text(
                    "UPDATE aptitude_tests "
                    "SET question_source = 'fallback' "
                    "WHERE question_source IS NULL "
                    "OR question_source = ''"
                )
            )

        # =====================================================
        # AI INTERVIEW TURNS V2 TABLE
        # =====================================================

        ai_interview_turns_v2_columns = {

            "question_focus": (
                "ALTER TABLE ai_interview_turns_v2 "
                "ADD COLUMN question_focus VARCHAR(30)"
            ),
        }

        ai_interview_turns_v2_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' "
                "AND name='ai_interview_turns_v2'"
            )
        ).fetchone()

        if ai_interview_turns_v2_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(ai_interview_turns_v2)"
                    )
                ).fetchall()
            }

            for column_name, alter_sql in (
                ai_interview_turns_v2_columns.items()
            ):

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )

        # =====================================================
        # COMPANIES TABLE
        # =====================================================

        companies_columns = {

            "hr_name": (
                "ALTER TABLE companies "
                "ADD COLUMN hr_name VARCHAR(150)"
            ),

            "username": (
                "ALTER TABLE companies "
                "ADD COLUMN username VARCHAR(80)"
            ),

            "created_at": (
                "ALTER TABLE companies "
                "ADD COLUMN created_at DATETIME"
            ),

            "email_verification_hash": (
                "ALTER TABLE companies "
                "ADD COLUMN email_verification_hash VARCHAR(64)"
            ),

            "email_verification_expires_at": (
                "ALTER TABLE companies "
                "ADD COLUMN email_verification_expires_at DATETIME"
            ),

            "email_verification_attempts": (
                "ALTER TABLE companies "
                "ADD COLUMN email_verification_attempts INTEGER NOT NULL DEFAULT 0"
            ),

            "email_verification_sent_at": (
                "ALTER TABLE companies "
                "ADD COLUMN email_verification_sent_at DATETIME"
            ),
        }

        companies_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='companies'"
            )
        ).fetchone()

        if companies_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(companies)"
                    )
                ).fetchall()
            }

            first_time_verification = "email_verification_hash" not in existing_columns

            for column_name, alter_sql in companies_columns.items():

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )

            if first_time_verification:

                # Email verification is introduced now: every HR account that already exists keeps working (it is treated as verified once).
                connection.execute(text("UPDATE companies SET email_verified = 1 WHERE email_verified = 0"))

        # =====================================================
        # CANDIDATE INVITES TABLE
        # =====================================================

        candidate_invites_columns = {

            "scheduled_at": (
                "ALTER TABLE candidate_invites "
                "ADD COLUMN scheduled_at DATETIME"
            ),

            "reminder_24h_sent": (
                "ALTER TABLE candidate_invites "
                "ADD COLUMN reminder_24h_sent BOOLEAN DEFAULT 0"
            ),

            "reminder_2h_sent": (
                "ALTER TABLE candidate_invites "
                "ADD COLUMN reminder_2h_sent BOOLEAN DEFAULT 0"
            ),
        }

        candidate_invites_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='candidate_invites'"
            )
        ).fetchone()

        if candidate_invites_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(candidate_invites)"
                    )
                ).fetchall()
            }

            for column_name, alter_sql in candidate_invites_columns.items():

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )

        # =====================================================
        # APTITUDE TESTS TABLE
        # =====================================================

        aptitude_tests_columns = {

            "draft_answers": (
                "ALTER TABLE aptitude_tests "
                "ADD COLUMN draft_answers TEXT"
            ),
        }

        aptitude_tests_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='aptitude_tests'"
            )
        ).fetchone()

        if aptitude_tests_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(aptitude_tests)"
                    )
                ).fetchall()
            }

            for column_name, alter_sql in aptitude_tests_columns.items():

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )

        # =====================================================
        # PROGRAMMING ASSESSMENTS V2 TABLE
        # =====================================================

        programming_assessments_v2_columns = {

            "draft_code_json": (
                "ALTER TABLE programming_assessments_v2 "
                "ADD COLUMN draft_code_json TEXT"
            ),
        }

        programming_assessments_v2_exists = connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='programming_assessments_v2'"
            )
        ).fetchone()

        if programming_assessments_v2_exists:

            existing_columns = {
                row[1]
                for row in connection.execute(
                    text(
                        "PRAGMA table_info(programming_assessments_v2)"
                    )
                ).fetchall()
            }

            for column_name, alter_sql in programming_assessments_v2_columns.items():

                if column_name not in existing_columns:

                    connection.execute(
                        text(alter_sql)
                    )


# =========================================================
# INITIALIZE DATABASE
# =========================================================

def initialize_database():
    """
    Import all models before create_all() so SQLAlchemy
    knows about every table in the application.
    """

    # -----------------------------------------------------
    # CORE MODELS
    # -----------------------------------------------------

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
    from backend.app.models.resume import Resume
    from backend.app.models.candidate_profile import (
        CandidateProfile
    )

    # -----------------------------------------------------
    # JOB MODELS
    # -----------------------------------------------------

    from backend.app.models.job import Job
    from backend.app.models.job_question import (
        JobQuestion
    )

    # -----------------------------------------------------
    # APPLICATION MODELS
    # -----------------------------------------------------

    from backend.app.models.job_application import (
        JobApplication
    )
    from backend.app.models.offer_letter_log import OfferLetterLog
    from backend.app.models.practical_roleplay import PracticalRoleplay

    from backend.app.models.application_answer import (
        ApplicationAnswer
    )

    from backend.app.models.application_status_history import (
        ApplicationStatusHistory
    )

    from backend.app.models.aptitude_test import (
        AptitudeTest
    )

    from backend.app.models.situational_judgment_test import (
        SituationalJudgmentTest
    )

    from backend.app.models.technical_domain_assessment import (
        TechnicalDomainAssessment
    )

    # -----------------------------------------------------
    # AUTHENTICATION
    # -----------------------------------------------------

    from backend.app.models.auth_session import (
        AuthSession
    )

    # -----------------------------------------------------
    # ASSESSMENT ENGINE
    # -----------------------------------------------------

    from backend.app.models.assessment_session import (
        AssessmentSession
    )

    from backend.app.models.programming_test import (
        ProgrammingTest
    )

    from backend.app.models.programming_submission import (
        ProgrammingSubmission
    )

    from backend.app.models.interview_test import (
        InterviewTest
    )

    from backend.app.models.interview_response import (
        InterviewResponse
    )

    from backend.app.models.assessment_assignment import (
        AssessmentAssignment
    )

    from backend.app.models.assessment_integrity_event import (
        AssessmentIntegrityEvent
    )

    from backend.app.models.assessment_video_recording import (
        AssessmentVideoRecording
    )

    from backend.app.models.assessment_snapshot import (
        AssessmentSnapshot
    )

    from backend.app.models.face_match_result import FaceMatchResult  # noqa: F401 - registers the table

    from backend.app.models.assessment_consent import (
        AssessmentConsent
    )

    # Programming Engine V2, Live Integration task — new,
    # additive tables only. No FK to legacy programming_tests/
    # programming_submissions; create_all() below only creates
    # tables that don't already exist, so this never touches any
    # existing table.
    from backend.app.models.programming_assessment_v2 import (
        ProgrammingAssessmentV2
    )

    from backend.app.models.programming_submission_v2 import (
        ProgrammingSubmissionV2
    )

    # AI Interview V2 (Task 5H) — new, additive tables only. No FK
    # to legacy interview_tests/interview_responses; the legacy AI
    # Interview flow is left completely untouched.
    from backend.app.models.ai_interview_session_v2 import (
        AIInterviewSessionV2
    )

    from backend.app.models.ai_interview_turn_v2 import (
        AIInterviewTurnV2
    )

    # HR-sourced candidate invite (Phase 1) -- new, additive table only.
    from backend.app.models.candidate_invite import (
        CandidateInvite
    )

    # Admin moderation: warnings issued to a company -- new, additive table only.
    from backend.app.models.admin_warning import AdminWarning
    from backend.app.models.llm_usage_log import LlmUsageLog
    from backend.app.models.admin_setting import AdminSetting
    from backend.app.models.recording_behavior_summary import RecordingBehaviorSummary

    # Keep model imports active so SQLAlchemy registers them.
    _ = (
        User,
        Company,
        PasswordResetOTP,
        ResumeAnalysis,
        UserActivity,
        Resume,
        CandidateProfile,
        Job,
        JobQuestion,
        JobApplication,
        ApplicationAnswer,
        ApplicationStatusHistory,
        AptitudeTest,
        AuthSession,
        AssessmentSession,
        ProgrammingTest,
        ProgrammingSubmission,
        InterviewTest,
        InterviewResponse,
        AssessmentAssignment,
        AssessmentIntegrityEvent,
        AssessmentVideoRecording,
        AssessmentSnapshot,
        AssessmentConsent,
        ProgrammingAssessmentV2,
        ProgrammingSubmissionV2,
        AIInterviewSessionV2,
        AIInterviewTurnV2,
        CandidateInvite,
        AdminWarning,
        LlmUsageLog,
        AdminSetting,
        RecordingBehaviorSummary,
    )

    # -----------------------------------------------------
    # CREATE MISSING TABLES
    # -----------------------------------------------------

    Base.metadata.create_all(
        bind=engine
    )

    # -----------------------------------------------------
    # SAFE SCHEMA MIGRATIONS
    # -----------------------------------------------------

    migrate_database()