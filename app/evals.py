"""Opt-in live smoke evaluations for the configured agent providers."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.agent import stream_agent_response


async def evaluate_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        response_chunks: list[str] = []
        error = None
        async for event in stream_agent_response(case["prompt"]):
            if event["type"] == "token":
                response_chunks.append(str(event.get("text", "")))
            elif event["type"] == "error":
                error = str(event.get("text", "agent request failed"))

        response = "".join(response_chunks).casefold()
        expected = [text.casefold() for text in case["expected_contains"]]
        missing = [text for text in expected if text not in response]
        results.append(
            {
                "id": case["id"],
                "passed": error is None and not missing,
                "missing_expectations": missing,
                "error": error,
            }
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run opt-in agent smoke evaluations")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).parent.parent / "evals" / "cases.json",
        help="JSON file containing prompt and expected_contains cases",
    )
    args = parser.parse_args()

    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        results = asyncio.run(evaluate_cases(cases))
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        parser.exit(2, f"Could not run evaluations: {error}\n")

    for result in results:
        label = "PASS" if result["passed"] else "FAIL"
        print(f"[{label}] {result['id']}")
        if result["missing_expectations"]:
            print(f"  Missing expected text: {', '.join(result['missing_expectations'])}")
        if result["error"]:
            print(f"  Provider error: {result['error']}")

    passed = sum(result["passed"] for result in results)
    print(f"{passed}/{len(results)} evaluations passed")
    raise SystemExit(passed != len(results))


if __name__ == "__main__":
    main()