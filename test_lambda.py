# -*- coding: utf-8 -*-
"""
Test harness for the herbicide recommendation Lambda function.

Usage:
  Local dry-run (prints JSON events only):
    python test_lambda.py --dry-run

  Invoke against deployed Lambda:
    python test_lambda.py --function-name <your-lambda-name> [--region us-east-1]

  Invoke locally (imports the handler directly — requires S3/Bedrock access):
    python test_lambda.py --local
"""

import json, argparse, sys, copy
from datetime import datetime

# ── Base event template ──────────────────────────────────────────────────────

BASE_EVENT = {
    "actionGroup": "HerbicideRecommendation",
    "apiPath": "/recommend",
    "httpMethod": "POST",
    "sessionAttributes": {},
    "requestBody": {
        "content": {
            "application/json": {
                "properties": [
                    {"name": "weed_names",             "value": "[Convulvulus arvensis]"},
                    {"name": "application_timing",     "value": "post-emergence"},
                    {"name": "next_crop",              "value": "ajo"},
                    {"name": "taboo_products",         "value": "[]"},
                    {"name": "location",               "value": "galicia"},
                    {"name": "location_group_num",     "value": 1},
                    {"name": "development_stage",      "value": "less than three leaves"},
                    {"name": "soil_type",              "value": "sandy"},
                    {"name": "weed_pressure_level",    "value": "high"},
                    {"name": "follow_up_treatment",    "value": "false"},
                    {"name": "adengo_was_applied",     "value": "false"},
                    {"name": "previously_applied_products", "value": "[]"},
                ]
            }
        }
    }
}


def build_event(overrides: dict) -> dict:
    """
    Deep-copy BASE_EVENT and override specific parameter values.

    overrides: dict mapping parameter name -> value.
               Use None to remove a parameter entirely.
    """
    event = copy.deepcopy(BASE_EVENT)
    props = event["requestBody"]["content"]["application/json"]["properties"]

    # Remove params set to None
    remove_names = {k for k, v in overrides.items() if v is None}
    props[:] = [p for p in props if p["name"] not in remove_names]

    # Update existing or add new params
    existing_names = {p["name"] for p in props}
    for key, val in overrides.items():
        if val is None:
            continue
        if key in existing_names:
            for p in props:
                if p["name"] == key:
                    p["value"] = val
                    break
        else:
            props.append({"name": key, "value": val})

    return event


# ── Test case definitions ────────────────────────────────────────────────────
# Each entry: (test_id, description, overrides_dict, expected_behavior)

TEST_CASES = [
    # ─── Validation / early-exit tests ───────────────────────────────────
    ("VAL-01", "Missing application_timing → ask user",
     {"application_timing": None},
     "Should return message asking for pre/post-emergence"),

    ("VAL-02", "Missing location → ask user",
     {"location": None},
     "Should return message asking for province"),

    ("VAL-03", "Missing location_group_num → ask LLM to classify",
     {"location_group_num": None},
     "Should return message asking to classify location"),

    ("VAL-04", "Missing next_crop → ask user",
     {"next_crop": None},
     "Should return message asking for next crop"),

    ("VAL-05", "Missing weed_names → ask user",
     {"weed_names": None},
     "Should return message asking for weed names"),

    ("VAL-06", "Group 2 + post-emergence + missing stage → ask user",
     {"location_group_num": 2, "application_timing": "post-emergence",
      "development_stage": None},
     "Should ask for weed development stage"),

    ("VAL-07", "Follow-up treatment but no previously applied products",
     {"follow_up_treatment": "true", "previously_applied_products": "[]"},
     "Should ask for previously applied products"),

    # ─── Path A: Standard table lookup ───────────────────────────────────
    ("A-01", "Group 1, low pressure, common weed, pre-emergence",
     {"location_group_num": 1, "weed_names": "[Convulvulus arvensis]",
      "application_timing": "pre-emergence", "weed_pressure_level": "low",
      "location": "Castilla y Leon", "next_crop": "trigo"},
     "Path A: standard lookup, Group 1"),

    ("A-02", "Group 1, low pressure, common weed, post-emergence",
     {"location_group_num": 1, "weed_names": "[Convulvulus arvensis]",
      "application_timing": "post-emergence", "weed_pressure_level": "low",
      "location": "Castilla y Leon", "next_crop": "cebada"},
     "Path A: standard lookup, Group 1, post"),

    ("A-03", "Group 1, two weeds, pre-emergence",
     {"location_group_num": 1, "weed_names": "[Convulvulus arvensis, Chenopodium album]",
      "application_timing": "pre-emergence", "weed_pressure_level": "low",
      "location": "Castilla y Leon", "next_crop": "trigo"},
     "Path A: two-weed combo"),

    ("A-04", "Group 2, pre-emergence → low dose",
     {"location_group_num": 2, "application_timing": "pre-emergence",
      "location": "Sevilla", "weed_names": "[Chenopodium album]",
      "next_crop": "trigo"},
     "Path A: Group 2 pre, dose=low"),

    ("A-05", "Group 2, post-emergence, less than 3 leaves → low dose",
     {"location_group_num": 2, "application_timing": "post-emergence",
      "development_stage": "less than three leaves",
      "location": "Sevilla", "weed_names": "[Chenopodium album]",
      "next_crop": "trigo"},
     "Path A: Group 2 post early stage, dose=low"),

    ("A-06", "Group 2, post-emergence, 3 leaves or more → high dose",
     {"location_group_num": 2, "application_timing": "post-emergence",
      "development_stage": "three leaves or more",
      "location": "Sevilla", "weed_names": "[Chenopodium album]",
      "next_crop": "trigo"},
     "Path A: Group 2 post late stage, dose=high"),

    ("A-07", "Group 3, non-palmeri, pre-emergence, sandy → medium dose",
     {"location_group_num": 3, "application_timing": "pre-emergence",
      "soil_type": "sandy", "weed_names": "[Chenopodium album]",
      "location": "Huesca", "next_crop": "trigo",
      "weed_pressure_level": "low"},
     "Path A: Group 3 pre sandy, dose=medium"),

    ("A-08", "Group 3, non-palmeri, pre-emergence, not sandy → high dose",
     {"location_group_num": 3, "application_timing": "pre-emergence",
      "soil_type": "not sandy", "weed_names": "[Chenopodium album]",
      "location": "Huesca", "next_crop": "trigo",
      "weed_pressure_level": "low"},
     "Path A: Group 3 pre non-sandy, dose=high"),

    ("A-09", "Group 3, non-palmeri, post-emergence → high dose",
     {"location_group_num": 3, "application_timing": "post-emergence",
      "weed_names": "[Chenopodium album]",
      "location": "Huesca", "next_crop": "trigo",
      "weed_pressure_level": "low"},
     "Path A: Group 3 post, dose=high"),

    # ─── Path A: taboo / Adengo filtering ────────────────────────────────
    ("A-10", "Taboo product filtering",
     {"location_group_num": 1, "weed_names": "[Convulvulus arvensis]",
      "application_timing": "pre-emergence", "weed_pressure_level": "low",
      "location": "Castilla y Leon", "next_crop": "trigo",
      "taboo_products": "[Laudis WG, Monsoon]"},
     "Path A: should exclude Laudis WG and Monsoon from results"),

    ("A-11", "Adengo previously applied → block Adengo/Spade Flexx/Monsoon",
     {"location_group_num": 1, "weed_names": "[Convulvulus arvensis]",
      "application_timing": "pre-emergence", "weed_pressure_level": "low",
      "location": "Castilla y Leon", "next_crop": "trigo",
      "adengo_was_applied": "true",
      "previously_applied_products": "[Adengo]"},
     "Path A: Adengo cross-resistance filtering"),

    # ─── Path B: Amaranthus palmeri (Group 3) ────────────────────────────
    ("B-01", "A. palmeri, Group 3, high pressure",
     {"location_group_num": 3, "weed_names": "[Amaranthus palmeri]",
      "application_timing": "pre-emergence",
      "weed_pressure_level": "high",
      "location": "Huesca", "next_crop": "trigo"},
     "Path B: consecutive pre+post hardcoded scheme"),

    ("B-02", "A. palmeri, Group 3, low pressure",
     {"location_group_num": 3, "weed_names": "[Amaranthus palmeri]",
      "application_timing": "pre-emergence",
      "weed_pressure_level": "low",
      "location": "Huesca", "next_crop": "trigo"},
     "Path B: pre-only scheme, post insufficient"),

    ("B-03", "A. palmeri + 2nd weed, Group 3, high pressure",
     {"location_group_num": 3, "weed_names": "[Amaranthus palmeri, Chenopodium album]",
      "application_timing": "pre-emergence",
      "weed_pressure_level": "high",
      "location": "Huesca", "next_crop": "cebada"},
     "Path B: two weeds including palmeri"),

    # ─── Path C: Cyperus (Group 1) ──────────────────────────────────────
    ("C-01", "Cyperus rotundus, Group 1, high pressure",
     {"location_group_num": 1, "weed_names": "[Cyperus rotundus]",
      "application_timing": "pre-emergence",
      "weed_pressure_level": "high",
      "location": "Castilla y Leon", "next_crop": "trigo"},
     "Path C: consecutive pre+post Cyperus scheme"),

    ("C-02", "Cyperus esculentus, Group 1, low pressure, pre-emergence",
     {"location_group_num": 1, "weed_names": "[Cyperus esculentus]",
      "application_timing": "pre-emergence",
      "weed_pressure_level": "low",
      "location": "Castilla y Leon", "next_crop": "trigo"},
     "Path C: low pressure pre scheme"),

    ("C-03", "Cyperus rotundus, Group 1, low pressure, post-emergence",
     {"location_group_num": 1, "weed_names": "[Cyperus rotundus]",
      "application_timing": "post-emergence",
      "weed_pressure_level": "low",
      "location": "Castilla y Leon", "next_crop": "trigo"},
     "Path C: low pressure post scheme"),

    # ─── Path D: Setaria (Group 1, high pressure) ───────────────────────
    ("D-01", "Setaria verticilata, Group 1, high pressure",
     {"location_group_num": 1, "weed_names": "[Setaria verticilata]",
      "application_timing": "pre-emergence",
      "weed_pressure_level": "high",
      "location": "Castilla y Leon", "next_crop": "trigo"},
     "Path D: consecutive Setaria scheme"),

    ("D-02", "Setaria viridis, Group 1, high pressure",
     {"location_group_num": 1, "weed_names": "[Setaria viridis]",
      "application_timing": "post-emergence",
      "weed_pressure_level": "high",
      "location": "Castilla y Leon", "next_crop": "cebada"},
     "Path D: Setaria viridis variant"),

    # ─── Edge cases ─────────────────────────────────────────────────────
    ("E-01", "Unrecognized weed name → suggestion list",
     {"weed_names": "[mala hierba desconocida]",
      "location_group_num": 1, "application_timing": "pre-emergence",
      "weed_pressure_level": "low", "location": "galicia", "next_crop": "trigo"},
     "Should return 'Did you mean...' suggestions"),

    ("E-02", "Unrecognized crop name",
     {"next_crop": "kiwi espacial",
      "location_group_num": 1, "weed_names": "[Convulvulus arvensis]",
      "application_timing": "pre-emergence", "weed_pressure_level": "low",
      "location": "galicia"},
     "Should return crop-not-in-database error"),

    ("E-03", "Spanish weed common name input",
     {"weed_names": "[correhuela]",
      "location_group_num": 1, "application_timing": "pre-emergence",
      "weed_pressure_level": "low", "location": "galicia", "next_crop": "trigo"},
     "Should resolve Spanish name via embeddings"),

    ("E-04", "Group 3, non-palmeri, pre-emergence, missing soil_type → ask",
     {"location_group_num": 3, "application_timing": "pre-emergence",
      "weed_names": "[Chenopodium album]", "location": "Huesca",
      "next_crop": "trigo", "soil_type": None, "weed_pressure_level": "low"},
     "Should ask for soil type"),

    ("E-05", "Setaria Group 1, missing pressure → ask",
     {"location_group_num": 1, "weed_names": "[Setaria verticilata]",
      "application_timing": "pre-emergence", "location": "Castilla y Leon",
      "next_crop": "trigo", "weed_pressure_level": None},
     "Should ask for weed pressure level"),

    ("E-06", "Cyperus Group 1, missing pressure → ask",
     {"location_group_num": 1, "weed_names": "[Cyperus rotundus]",
      "application_timing": "pre-emergence", "location": "Castilla y Leon",
      "next_crop": "trigo", "weed_pressure_level": None},
     "Should ask for weed pressure level"),

    ("E-07", "A. palmeri Group 3, missing pressure → ask",
     {"location_group_num": 3, "weed_names": "[Amaranthus palmeri]",
      "application_timing": "pre-emergence", "location": "Huesca",
      "next_crop": "trigo", "weed_pressure_level": None},
     "Should ask for weed pressure level"),
]


# ── Execution modes ──────────────────────────────────────────────────────────

def run_dry(cases):
    """Print each test event as JSON (no invocation)."""
    for test_id, desc, overrides, expected in cases:
        event = build_event(overrides)
        print(f"\n{'='*80}")
        print(f"TEST {test_id}: {desc}")
        print(f"EXPECTED: {expected}")
        print(f"{'='*80}")
        print(json.dumps(event, indent=2, ensure_ascii=False))


def run_local(cases):
    """Import the handler and invoke it directly."""
    # Adjust the module name if your file is named differently
    import importlib
    mod = importlib.import_module("get-herbicides-v3-fr94o")
    handler = mod.lambda_handler

    results = []
    for test_id, desc, overrides, expected in cases:
        event = build_event(overrides)
        print(f"\n{'='*80}")
        print(f"TEST {test_id}: {desc}")
        print(f"EXPECTED: {expected}")
        print(f"{'-'*80}")
        try:
            resp = handler(event, None)
            body = resp.get("response", {}).get("responseBody", {}).get("application/json", {}).get("body", "")
            # body may be double-encoded JSON string
            try:
                body = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                pass
            status = resp.get("response", {}).get("httpStatusCode", "?")
            print(f"STATUS: {status}")
            print(f"BODY:   {json.dumps(body, indent=2, ensure_ascii=False)}")
            results.append((test_id, "OK", status, body))
        except Exception as e:
            print(f"ERROR:  {type(e).__name__}: {e}")
            results.append((test_id, "EXCEPTION", type(e).__name__, str(e)))

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    passed = sum(1 for r in results if r[1] == "OK")
    failed = sum(1 for r in results if r[1] == "EXCEPTION")
    print(f"  Total: {len(results)}  |  OK: {passed}  |  Exceptions: {failed}")
    if failed:
        print("\n  Failed tests:")
        for r in results:
            if r[1] == "EXCEPTION":
                print(f"    {r[0]}: {r[2]}: {r[3]}")


def run_lambda(function_name, region, cases):
    """Invoke the deployed Lambda function via boto3."""
    import boto3
    client = boto3.client("lambda", region_name=region)

    results = []
    for test_id, desc, overrides, expected in cases:
        event = build_event(overrides)
        print(f"\n{'='*80}")
        print(f"TEST {test_id}: {desc}")
        print(f"EXPECTED: {expected}")
        print(f"{'-'*80}")
        try:
            resp = client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(event),
            )
            payload = json.loads(resp["Payload"].read())
            body = payload.get("response", {}).get("responseBody", {}).get("application/json", {}).get("body", "")
            try:
                body = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                pass
            status = payload.get("response", {}).get("httpStatusCode", "?")
            func_error = resp.get("FunctionError")

            if func_error:
                print(f"LAMBDA ERROR: {func_error}")
                print(f"BODY: {json.dumps(payload, indent=2, ensure_ascii=False)}")
                results.append((test_id, "LAMBDA_ERROR", func_error, payload))
            else:
                print(f"STATUS: {status}")
                print(f"BODY:   {json.dumps(body, indent=2, ensure_ascii=False)}")
                results.append((test_id, "OK", status, body))
        except Exception as e:
            print(f"ERROR:  {type(e).__name__}: {e}")
            results.append((test_id, "EXCEPTION", type(e).__name__, str(e)))

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    ok = sum(1 for r in results if r[1] == "OK")
    errs = sum(1 for r in results if r[1] != "OK")
    print(f"  Total: {len(results)}  |  OK: {ok}  |  Errors: {errs}")
    if errs:
        print("\n  Failed tests:")
        for r in results:
            if r[1] != "OK":
                print(f"    {r[0]}: {r[1]} — {r[2]}: {r[3]}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Test harness for herbicide Lambda")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                       help="Print test events as JSON without invoking")
    mode.add_argument("--local", action="store_true",
                       help="Import and call lambda_handler directly")
    mode.add_argument("--function-name", type=str,
                       help="Invoke deployed Lambda by name/ARN")

    parser.add_argument("--region", default="us-east-1",
                         help="AWS region (default: us-east-1)")
    parser.add_argument("--filter", type=str, default=None,
                         help="Run only tests whose ID starts with this prefix (e.g. 'B' or 'VAL')")

    args = parser.parse_args()

    cases = TEST_CASES
    if args.filter:
        prefix = args.filter.upper()
        cases = [c for c in cases if c[0].upper().startswith(prefix)]
        if not cases:
            print(f"No test cases match filter '{args.filter}'")
            sys.exit(1)

    print(f"Running {len(cases)} test case(s)...\n")

    if args.dry_run:
        run_dry(cases)
    elif args.local:
        run_local(cases)
    elif args.function_name:
        run_lambda(args.function_name, args.region, cases)


if __name__ == "__main__":
    main()
