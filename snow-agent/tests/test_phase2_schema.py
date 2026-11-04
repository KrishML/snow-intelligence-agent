"""
Smoke tests for Phase 2 ORM schema — task 2.3.

Strategy
--------
All assertions use pure Python ORM metadata inspection — no database
connection is needed.  We interrogate:

  - ``__tablename__``   — correct table names
  - ``__table_args__``  — CHECK constraint names (by iterating CheckConstraint
                          entries in the tuple)
  - ``__table__.columns`` — required columns are declared
  - column type         — Vector(1536) for pgvector embedding columns

Requirements: 15.1, 15.2, 15.3, 15.4, 15.5
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint
from pgvector.sqlalchemy import Vector

from app.db.models.qualitative_signal import QualitativeSignal
from app.db.models.webcam_snapshot import WebcamSnapshot
from app.db.models.trip_plan import TripPlan
from app.db.models.embeddings import (
    PreferenceEmbedding,
    TripFeedbackEmbedding,
    ResortKnowledgeEmbedding,
    SessionSummaryEmbedding,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_constraint_names(model) -> set[str]:
    """Return the set of CheckConstraint names declared in a model's __table_args__."""
    return {
        arg.name
        for arg in model.__table_args__
        if isinstance(arg, CheckConstraint)
    }


def _column_names(model) -> set[str]:
    """Return the set of column names declared on a model."""
    return {c.name for c in model.__table__.columns}


# ---------------------------------------------------------------------------
# 1.  Table names
# ---------------------------------------------------------------------------


class TestTableNames:
    def test_qualitative_signal_tablename(self):
        assert QualitativeSignal.__tablename__ == "qualitative_signals"

    def test_webcam_snapshot_tablename(self):
        assert WebcamSnapshot.__tablename__ == "webcam_snapshots"

    def test_trip_plan_tablename(self):
        assert TripPlan.__tablename__ == "trip_plans"

    def test_preference_embedding_tablename(self):
        assert PreferenceEmbedding.__tablename__ == "preference_embeddings"

    def test_trip_feedback_embedding_tablename(self):
        assert TripFeedbackEmbedding.__tablename__ == "trip_feedback_embeddings"

    def test_resort_knowledge_embedding_tablename(self):
        assert ResortKnowledgeEmbedding.__tablename__ == "resort_knowledge_embeddings"

    def test_session_summary_embedding_tablename(self):
        assert SessionSummaryEmbedding.__tablename__ == "session_summary_embeddings"


# ---------------------------------------------------------------------------
# 2.  CHECK constraint names
# ---------------------------------------------------------------------------


class TestCheckConstraints:
    def test_qualitative_source_type_constraint(self):
        assert "ck_qualitative_source_type" in _check_constraint_names(QualitativeSignal)

    def test_qualitative_sentiment_constraint(self):
        assert "ck_qualitative_sentiment" in _check_constraint_names(QualitativeSignal)

    def test_webcam_classification_constraint(self):
        assert "ck_webcam_classification" in _check_constraint_names(WebcamSnapshot)

    def test_trip_plan_status_constraint(self):
        assert "ck_trip_plan_status" in _check_constraint_names(TripPlan)


# ---------------------------------------------------------------------------
# 3.  Required columns per model
# ---------------------------------------------------------------------------


class TestRequiredColumns:
    def test_qualitative_signal_columns(self):
        cols = _column_names(QualitativeSignal)
        for required in (
            "id", "weekly_run_id", "resort_id", "source_type",
            "raw_snippet", "summary", "sentiment",
            "negative_keywords", "score", "created_at",
        ):
            assert required in cols, f"QualitativeSignal missing column '{required}'"

    def test_webcam_snapshot_columns(self):
        cols = _column_names(WebcamSnapshot)
        for required in (
            "id", "weekly_run_id", "resort_id", "captured_at",
            "classification", "description", "file_path", "created_at",
        ):
            assert required in cols, f"WebcamSnapshot missing column '{required}'"

    def test_trip_plan_columns(self):
        cols = _column_names(TripPlan)
        for required in (
            "id", "session_id", "resort_id", "outbound_date", "return_date",
            "flight_option", "hotel_option", "total_estimated_gbp",
            "session_notes", "status", "created_at", "updated_at",
        ):
            assert required in cols, f"TripPlan missing column '{required}'"

    def test_preference_embedding_columns(self):
        cols = _column_names(PreferenceEmbedding)
        for required in ("id", "user_id", "text", "embedding", "created_at"):
            assert required in cols, f"PreferenceEmbedding missing column '{required}'"

    def test_trip_feedback_embedding_columns(self):
        cols = _column_names(TripFeedbackEmbedding)
        for required in ("id", "user_id", "text", "embedding", "created_at"):
            assert required in cols, f"TripFeedbackEmbedding missing column '{required}'"

    def test_resort_knowledge_embedding_columns(self):
        cols = _column_names(ResortKnowledgeEmbedding)
        for required in ("id", "resort_id", "season", "text", "embedding", "created_at"):
            assert required in cols, f"ResortKnowledgeEmbedding missing column '{required}'"

    def test_session_summary_embedding_columns(self):
        cols = _column_names(SessionSummaryEmbedding)
        for required in ("id", "user_id", "session_id", "text", "embedding", "created_at"):
            assert required in cols, f"SessionSummaryEmbedding missing column '{required}'"


# ---------------------------------------------------------------------------
# 4.  Vector type on PreferenceEmbedding
# ---------------------------------------------------------------------------


class TestVectorType:
    def test_preference_embedding_has_vector_column(self):
        col = PreferenceEmbedding.__table__.columns["embedding"]
        assert isinstance(col.type, Vector), (
            f"PreferenceEmbedding.embedding must be Vector, got {type(col.type)}"
        )

    def test_preference_embedding_vector_dimension(self):
        col = PreferenceEmbedding.__table__.columns["embedding"]
        assert col.type.dim == 1536, (
            f"PreferenceEmbedding.embedding must be Vector(1536), got dim={col.type.dim}"
        )


# ---------------------------------------------------------------------------
# 5.  ResortKnowledgeEmbedding specific columns
# ---------------------------------------------------------------------------


class TestResortKnowledgeEmbedding:
    def test_resort_id_column(self):
        assert "resort_id" in _column_names(ResortKnowledgeEmbedding)

    def test_season_column(self):
        assert "season" in _column_names(ResortKnowledgeEmbedding)

    def test_embedding_column(self):
        assert "embedding" in _column_names(ResortKnowledgeEmbedding)

    def test_resort_knowledge_vector_dimension(self):
        col = ResortKnowledgeEmbedding.__table__.columns["embedding"]
        assert isinstance(col.type, Vector)
        assert col.type.dim == 1536
