from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import isclose

from db.models.attempt_quiz import (
    MainTopicPerformance,
    QuizAnswerEvaluation,
    QuizAnswerStatus,
    QuizAnswerSubmission,
    QuizAttemptAnswer,
    QuizAttemptCreate,
    QuizAttemptResult,
    QuizAttemptSubmission,
    QuizQuestionResponse,
    SubTopicPerformance,
    TopicPerformanceStatus,
    WeakTopic,
)
from db.models.generated_quiz import (
    FillInTheBlankQuestion,
    GeneratedQuizBase,
    GeneratedQuizQuestion,
    MatchTheFollowingQuestion,
    MultipleCorrectMCQQuestion,
    SingleCorrectMCQQuestion,
    TrueFalseQuestion,
)

MIN_QUESTIONS_FOR_TOPIC_CLASSIFICATION = 2
WEAK_TOPIC_PERCENTAGE = 50.0
STRONG_TOPIC_PERCENTAGE = 75.0


class QuizSubmissionError(ValueError):
    """The submitted answers do not match the generated quiz structure."""


class QuizDefinitionError(RuntimeError):
    """The stored generated quiz cannot be evaluated safely."""


@dataclass
class _PerformanceStats:
    awarded_marks: float = 0.0
    maximum_marks: float = 0.0
    correct: int = 0
    partially_correct: int = 0
    incorrect: int = 0
    attempted: int = 0
    total_questions: int = 0

    def add(self, *, awarded_marks: float, status: QuizAnswerStatus) -> None:
        self.awarded_marks += awarded_marks
        self.maximum_marks += 1.0
        self.total_questions += 1
        if status == "skipped":
            return
        self.attempted += 1
        if status == "correct":
            self.correct += 1
        elif status == "partially_correct":
            self.partially_correct += 1
        else:
            self.incorrect += 1

    @property
    def percentage(self) -> float:
        if self.maximum_marks == 0:
            return 0.0
        return round(self.awarded_marks / self.maximum_marks * 100, 2)


@dataclass
class _TopicStats:
    name: str
    performance: _PerformanceStats = field(default_factory=_PerformanceStats)
    sub_topics: dict[str, "_TopicStats"] = field(default_factory=dict)


def _ensure_only_response_fields(
    response: QuizQuestionResponse,
    *,
    allowed: set[str],
    question_id: str,
) -> None:
    populated = {
        "selected_options": bool(response.selected_options),
        "boolean_answer": response.boolean_answer is not None,
        "blank_answers": bool(response.blank_answers),
        "matches": bool(response.matches),
    }
    unexpected = [
        name
        for name, present in populated.items()
        if present and name not in allowed
    ]
    if unexpected:
        raise QuizSubmissionError(
            f"Question {question_id} received unsupported response fields: "
            f"{', '.join(unexpected)}."
        )


def _score_single_correct(
    question: SingleCorrectMCQQuestion,
    response: QuizQuestionResponse,
) -> tuple[float, bool]:
    _ensure_only_response_fields(
        response,
        allowed={"selected_options"},
        question_id=question.id,
    )
    if not response.selected_options:
        return 0.0, False
    if len(response.selected_options) != 1:
        raise QuizSubmissionError(
            f"Question {question.id} accepts exactly one selected option."
        )
    return (
        float(response.selected_options[0] == question.correct_answer.option),
        True,
    )


def _score_multiple_correct(
    question: MultipleCorrectMCQQuestion,
    response: QuizQuestionResponse,
) -> tuple[float, bool]:
    _ensure_only_response_fields(
        response,
        allowed={"selected_options"},
        question_id=question.id,
    )
    selected_options = response.selected_options
    if not selected_options:
        return 0.0, False
    if len(selected_options) != len(set(selected_options)):
        raise QuizSubmissionError(
            f"Question {question.id} contains duplicate selected options."
        )

    correct = {answer.option for answer in question.correct_answers}
    if not correct:
        raise QuizDefinitionError(
            f"Question {question.id} has no correct options."
        )

    selected = set(selected_options)
    if selected == correct:
        return 1.0, True
    if not question.scoring.allow_partial_credit:
        return 0.0, True

    correct_selected = len(selected & correct)
    incorrect_selected = len(selected - correct)
    score = (correct_selected - incorrect_selected) / len(correct)
    return max(0.0, min(1.0, score)), True


def _score_true_false(
    question: TrueFalseQuestion,
    response: QuizQuestionResponse,
) -> tuple[float, bool]:
    _ensure_only_response_fields(
        response,
        allowed={"boolean_answer"},
        question_id=question.id,
    )
    if response.boolean_answer is None:
        return 0.0, False
    return float(response.boolean_answer is question.correct_answer), True


def _normalize_blank_answer(value: str, *, case_sensitive: bool) -> str:
    normalized = " ".join(value.split())
    return normalized if case_sensitive else normalized.casefold()


def _score_fill_in_the_blank(
    question: FillInTheBlankQuestion,
    response: QuizQuestionResponse,
) -> tuple[float, bool]:
    _ensure_only_response_fields(
        response,
        allowed={"blank_answers"},
        question_id=question.id,
    )
    if not question.blanks:
        raise QuizDefinitionError(f"Question {question.id} has no blanks.")
    if not response.blank_answers:
        return 0.0, False

    submitted: dict[str, str] = {}
    valid_blank_ids = {blank.blank_id for blank in question.blanks}
    for blank in response.blank_answers:
        if blank.blank_id in submitted:
            raise QuizSubmissionError(
                f"Question {question.id} contains duplicate answer for blank "
                f"{blank.blank_id}."
            )
        if blank.blank_id not in valid_blank_ids:
            raise QuizSubmissionError(
                f"Question {question.id} contains unknown blank {blank.blank_id}."
            )
        submitted[blank.blank_id] = blank.answer

    correct_count = 0
    for blank in question.blanks:
        if not blank.correct_answers:
            raise QuizDefinitionError(
                f"Question {question.id} blank {blank.blank_id} has no correct answers."
            )
        submitted_answer = submitted.get(blank.blank_id)
        if submitted_answer is None:
            continue
        normalized_submission = _normalize_blank_answer(
            submitted_answer,
            case_sensitive=blank.case_sensitive,
        )
        accepted = {
            _normalize_blank_answer(
                answer,
                case_sensitive=blank.case_sensitive,
            )
            for answer in blank.correct_answers
        }
        if normalized_submission in accepted:
            correct_count += 1

    return correct_count / len(question.blanks), True


def _score_matching(
    question: MatchTheFollowingQuestion,
    response: QuizQuestionResponse,
) -> tuple[float, bool]:
    _ensure_only_response_fields(
        response,
        allowed={"matches"},
        question_id=question.id,
    )
    if not question.correct_matches:
        raise QuizDefinitionError(
            f"Question {question.id} has no correct matches."
        )
    if not response.matches:
        return 0.0, False

    valid_left_ids = {item.id for item in question.left_items}
    valid_right_ids = {item.id for item in question.right_items}
    left_ids: set[str] = set()
    right_ids: set[str] = set()
    submitted: set[tuple[str, str]] = set()
    for match in response.matches:
        if (
            match.left_id not in valid_left_ids
            or match.right_id not in valid_right_ids
        ):
            raise QuizSubmissionError(
                f"Question {question.id} contains an unknown match item."
            )
        if match.left_id in left_ids or match.right_id in right_ids:
            raise QuizSubmissionError(
                f"Question {question.id} contains duplicate match items."
            )
        left_ids.add(match.left_id)
        right_ids.add(match.right_id)
        submitted.add((match.left_id, match.right_id))

    correct = {
        (match.left_id, match.right_id)
        for match in question.correct_matches
    }
    return len(submitted & correct) / len(correct), True


def _score_question(
    question: GeneratedQuizQuestion,
    response: QuizQuestionResponse,
) -> tuple[float, bool]:
    if isinstance(question, SingleCorrectMCQQuestion):
        return _score_single_correct(question, response)
    if isinstance(question, MultipleCorrectMCQQuestion):
        return _score_multiple_correct(question, response)
    if isinstance(question, TrueFalseQuestion):
        return _score_true_false(question, response)
    if isinstance(question, FillInTheBlankQuestion):
        return _score_fill_in_the_blank(question, response)
    if isinstance(question, MatchTheFollowingQuestion):
        return _score_matching(question, response)
    raise QuizDefinitionError(f"Question {question.id} has an unsupported type.")


def _answer_status(*, score: float, attempted: bool) -> QuizAnswerStatus:
    if not attempted:
        return "skipped"
    if isclose(score, 1.0, abs_tol=1e-9):
        return "correct"
    if score <= 0:
        return "incorrect"
    return "partially_correct"


def _topic_status(stats: _PerformanceStats) -> TopicPerformanceStatus:
    if stats.attempted < MIN_QUESTIONS_FOR_TOPIC_CLASSIFICATION:
        return "insufficient_data"
    if stats.percentage < WEAK_TOPIC_PERCENTAGE:
        return "weak"
    if stats.percentage < STRONG_TOPIC_PERCENTAGE:
        return "needs_practice"
    return "strong"


def _topic_key(label: str) -> str:
    return " ".join(label.split()).casefold()


def _build_topic_performance(
    answers: list[QuizAttemptAnswer],
) -> tuple[list[MainTopicPerformance], list[WeakTopic]]:
    main_topics: dict[str, _TopicStats] = {}

    for answer in answers:
        evaluation = answer.evaluation
        if evaluation is None:
            raise QuizDefinitionError(
                f"Question {answer.question_id} is missing its evaluation."
            )
        main_key = _topic_key(answer.topic.main_topic)
        main_stats = main_topics.setdefault(
            main_key,
            _TopicStats(name=" ".join(answer.topic.main_topic.split())),
        )
        main_stats.performance.add(
            awarded_marks=evaluation.awarded_marks,
            status=evaluation.status,
        )

        seen_sub_topics: set[str] = set()
        for sub_topic in answer.topic.sub_topics:
            sub_key = _topic_key(sub_topic)
            if not sub_key or sub_key in seen_sub_topics:
                continue
            seen_sub_topics.add(sub_key)
            sub_stats = main_stats.sub_topics.setdefault(
                sub_key,
                _TopicStats(name=" ".join(sub_topic.split())),
            )
            sub_stats.performance.add(
                awarded_marks=evaluation.awarded_marks,
                status=evaluation.status,
            )

    topic_performance: list[MainTopicPerformance] = []
    weak_topics: list[WeakTopic] = []
    for main_stats in main_topics.values():
        sub_topic_performance: list[SubTopicPerformance] = []
        for sub_stats in main_stats.sub_topics.values():
            stats = sub_stats.performance
            status = _topic_status(stats)
            sub_topic_performance.append(
                SubTopicPerformance(
                    name=sub_stats.name,
                    correct=stats.correct,
                    attempted=stats.attempted,
                    total_questions=stats.total_questions,
                    percentage=stats.percentage,
                    status=status,
                )
            )
            if status == "weak":
                weak_topics.append(
                    WeakTopic(
                        main_topic=main_stats.name,
                        sub_topic=sub_stats.name,
                        percentage=stats.percentage,
                        question_count=stats.attempted,
                    )
                )

        stats = main_stats.performance
        topic_performance.append(
            MainTopicPerformance(
                main_topic=main_stats.name,
                correct=stats.correct,
                partially_correct=stats.partially_correct,
                incorrect=stats.incorrect,
                attempted=stats.attempted,
                total_questions=stats.total_questions,
                percentage=stats.percentage,
                status=_topic_status(stats),
                sub_topics=sub_topic_performance,
            )
        )

    return topic_performance, weak_topics


def _submission_by_question_id(
    submission: QuizAttemptSubmission,
) -> dict[str, QuizAnswerSubmission]:
    answers: dict[str, QuizAnswerSubmission] = {}
    for answer in submission.answers:
        if answer.question_id in answers:
            raise QuizSubmissionError(
                f"Question {answer.question_id} was submitted more than once."
            )
        answers[answer.question_id] = answer
    return answers


def evaluate_quiz(
    *,
    quiz_id: str,
    quiz: GeneratedQuizBase,
    submission: QuizAttemptSubmission,
    submitted_at: datetime | None = None,
) -> QuizAttemptCreate:
    """Evaluate one submission deterministically against a stored generated quiz."""
    if not quiz.questions:
        raise QuizDefinitionError("The generated quiz has no questions.")

    questions_by_id = {question.id: question for question in quiz.questions}
    if len(questions_by_id) != len(quiz.questions):
        raise QuizDefinitionError("The generated quiz contains duplicate question IDs.")

    submitted_answers = _submission_by_question_id(submission)
    unknown_question_ids = submitted_answers.keys() - questions_by_id.keys()
    if unknown_question_ids:
        unknown = ", ".join(sorted(unknown_question_ids))
        raise QuizSubmissionError(f"Unknown question IDs: {unknown}.")

    evaluated_answers: list[QuizAttemptAnswer] = []
    status_counts: dict[QuizAnswerStatus, int] = {
        "correct": 0,
        "incorrect": 0,
        "partially_correct": 0,
        "skipped": 0,
    }
    total_score = 0.0
    total_duration = 0
    evaluation_time = submitted_at or datetime.now(timezone.utc)

    for question in quiz.questions:
        submitted = submitted_answers.get(question.id)
        response = (
            submitted.response
            if submitted is not None
            else QuizQuestionResponse()
        )
        score, attempted = _score_question(question, response)
        status = _answer_status(score=score, attempted=attempted)
        awarded_marks = round(score, 4)
        status_counts[status] += 1
        total_score += awarded_marks
        time_taken = submitted.time_taken_seconds if submitted else 0
        total_duration += time_taken

        evaluated_answers.append(
            QuizAttemptAnswer(
                question_id=question.id,
                question_type=question.type,
                response=response.model_copy(deep=True),
                evaluation=QuizAnswerEvaluation(
                    status=status,
                    is_correct=status == "correct",
                    awarded_marks=awarded_marks,
                    maximum_marks=1,
                ),
                topic=question.topic.model_copy(deep=True),
                time_taken_seconds=time_taken,
                answered_at=evaluation_time if attempted else None,
            )
        )

    maximum_score = float(len(quiz.questions))
    score = round(total_score, 4)
    topic_performance, weak_topics = _build_topic_performance(evaluated_answers)

    return QuizAttemptCreate(
        quiz_id=quiz_id,
        user_id=quiz.user_id,
        chat_id=quiz.chat_id,
        submission_id=submission.submission_id,
        status="evaluated",
        started_at=evaluation_time - timedelta(seconds=total_duration),
        submitted_at=evaluation_time,
        duration_seconds=total_duration,
        answers=evaluated_answers,
        result=QuizAttemptResult(
            score=score,
            maximum_score=maximum_score,
            percentage=round(score / maximum_score * 100, 2),
            correct=status_counts["correct"],
            incorrect=status_counts["incorrect"],
            partially_correct=status_counts["partially_correct"],
            skipped=status_counts["skipped"],
        ),
        topic_performance=topic_performance,
        weak_topics=weak_topics,
    )
