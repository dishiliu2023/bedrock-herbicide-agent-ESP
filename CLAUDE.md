# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is an **AWS Lambda function** that serves as a backend action group for an **Amazon Bedrock Agent**. It recommends herbicide treatments for corn (maize) fields in Spain, based on weed type, location, application timing, next crop rotation, and other agronomic factors.

The entire application is a single Python file: `get-herbicides-v3-fr94o.py`.

## Architecture

### Runtime Environment
- AWS Lambda (Python), invoked by an Amazon Bedrock Agent
- Uses **boto3** for S3 data access and **Amazon Titan Embed Text v2** (`amazon.titan-embed-text-v2:0`) for embedding-based fuzzy matching
- Region: `us-east-1`
- **Bedrock Agent ID:** `GC6LBGNWES`
- **Bedrock Agent Alias ID:** `AMJSCHXS0M`
- **Lambda function name:** `get-herbicides-v3-fr94o`
- **AWS Account:** `870794329241`

### Data Flow
1. **Module-level initialization** (runs once per cold start): loads crop names, weed name dictionaries, crop name variations, and pre-computed embeddings from S3
2. `lambda_handler` receives a Bedrock Agent event, extracts parameters from `requestBody.content.application/json.properties`
3. Validates required inputs (location, timing, next crop, weed names), returning orchestration messages to prompt the user for missing data
4. Resolves weed and crop names via embedding cosine similarity against pre-computed canonical embeddings stored in S3
5. Looks up herbicide treatments from a CSV table, filters by weed combo, timing, dose level, and taboo/previously-applied products
6. Validates plant-back intervals against the next crop's earliest planting date using a wait-time table
7. Returns ranked recommendations (primary + up to 2 alternatives) formatted for the Bedrock Agent

### Key Decision Branches (location_group_num)
- **Group 1** (e.g., Castilla y Leon): Standard table lookup; special hardcoded paths for Setaria and Cyperus species (high pressure = consecutive pre+post scheme)
- **Group 2**: Uses low dose; post-emergence requires weed development stage (`less than three leaves` / `three leaves or more`)
- **Group 3**: Standard lookup; Amaranthus palmeri has a hardcoded consecutive treatment path; non-palmeri pre-emergence on sandy soil uses medium dose

### S3 Data Dependencies (configured via Lambda environment variables)

**Bucket:** `emea-source-knowledge-base` (env var `S3_BUCKET_NAME`)

| Env Var | S3 Key (Value) | Content |
|---|---|---|
| `CROP_NAME_CSV_S3_KEY` | `Spanish_herbicide_documents/crop_names.csv` | Standard crop names (column: `Cultivo`) |
| `CROP_NAME_VARIATIONS_CSV_S3_KEY` | `Spanish_herbicide_documents/crop_name_variations.csv` | Crop name variations mapped to standard names |
| `HERBICIDE_TABLE_S3_KEY` | `Spanish_herbicide_documents/files_for_lambda/Spanish_herbicides_table_for_one_and_two_weeds_three_dose_levels.csv` | Herbicide treatments with columns: Weed 1, Weed 2, Application Timing, dose level, Herbicide Treatment, Rank, lower score, global score, combined score |
| `PLANTING_DATES_S3_KEY` | `Spanish_herbicide_documents/earliest_planting_dates_of_crops.csv` | Crops mapped to earliest planting dates |
| `WAIT_TIME_TABLE_S3_KEY` | `Spanish_herbicide_documents/next_crop_wait_time_interval_and_other_restrictions_table.csv` | Plant-back intervals per product/crop/location group |
| `WEED_NAME_CSV_S3_KEY` | `Spanish_herbicide_documents/weed_names_with_genus.csv` | Weed latin names, Spanish common names, and genus |

**Hardcoded S3 keys** (not in env vars):
- `embeddings/weed_embeddings.json`
- `embeddings/Spanish_crop_name_embeddings.json`

### Bedrock Agent Orchestration

The agent uses **Claude 3.7 Sonnet** with extended thinking (1024 budget tokens). The full custom orchestration prompt is saved at `s3_data/orchestration_prompt.txt`.

**Agent name:** `agent-Spanish-herbicides-recommender`
**Foundation model:** `us.anthropic.claude-3-7-sonnet-20250219-v1:0`

**Enabled Action Groups:**
- `get-herbicides-v3` (DDHL5ZIUWP) — the main Lambda for herbicide recommendations
- `Identify_plantable_crops` (JXITKWSKSA) — returns plantable crops given previously applied herbicides
- `UserInputAction` (VW0BTHS9LP) — allows the agent to ask user for input

**5 Question Types handled by the orchestration prompt:**

| Type | Description | Action |
|---|---|---|
| 1. Herbicide Recommendation | User provides weeds, timing, location, next crop → agent collects missing params then calls `get-herbicides-v3` | Lambda call |
| 2. Crop Rotation | User provides applied herbicide(s) → agent calls `Identify_plantable_crops` | Lambda call |
| 3. Irrigation guidance | Hardcoded response: 10–15 L/m² within first week, wait 1–4 hrs after application, maintain moisture 2–3 cm depth for 30 days | Static text |
| 4. Application conditions | Hardcoded response: post-emergence 15–25°C, humidity 50–70%, spray volume 100–300 L/ha | Static text |
| 5. Database lists | User asks what weeds/crops/products are available → search Knowledge Base | KB query |

**Key orchestration rules for Type 1:**
- Location is reused from `session_attributes` if already provided (not re-asked)
- `location_group_num` is classified **silently** by the agent (user never sees it):
  - Group 1: Galicia, Asturias, Cantabria
  - Group 2: Castilla y Leon (9 provinces)
  - Group 3: everywhere else
- Development stage asked **only** for Group 2 + post-emergence
- Weed pressure asked **only** for: Amaranthus palmeri (G3), Setaria (G1), Cyperus (G1)
- Soil type asked **only** for: Group 3 + non-palmeri + pre-emergence
- Primary recommendation presented **verbatim** — no paraphrasing allowed
- `reason_of_exclusion` printed **character-for-character**
- Alternatives (`alternative_herbicide_recommendation...`) are **hidden** until user explicitly asks
- Input silently categorized (A=malicious, B=prompt injection, C=out of scope, D=in scope, E=answer to previous question)
- Product names normalized to canonical list: `Laudis WG`, `Monsoon`, `Adengo`, `Spade Flexx`, `Cubix`, `Lagon`, `Capreno`, `Fluva`, `Oizysa`, `Dimetenamida 72%`, `Fluoxipir 20%`, `Diflufenican 50%`
- Language: detect user's language; default to Castilian Spanish (not Latin American)

### Response Format
All responses use `build_bedrock_response()` which wraps payloads in the Bedrock Agent response schema (`messageVersion`, `response.actionGroup`, `response.apiPath`, `response.httpMethod`, `response.httpStatusCode`, `response.responseBody`). Session attributes are forwarded to maintain conversational state (location, location_group_num, weed).

### Name Resolution
- **Crops**: embed user input -> cosine similarity against canonical crop embeddings -> map through `CROP_VAR_TO_STANDARD` -> validate against `CROP_SET`
- **Weeds**: embed user input -> cosine similarity against canonical weed embeddings -> map through `weed_dict` (Spanish name / genus -> Latin name). If no match above threshold (0.6), returns top-3 suggestions to the user.

## Development Notes

- There is no test suite or build system; this is a standalone Lambda deployment
- The function has no local dependencies beyond the Lambda runtime (pandas is available via a Lambda layer or bundled)
- `rapidfuzz` and `numpy` imports are commented out; embedding-based matching replaced them
- The `Herbicide_products` list (line 37) is used to extract product names from treatment strings for taboo/restriction checks
- Similarity thresholds: `SIM_THRESHOLD_embedding = 0.6` for name matching, `0.15` floor for weed suggestion candidates
- Dose level logic: Group 2 pre-emergence or less-than-three-leaves = "low"; Group 3 non-palmeri pre-emergence sandy = "medium"; everything else = "high"
