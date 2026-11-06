import json
from pathlib import Path

GOLDEN = Path(__file__).parents[1] / "eval" / "golden.jsonl"


def test_golden_set_has_ten_to_fifteen_labeled_cases():
    cases = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    assert 10 <= len(cases) <= 15
    assert all(case["question"] and case["expected_docs"] for case in cases)
    assert all(case["must_abstain"] in (True, False) for case in cases)


def test_no_case_expects_an_archived_manual():
    cases = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    for case in cases:
        assert all("VERSIONES ANTERIORES" not in doc for doc in case["expected_docs"])
