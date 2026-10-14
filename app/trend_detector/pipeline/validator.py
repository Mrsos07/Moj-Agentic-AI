"""
بوابة التقييم — تحكم HOT / EARLY / NOT_YET من تقييم المرشح وحده.

كانت ``XValidator`` تتحقق عبر خادم X API خاص (``X_API_SERVER_URL``) لم يُهيّأ قط، فكانت
تعمل دائماً بهذا الوضع الاحتياطي. أُزيل التحقق الخارجي ضمن الاكتفاء بـ Tavily؛ تعدد
المصادر التي تغطي القصة (من Tavily) هو الدليل على الانتشار داخل التقييم نفسه.
"""
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy.orm import Session

from app.trend_detector.config import VALIDATION_THRESHOLDS, WATCHLIST_CHECK_INTERVAL, WATCHLIST_MAX_CHECKS
from app.trend_detector.models import Candidate, Watchlist


class ScoreGate:
    """HOT ← تقييم ≥ حد hot · EARLY ← ≥ حد early (قائمة المراقبة) · وإلا NOT_YET."""

    async def validate(self, candidates: List[Candidate], db: Session, quiet: bool = False) -> List[Candidate]:
        hot_candidates = []
        for candidate in candidates:
            if candidate.score >= VALIDATION_THRESHOLDS["hot"]["min_score"]:
                candidate.status = "hot"
                hot_candidates.append(candidate)
            elif candidate.score >= VALIDATION_THRESHOLDS["early"]["min_score"]:
                candidate.status = "early"
                self._add_to_watchlist(candidate, db)
            else:
                candidate.status = "not_yet"
        db.commit()
        if not quiet:
            print(f"[ScoreGate] {len(hot_candidates)} HOT of {len(candidates)}")
        return hot_candidates

    def _add_to_watchlist(self, candidate: Candidate, db: Session):
        """Add an EARLY candidate to the watchlist for re-checking"""
        existing = (
            db.query(Watchlist)
            .filter(Watchlist.candidate_id == candidate.id, Watchlist.is_active == True)  # noqa: E712
            .first()
        )
        if existing:
            return
        db.add(Watchlist(
            candidate_id=candidate.id,
            check_count=0,
            max_checks=WATCHLIST_MAX_CHECKS,
            next_check_at=datetime.now(timezone.utc) + timedelta(minutes=WATCHLIST_CHECK_INTERVAL),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        ))
