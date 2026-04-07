"""
End-to-end test & evaluation script for the Spanish Herbicide Bedrock Agent.

Sends natural-language queries to the Bedrock Agent, collects responses,
and evaluates correctness using Claude 3.5 Haiku as an LLM judge.

Usage:
    python test_bedrock_agent.py                          # run all 60 test cases
    python test_bedrock_agent.py --cases TC-01,TC-02      # run specific cases
    python test_bedrock_agent.py --cases TC-11:TC-22      # run a range
    python test_bedrock_agent.py --skip-eval              # skip LLM evaluation
    python test_bedrock_agent.py --output my_results.json # custom output file
"""

import sys
import os

# Force UTF-8 output on Windows
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

import boto3
import json
import uuid
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────
AGENT_ID = "GC6LBGNWES"
AGENT_ALIAS_ID = "AMJSCHXS0M"
REGION = "us-east-1"
EVAL_MODEL_ID = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
MAX_TURNS = 3          # max conversation turns per test case
SLEEP_BETWEEN = 2      # seconds between agent calls
DEFAULT_OUTPUT_FILE = "test_results.json"
TEST_CASES_FILE = Path(__file__).parent / "test_cases.json"


# ──────────────────────────────────────────────────────────────
# Load test cases from JSON
# ──────────────────────────────────────────────────────────────
def load_test_cases(filepath=TEST_CASES_FILE):
    """Load test case definitions from the JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def select_cases(all_cases, selector):
    """
    Filter test cases by selector string.
    Supports:
      - Comma-separated IDs: "TC-01,TC-02,TC-10"
      - Range: "TC-11:TC-22"
      - Mixed: "TC-01,TC-11:TC-15,TC-30"
    """
    if not selector:
        return all_cases

    selected_ids = set()
    for part in selector.split(","):
        part = part.strip()
        if ":" in part:
            start, end = part.split(":", 1)
            start_num = int(start.replace("TC-", ""))
            end_num = int(end.replace("TC-", ""))
            for n in range(start_num, end_num + 1):
                selected_ids.add(f"TC-{n:02d}")
        else:
            selected_ids.add(part)

    return [tc for tc in all_cases if tc["id"] in selected_ids]


# ──────────────────────────────────────────────────────────────
# Agent invocation
# ──────────────────────────────────────────────────────────────
def invoke_agent(query, session_id, follow_up_answers=None, verbose=True):
    """
    Send a query to the Bedrock Agent and return the full response text.
    Handles multi-turn conversations if the agent asks follow-up questions.
    """
    client = boto3.client("bedrock-agent-runtime", region_name=REGION)
    follow_up_answers = follow_up_answers or {}
    conversation_log = []

    current_input = query
    for turn in range(MAX_TURNS):
        if verbose:
            print(f"    Turn {turn + 1}: Sending: {current_input[:80]}...")

        response = client.invoke_agent(
            agentId=AGENT_ID,
            agentAliasId=AGENT_ALIAS_ID,
            sessionId=session_id,
            inputText=current_input,
            enableTrace=False,
        )

        # Read streamed chunks
        chunks = []
        for event in response["completion"]:
            if "chunk" in event:
                chunks.append(event["chunk"]["bytes"].decode("utf-8"))
        answer = "".join(chunks).strip()

        conversation_log.append({"role": "user", "text": current_input})
        conversation_log.append({"role": "agent", "text": answer})

        if verbose:
            preview = answer[:120].replace("\n", " ")
            print(f"    Turn {turn + 1}: Response: {preview}...")

        # Check if the agent is asking a follow-up question
        if "?" in answer and turn < MAX_TURNS - 1:
            matched_answer = None
            answer_lower = answer.lower()
            for keyword, reply in follow_up_answers.items():
                if keyword.lower() in answer_lower:
                    matched_answer = reply
                    break

            if matched_answer:
                current_input = matched_answer
                time.sleep(1)
                continue
            else:
                break
        else:
            break

    return answer, conversation_log


# ──────────────────────────────────────────────────────────────
# LLM Evaluation
# ──────────────────────────────────────────────────────────────
def evaluate_with_llm(test_case, agent_response):
    """
    Use Claude 3.5 Haiku on Bedrock to evaluate the agent response.
    Returns a dict with score (1-5), explanation, and per-criterion results.
    """
    client = boto3.client("bedrock-runtime", region_name=REGION)

    criteria_text = "\n".join(
        f"  {i + 1}. {c}" for i, c in enumerate(test_case["criteria"])
    )

    prompt = f"""You are an evaluation judge for a herbicide recommendation chatbot.
You will be given a test query, the chatbot's response, the expected correct behavior, and a list of verification criteria.

Your job is to evaluate whether the chatbot's response is correct.

IMPORTANT: The chatbot responds in Spanish (Castilian). Product names like "Laudis WG", "Monsoon", "Fluva", "Spade Flexx", "Cubix", "Capreno", "Oizysa", "Dimetenamida", "Diflufenican", "Adengo", "Fluoxipir" are the same in Spanish and English.

## Test Query
{test_case['query']}

## Chatbot Response
{agent_response}

## Expected Behavior
{test_case['expected']}

## Verification Criteria
{criteria_text}

## Instructions
For each criterion, determine if it PASSES or FAILS based on the chatbot response.
Then give an overall score from 1 to 5:
  1 = Completely wrong (no valid recommendation, or fundamentally wrong products)
  2 = Mostly wrong (wrong products or major issues)
  3 = Partially correct (some criteria met, some not)
  4 = Mostly correct (minor issues only)
  5 = Fully correct (all criteria met)

Return your evaluation as JSON with this exact structure:
{{
  "criteria_results": {{
    "1": {{"pass": true/false, "note": "brief explanation"}},
    "2": {{"pass": true/false, "note": "brief explanation"}},
    ...
  }},
  "score": <1-5>,
  "explanation": "brief overall explanation"
}}

Return ONLY the JSON, no other text."""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    })

    try:
        response = client.invoke_model(
            modelId=EVAL_MODEL_ID,
            body=body,
        )
        result = json.loads(response["body"].read())
        text = result["content"][0]["text"].strip()

        # Parse JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        evaluation = json.loads(text)
        return evaluation

    except Exception as e:
        return {
            "score": 0,
            "explanation": f"Evaluation LLM error: {str(e)}",
            "criteria_results": {},
        }


# ──────────────────────────────────────────────────────────────
# Display
# ──────────────────────────────────────────────────────────────
def print_summary_table(results):
    """Print a formatted summary table."""
    print("\n" + "=" * 90)
    print(f"{'ID':<8} {'Name':<52} {'Score':>5}  {'Result':<8}")
    print("-" * 90)
    for r in results:
        score = r["evaluation"]["score"]
        status = "PASS" if score >= 4 else "WARN" if score == 3 else "FAIL"
        print(f"{r['test_id']:<8} {r['name']:<52} {score:>5}  {status:<8}")
    print("-" * 90)

    scores = [r["evaluation"]["score"] for r in results]
    avg = sum(scores) / len(scores) if scores else 0
    passed = sum(1 for s in scores if s >= 4)
    print(f"{'TOTAL':<8} {len(results)} tests, {passed} passed, avg score: {avg:.1f}")
    print("=" * 90)


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Test Bedrock Agent for Spanish herbicide recommendations"
    )
    parser.add_argument(
        "--skip-eval", action="store_true",
        help="Skip LLM evaluation, just show agent responses"
    )
    parser.add_argument(
        "--cases", type=str, default=None,
        help="Select test cases: 'TC-01,TC-02' or 'TC-11:TC-22' or mixed"
    )
    parser.add_argument(
        "--output", type=str, default=DEFAULT_OUTPUT_FILE,
        help=f"Output JSON file (default: {DEFAULT_OUTPUT_FILE})"
    )
    parser.add_argument(
        "--test-file", type=str, default=None,
        help="Path to test cases JSON file (default: test_cases.json)"
    )
    args = parser.parse_args()

    # Load and filter test cases
    test_file = Path(args.test_file) if args.test_file else TEST_CASES_FILE
    all_cases = load_test_cases(test_file)
    test_cases = select_cases(all_cases, args.cases)

    if not test_cases:
        print("No test cases matched the selector. Available IDs:")
        for tc in all_cases:
            print(f"  {tc['id']}: {tc['name']}")
        return

    print(f"Running {len(test_cases)} test case(s) against Bedrock Agent...")
    print(f"Agent: {AGENT_ID}, Alias: {AGENT_ALIAS_ID}, Region: {REGION}")
    if not args.skip_eval:
        print(f"Evaluation model: {EVAL_MODEL_ID}")
    print()

    results = []
    start_time = time.time()

    for i, tc in enumerate(test_cases, 1):
        print(f"[{i}/{len(test_cases)}] {tc['id']}: {tc['name']}")
        session_id = str(uuid.uuid4())

        # Invoke agent
        try:
            agent_response, conversation_log = invoke_agent(
                tc["query"], session_id, tc.get("follow_up_answers", {})
            )
        except Exception as e:
            agent_response = f"AGENT_ERROR: {str(e)}"
            conversation_log = []
            print(f"    ERROR: {e}")

        # Evaluate
        if args.skip_eval:
            evaluation = {"score": -1, "explanation": "Evaluation skipped", "criteria_results": {}}
        else:
            print("    Evaluating with LLM...")
            evaluation = evaluate_with_llm(tc, agent_response)
            print(f"    Score: {evaluation.get('score', '?')}/5 -- {evaluation.get('explanation', '')[:80]}")

        result = {
            "test_id": tc["id"],
            "name": tc["name"],
            "query": tc["query"],
            "agent_response": agent_response,
            "conversation_log": conversation_log,
            "expected": tc["expected"],
            "evaluation": evaluation,
        }
        results.append(result)

        # Sleep between calls to avoid throttling
        if i < len(test_cases):
            time.sleep(SLEEP_BETWEEN)

    elapsed = time.time() - start_time

    # Print summary
    if not args.skip_eval:
        print_summary_table(results)

    print(f"\nTotal time: {elapsed:.1f}s")

    # Save detailed results
    output = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": AGENT_ID,
        "agent_alias_id": AGENT_ALIAS_ID,
        "eval_model": EVAL_MODEL_ID if not args.skip_eval else "skipped",
        "total_time_seconds": round(elapsed, 1),
        "results": results,
        "summary": {
            "total": len(results),
            "avg_score": round(
                sum(r["evaluation"]["score"] for r in results) / len(results), 1
            ) if not args.skip_eval else None,
            "passed": sum(1 for r in results if r["evaluation"]["score"] >= 4) if not args.skip_eval else None,
            "failed": sum(1 for r in results if r["evaluation"]["score"] < 4) if not args.skip_eval else None,
        },
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Detailed results saved to {args.output}")


if __name__ == "__main__":
    main()
