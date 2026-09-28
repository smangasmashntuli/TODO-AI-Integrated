import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import schemas, services  # noqa: E402  (import after sys.path/env setup)

# The four inputs required by Rules.md section 19.
CORPUS = [
    ("normal", "Finish the Q4 report by Friday and send it to Sarah."),
    ("minimal", "Finish report."),
    ("ambiguous", "Do the presentation Friday."),
    ("complex", (
        "Prepare the investor deck with Sarah and Thabo by EOD Friday. "
        "First pull the Q3 numbers, then draft the slides; it will take about 6 hours "
        "and needs a review from finance before we can send it to the board."
    )),
]


def check(name: str, result: schemas.TaskParseResult) -> list[str]:
    """Structural checks only - never assert exact model wording."""
    problems = []
    title = result.extracted.title
    if not title and not result.clarification.required:
        problems.append("no title and no clarification requested")
    if result.extracted.effort_hours is not None and result.extracted.effort_hours < 0:
        problems.append("negative effort")
    score = result.inferred.priority_score
    if score is not None and not 0 <= score <= 1:
        problems.append(f"priority_score out of range: {score}")
    confidence = result.confidence_level
    if confidence is not None and not 0 <= confidence <= 1:
        problems.append(f"confidence_level out of range: {confidence}")
    due = result.extracted.due_date
    if name in {"normal", "complex"} and due is None:
        problems.append("stated deadline was not resolved to a date")
    if name == "complex" and len(result.extracted.subtasks or []) < 2:
        problems.append("complex input produced fewer than 2 subtasks")
    return problems


def main() -> int:
    if not os.getenv("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set - live corpus skipped.")
        return 2

    service = services.TaskParsingService()
    failed = 0
    for name, text in CORPUS:
        try:
            result = service.parse(text)
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        problems = check(name, result)
        status = "ok" if not problems else "PROBLEM"
        if problems:
            failed += 1
        print(f"[{status}] {name}: title={result.extracted.title!r} "
              f"due={result.extracted.due_date} assignees={result.extracted.assignees} "
              f"subtasks={len(result.extracted.subtasks or [])} "
              f"category={result.inferred.category!r} "
              f"priority={result.inferred.priority_score} "
              f"confidence={result.confidence_level} "
              f"clarification={result.clarification.required}")
        for problem in problems:
            print(f"         - {problem}")

    print(f"\n{len(CORPUS) - failed}/{len(CORPUS)} scenarios passed structure checks.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
