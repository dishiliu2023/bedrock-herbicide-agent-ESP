# -*- coding: utf-8 -*-
import os, csv, boto3, json, math
import pandas as pd
#from docx import Document
import io
from datetime import datetime
import numbers   # import numpy as np

#from rapidfuzz import process, fuzz          # pip install rapidfuzz
SIM_THRESHOLD = 80                           # ≥90 % similarity counts as “very close”
SIM_THRESHOLD_embedding = 0.6                # for cosine similarity of embeddings


s3 = boto3.client('s3')
bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

# These should be set as environment variables in your Lambda function
HERBICIDE_TABLE_S3_KEY = os.environ['HERBICIDE_TABLE_S3_KEY']
PLANTING_DATES_S3_KEY = os.environ['PLANTING_DATES_S3_KEY']
WAIT_TIME_TABLE_S3_KEY = os.environ['WAIT_TIME_TABLE_S3_KEY']
S3_BUCKET_NAME = os.environ['S3_BUCKET_NAME']
WEED_NAME_CSV_S3_KEY = os.environ['WEED_NAME_CSV_S3_KEY']
CROP_NAME_CSV_S3_KEY = os.environ['CROP_NAME_CSV_S3_KEY']
CROP_NAME_VARIATIONS_CSV_S3_KEY = os.environ['CROP_NAME_VARIATIONS_CSV_S3_KEY']

WEED_EMBEDDING_KEY =  "embeddings/weed_embeddings.json"
CROP_EMBEDDING_KEY =  "embeddings/Spanish_crop_name_embeddings.json"

EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"  # adjust if needed

Herbicide_products = [
    "Laudis WG",
    "Monsoon",
    "Adengo",
    "Spade Flexx",
    "Cubix",
    "Lagon",
    "Capreno",
    "Fluva",
    "Oizysa",
    "Dimetenamida 72%",
    "Fluoxipir 20%",
    "Diflufenican 50%"
]

raw_crop       = s3.get_object(Bucket=S3_BUCKET_NAME, Key=CROP_NAME_CSV_S3_KEY)["Body"].read().decode("utf-8-sig")
reader_crop    = csv.DictReader(raw_crop.splitlines())   # header row: Cultivo
CROP_SET  = {row["Cultivo"].strip().lower() for row in reader_crop}
#print(CROP_SET)
raw_weed       = s3.get_object(Bucket=S3_BUCKET_NAME, Key=WEED_NAME_CSV_S3_KEY)["Body"].read().decode("utf-8-sig")
reader_weed    = csv.DictReader(raw_weed.splitlines())
#WEED_SET  = {row["Weed latin name"].strip().lower() for row in reader_weed }
#print(WEED_SET)
###################################################################################

# Initialize dictionary

weed_dict = {}

for row in reader_weed:   
    latin_name = row["Weed latin name"].strip()
    spanish_names = [name.strip() for name in row["Common Spanish name of weed"].split(",")]    
    
    # Map Latin name to itself
    weed_dict[latin_name] = latin_name    
    
    # Map each Spanish name to the Latin name
    for name in spanish_names:
        weed_dict[name] = latin_name
    
    # Map genus to Latin name if it's not empty/NaN
    genus_value = row["genus"]
    if genus_value and str(genus_value).strip() and str(genus_value).lower() != 'nan':
        genus = str(genus_value).strip()
        weed_dict[genus] = latin_name

#print(weed_dict)
####################################################################################

raw_crop_var   = s3.get_object(Bucket=S3_BUCKET_NAME, Key=CROP_NAME_VARIATIONS_CSV_S3_KEY)["Body"].read().decode("latin-1")
# Convert to dictionary
df = pd.read_csv(io.StringIO(raw_crop_var))
CROP_VAR_TO_STANDARD = dict(zip(df['variation'], df['standard_name']))

def load_canonical(EMBEDDING_KEY):
    resp = s3.get_object(Bucket=S3_BUCKET_NAME, Key=EMBEDDING_KEY)
    canonical = json.loads(resp["Body"].read())
    return canonical

def embed(text: str):
    body = json.dumps({"inputText": text})
    resp = bedrock.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=body
    )
    payload = json.loads(resp["body"].read())
    return payload["embedding"]

def cosine_similarity(a, b):
    # assumes len(a) == len(b)
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))

#def find_best_matches(query_vec, canonical_items, top_k=3):
#    scored = []
#    for item in canonical_items:
#        sim = cosine_similarity(query_vec, item["vector"])
#        scored.append((sim, item["name"]))
#    scored.sort(reverse=True, key=lambda x: x[0])
#    return scored[:top_k]

def find_best_matches(query_vec, canonical_items, top_k=3, sim_threshold=0.0):
    """
    Find the top_k best matches for a query vector from canonical_items,
    filtering out items with similarity below sim_threshold.

    Parameters:
        query_vec: The query embedding vector.
        canonical_items: List of dicts with keys 'vector' and 'name'.
        top_k: Number of top matches to return.
        sim_threshold: Minimum similarity score to include in results.

    Returns:
        List of tuples (similarity_score, item_name) for matches above threshold.
    """
    scored = []
    for item in canonical_items:
        sim = cosine_similarity(query_vec, item["vector"])
        if sim >= sim_threshold:
            scored.append((sim, item["name"]))
    scored.sort(reverse=True, key=lambda x: x[0])
    return scored[:top_k]

weed_canonical_items = load_canonical(WEED_EMBEDDING_KEY)
crop_canonical_items = load_canonical(CROP_EMBEDDING_KEY)

# Load S3 data tables at module level so they persist across warm Lambda invocations
herbicide_obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=HERBICIDE_TABLE_S3_KEY)
herbicide_df = pd.read_csv(io.BytesIO(herbicide_obj['Body'].read()), encoding='utf-8', sep=',')

planting_obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=PLANTING_DATES_S3_KEY)
planting_df = pd.read_csv(io.BytesIO(planting_obj['Body'].read()), encoding='utf-8', sep=',')

wait_time_obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=WAIT_TIME_TABLE_S3_KEY)
wait_time_df = pd.read_csv(io.BytesIO(wait_time_obj['Body'].read()), encoding='utf-8', sep=',')

def resolve_weed_name(weed_input, weed_canonical_items, weed_dict, event, updated_session_attributes=None):
    """
    Resolve a user-provided weed name to its canonical Latin name via embeddings.

    Returns:
        (resolved_name, None) on success.
        (None, error_response) if the weed cannot be resolved.
    """
    q_vec = embed(weed_input)
    best = find_best_matches(q_vec, weed_canonical_items, top_k=3, sim_threshold=0.15)

    if not best:
        error_response = build_bedrock_response(event, 200,
            {"error": f'Weed "{weed_input}" was not found in the database, and no similar matches were found.'},
            updated_session_attributes)
        return None, error_response

    score, best_match = best[0]
    if score >= SIM_THRESHOLD_embedding:
        resolved = weed_dict.get(best_match, weed_input)
        print(resolved)
        return resolved, None
    else:
        closest = [item[1] for item in best]
        suggestion_list = [f"{i+1}: {name}" for i, name in enumerate(closest)]
        suggestion_str = ", ".join(suggestion_list)
        error_response = build_bedrock_response(event, 200, {
            "message_to_agent_orchestration": f"Weed '{weed_input}' has no exact match in the database. Do you mean {suggestion_str}, or any other weed?"
        }, updated_session_attributes)
        return None, error_response


def check_wait_times(product_names, standardized_crop, location_group_num, interval, additional_text, next_crop):
    """
    Check plant-back wait times for a list of products against the next crop.

    Returns:
        (updated_additional_text, all_valid) where all_valid is True if all
        products meet the planting interval requirement.
    """
    wait_months_list = []
    other_restrictions_list = []

    for product in product_names:
        match = wait_time_df[
            (wait_time_df['Herbicide product'].str.lower() == product.lower()) &
            (wait_time_df['Next crop'].str.lower() == standardized_crop.lower()) &
            (wait_time_df['location group'] == location_group_num)
        ]

        if not match.empty:
            wait_months = match['Shortest time interval'].values[0]
            wait_months_list.append(wait_months)

            other_restrictions = match['Restriction'].values[0]
            if not pd.isna(other_restrictions):
                other_restrictions_list.append(other_restrictions)
        else:
            wait_months_list.append(None)

    # Convert to native Python ints
    wait_months_list = [int(w) if isinstance(w, numbers.Integral) else w for w in wait_months_list]

    all_valid = all(wait is not None and interval >= wait for wait in wait_months_list)

    if all_valid:
        other_restrictions_list = list(set(other_restrictions_list))
        joined_restrictions = ' '.join(other_restrictions_list)
        additional_text = additional_text + ". With the agronomic requirement for the planting of the next crop:" + joined_restrictions
    else:
        additional_text = additional_text + f"\nPlease notice: You cannot plant {next_crop} next season due to herbicide residue restrictions, please consider planning for a different crop in the next rotation. "

    if "Monsoon" in product_names:
        additional_text += "\n\nWarning: Apply Monsoon only before the corn reaches the 6-leaf stage."

    return additional_text, all_valid


def build_bedrock_response(event, status_code, payload, updated_session_attributes=None):
    """
    Constructs a Bedrock Agent-compatible response.

    Parameters:
    - event (dict): The incoming event from the Bedrock Agent.
    - status_code (int): The HTTP status code to return.
    - payload (dict or str): The response body. If it's a dict, it will be JSON-encoded.

    Returns:
    - dict: A response formatted for Bedrock Agent.
    """
    if isinstance(payload, dict):
        payload = json.dumps(payload)

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup"),
            "apiPath": event.get("apiPath"),
            "httpMethod": event.get("httpMethod"),
            "httpStatusCode": status_code,
            "responseBody": {
                "application/json": {
                    "body": payload
                }
            }
        }, 
        "sessionAttributes": updated_session_attributes
    }


def lambda_handler(event, context):
    
    #print("EVENT:", json.dumps(event)) # Add this line to inspect the structure    
    #print(CROP_SET)
    
    # Extract the list of properties
    try:
        properties = event["requestBody"]["content"]["application/json"]["properties"]
    except KeyError:
        error_response = build_bedrock_response(event, 400, {"error": "Missing or malformed requestBody content." })
        print("error RESPONSE:", json.dumps(error_response))
        return error_response    

    session_attributes = event.get('sessionAttributes', {})
    print("session_attributes:", session_attributes)

    # Check session attributes for previously stored values
    stored_location                       = session_attributes.get('location')
    stored_location_group_num = session_attributes.get('location_group_num')
    stored_weed = session_attributes.get('weed')

    # Convert list of properties to a dictionary
    params = {prop["name"]: prop["value"] for prop in properties}   


    #print("params:", params)
    # Now safely access the values
    weed_latin = params.get("weed_names")
    timing = (params.get("application_timing") or "").lower()
    next_crop = params.get("next_crop")
    location = params.get("location")

    raw_val = params.get("location_group_num")
    try:
        location_group_num = int(raw_val) if raw_val is not None else None
    except ValueError:
        # Handle cases where raw_val is "abc", "", etc.
        location_group_num = None 

    follow_up_treatment = params.get("follow_up_treatment", False)
    previously_applied_products = params.get("previously_applied_products", "[]")

    taboo = params.get("taboo_products", "[]")

    stage = params.get("development_stage")
    weed_pressure_level = params.get("weed_pressure_level")
    soil_type = params.get("soil_type")

    # Check if this is a hardcoded path that doesn't need user-specified timing
    skip_timing_validation = False
    weed_lower = (weed_latin or "").lower()
    #############################################################################################
    # Cyperus G1 HIGH or Setaria G1 HIGH → consecutive pre+post
    if weed_pressure_level == "high" and location_group_num == 1:
        if any(w in weed_lower for w in ["cyperus rotundus", "cyperus esculentus",
                                          "setaria verticilata", "setaria viridis"]):
            skip_timing_validation = True
    
    # Amaranthus palmeri G3 (any pressure) → hardcoded path, timing irrelevant
    if location_group_num == 3 and "amaranthus palmeri" in weed_lower:
        skip_timing_validation = True
    
    if skip_timing_validation:
        timing = "consecutive"  # placeholder — hardcoded paths don't use this value
    elif "post" in timing:
        timing = "post-emergence"
    elif "pre" in timing:
        timing = "pre-emergence"
    else:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "A specific timing of application is required. Please ask the user to provide it. The question must be like: `Would you like to apply the herbicides pre-emergence or post-emergence of the corn crop?` Your question must be terse."}, session_attributes)
        return error_response      
    ##############################################################################################
    #print("weed_latin:", weed_latin)
    #print("location:", location)
    #print("location_group_num:", location_group_num)
    #print("stage:", stage)
    #print("weed_pressure_level:", weed_pressure_level)
    #print("soil_type:", soil_type)

    if isinstance(follow_up_treatment, str):  # sometime it is passed not as boolean but as string
        follow_up_treatment = follow_up_treatment.lower() == "true"

    #print("follow_up_treatment: ", follow_up_treatment )
    #print("previously_applied_products:", previously_applied_products, type(previously_applied_products))

    ##################################################################
    # Convert the string to a list
    # Remove the brackets and split the string
    taboo_list = taboo.strip("[]").split(", ")
    # Remove any surrounding quotes from each element
    taboo_list = [item.strip("'\"") for item in taboo_list]
    print("taboo_list:", taboo_list)

    previously_applied_products_list = previously_applied_products.strip("[]").split(", ")
    previously_applied_products_list = [item.strip("'\"") for item in previously_applied_products_list]

    taboo_list.extend(previously_applied_products_list)

    #print("previously_applied_products:", previously_applied_products_list, type(previously_applied_products_list))
    #######################################################################

    # Update session attributes with current values
    updated_session_attributes = session_attributes.copy()
    if location:
        updated_session_attributes['location'] = location
    if location_group_num != None:
        updated_session_attributes['location_group_num'] = location_group_num
    if weed_latin:
        updated_session_attributes['weed'] = weed_latin

    # Check if required variables are missing
    if follow_up_treatment and previously_applied_products_list[0]=='':
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "To recommend a follow-up treatment, please ask the user to specify the herbicide products that were already applied previously in the current season. Your question must be terse."}, updated_session_attributes)
        return error_response

    if location is None:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "Location is required. Please ask the user to provide the location. The question must be: `In which province is your corn field located?` Your question must be terse."}, updated_session_attributes)
        return error_response
    elif location_group_num  is None:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "location_group_num is required.  You have to determine the value of this integer variable by classifying the user-input location."}, updated_session_attributes)
        return error_response       

    #if location not in ["Castilla y Leon", "Rest of Spain"]:
    #    error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "location must be coerced to either `Castilla y Leon` or `Rest of Spain`. The LLM has to coerce the user input location geographically to these two options."}, updated_session_attributes)
    #    return error_response

    if next_crop is None:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "Next crop is required. Please ask the user to provide the next crop name. The question must be like: `What crop are you planning for the next season?` Your question must be terse."}, updated_session_attributes)
        return error_response

    if not weed_latin:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "Weed name is required. Please ask the user to provide the weed name(s) they want to control. Your question must be terse."}, updated_session_attributes)
        return error_response

    if location_group_num == 2 and timing == "post-emergence" and stage not in {"less than three leaves", "three leaves or more"}:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "Weed development stage is required for weed control in this case. Please ask the user to provide the weed development stage. You MUST ask: `What is the development stage of the weeds? Are they less than three leaves or three leaves or more?`. Your question must be terse and limited to the essential. The user's answer must be classified to either `less than three leaves` or `three leaves or more`."}, updated_session_attributes)
        return error_response

    if "amaranthus palmeri" in weed_latin.lower() and location_group_num == 3 and weed_pressure_level not in {"high", "low"}:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "Weed pressure level is required for weed control in this case. Please ask the user to provide the weed pressure level. The weed pressure level must be either `high` or `low`."}, updated_session_attributes)
        return error_response

    if "amaranthus palmeri" not in weed_latin.lower() and location_group_num == 3 and timing == "pre-emergence" and soil_type not in {"sandy", "not sandy"}:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "Soil type is required for weed control in this case. Please ask the user to provide the soil type. The soil type must be either `sandy` or `not sandy`."}, updated_session_attributes)
        return error_response

    if ("setaria verticilata" in weed_latin.lower() or "setaria viridis" in weed_latin.lower()) and location_group_num == 1 and weed_pressure_level not in {"high", "low"}:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "Weed pressure level is required for weed control in this case. Please ask the user to provide the weed pressure level. The weed pressure level must be either `high` or `low`."}, updated_session_attributes)
        return error_response       

    if ("cyperus rotundus" in weed_latin.lower() or "cyperus esculentus" in weed_latin.lower()) and location_group_num == 1 and weed_pressure_level not in {"high", "low"}:
        error_response = build_bedrock_response(event, 200, {"message_to_agent_orchestration": "Weed pressure level is required for weed control in this case. Please ask the user to provide the weed pressure level. The weed pressure level must be either `high` or `low`."}, updated_session_attributes)
        return error_response    

    if location_group_num == 2  and timing.lower() == "pre-emergence":
        dose = "low"
    elif location_group_num == 2  and stage.lower() == "less than three leaves":
        dose = "low"
    elif location_group_num == 3  and ("amaranthus palmeri" not in weed_latin.lower()) and timing.lower() == "pre-emergence" and soil_type.lower() =="sandy":
        dose = "medium"
    else:
        dose = "high"
    #print(dose)

    follow_up_suggestion = ""  # empty string to be filled conditionally 

    ###############################################################################################
    #if location =="Rest of Spain":
    #    location = "Spain excluding Castilla y Leon"  
    ###############################################################################################
    # Normalize and translate the crop name
    normalized_crop = next_crop.lower()

    q_vec = embed(normalized_crop)
    best = find_best_matches(q_vec, crop_canonical_items, top_k=3)
    #print("crop name best:", best)
    #print(CROP_VAR_TO_STANDARD)
    score_1, crop_best_match = best[0]
    #print("crop_best_match:", crop_best_match)
    #print("score_1:", score_1)

    if score_1 >= SIM_THRESHOLD_embedding:
        standardized_crop = CROP_VAR_TO_STANDARD.get(crop_best_match.lower(), crop_best_match.lower()).lower()  #  
        #print(standardized_crop)
    else:
        error_response = build_bedrock_response(event, 200, {"error": f"crop {next_crop} is not in my database."},updated_session_attributes)
        return error_response

    #print("CROP_SET", CROP_SET)
    if standardized_crop not in CROP_SET:
        print(f"Crop '{next_crop}' is not in the standard list.")
        error_response = build_bedrock_response(event, 200, {"error": "Crop not in the list in my database."}, updated_session_attributes)
        return error_response
    #################################################################################################


    #print("weed_latin:", weed_latin) 

    # Convert the string to a list
    # Remove the brackets and split the string
    weed_latin_list = weed_latin.strip("[]").split(", ")

    # Remove any surrounding quotes from each element
    weed_latin_list = [item.strip("'\"") for item in weed_latin_list]

    weed_1 = weed_latin_list[0]

    if len(weed_latin_list) >1:        
        weed_2 = weed_latin_list[1]
    else:
        weed_2 = "None"

    print("weed_1:", weed_1)
    print("weed_2:", weed_2)
    ###################################################################
    weed_1, err = resolve_weed_name(weed_1, weed_canonical_items, weed_dict, event, updated_session_attributes)
    if err:
        return err

    if len(weed_latin_list) > 1:
        weed_2, err = resolve_weed_name(weed_2, weed_canonical_items, weed_dict, event, updated_session_attributes)
        if err:
            return err
    ####################################################################

    ##############################################################################################
    # Get earliest planting date for the next crop
    planting_row = planting_df[planting_df['Crop'].str.lower() == standardized_crop.lower()]
    if planting_row.empty:
        #print(f"error: No planting date found for crop: {standardized_crop}")
        error_response = build_bedrock_response(event, 200, {"error": "Standardized_crop not in the list of crop planting dates." },updated_session_attributes)
        return error_response
        
    planting_date_ori = planting_row['Earliest Planting Date'].values[0]
    # Define the reference month (June)
    reference_month = datetime.strptime("June", "%B")

    # Handle cases where planting_date is in the next year
    if "next year" in planting_date_ori:
        planting_date = planting_date_ori.replace(" next year", "")
        planting_date = datetime.strptime(planting_date, "%B")
        planting_date = planting_date.replace(year=reference_month.year + 1)
    else:
        planting_date = datetime.strptime(planting_date_ori, "%B")

    # Calculate the interval in months
    interval = (planting_date.year - reference_month.year) * 12 + (planting_date.month - reference_month.month)
    ########################################################################################################################

    if (location_group_num == 1 and (weed_pressure_level == "low" or ( weed_1.lower() != 'setaria verticilata' and  weed_2.lower() != 'setaria verticilata'\
                                                                      and weed_1.lower() != 'setaria viridis' and  weed_2.lower() != 'setaria viridis'))\
                                and weed_1.lower() != 'cyperus rotundus'   and  weed_2.lower() != 'cyperus rotundus'\
                                and weed_1.lower() != 'cyperus esculentus' and  weed_2.lower() != 'cyperus esculentus') \
        or location_group_num == 2\
        or (location_group_num == 3 and weed_1.lower() != 'amaranthus palmeri' and  weed_2.lower() != 'amaranthus palmeri'):

        # Filter herbicide treatments
        if len(weed_latin_list) >1: 
            candidates = herbicide_df[
                (
                    ((herbicide_df['Weed 1'].str.lower() == weed_1.lower()) & (herbicide_df['Weed 2'].str.lower() == weed_2.lower())) |
                    ((herbicide_df['Weed 2'].str.lower() == weed_1.lower()) & (herbicide_df['Weed 1'].str.lower() == weed_2.lower()))
                ) &
                (herbicide_df['Application Timing'].str.lower() == timing.lower()) &        
                (herbicide_df['dose level'].str.lower() == dose.lower())
            ].sort_values(by='Rank')
        else:
            candidates = herbicide_df[
                ( (herbicide_df['Weed 1'].str.lower() == weed_1.lower()) & (herbicide_df['Weed 2'] == "PLACE HOLDER" )) &
                (herbicide_df['Application Timing'].str.lower() == timing.lower()) &
                (herbicide_df['dose level'].str.lower() == dose.lower())
            ].sort_values(by='Rank')

        #if weed_1 == 'amaranthus palmeri' and  weed_2 == "None":

        #print("candidates:", candidates['Herbicide Treatment', 'Rank','lower score', 'combined score' ])
        #print("all df:",herbicide_df)
        #print("candidates:", candidates)
        #print("dose level:", dose)

        if candidates.empty:
            if len(weed_latin_list) >1:
                if stage is not None:
                    pretext = f"For corn fields in {location}, to control weed {weed_1} and {weed_2} at stage {stage}, with {standardized_crop} to be planted in next season, "
                else:
                    pretext = f"For corn fields in {location}, to control weed {weed_1} and {weed_2}, with {standardized_crop} to be planted in next season, "
            else:
                if stage is not None:
                    pretext = f"For corn fields in {location}, to control weed {weed_1} at stage {stage}, with {standardized_crop} to be planted in next season, "
                else:
                    pretext = f"For corn fields in {location}, to control weed {weed_1}, with {standardized_crop} to be planted in next season, "

            payload = json.dumps({
                "primary_herbicide_recommendation_and_agronomic_requirement":  pretext + "there is no applicable treatments for the given application timing and target weeds."
            })         

            response = build_bedrock_response(event, 200, payload, updated_session_attributes)
            #print("RESPONSE:", json.dumps(response))
            return response

        ##########################################################################################
        all_valid = False
        
        # Create an empty DataFrame with the same columns as candidates plus 'restrictions'
        valid_candidates   = pd.DataFrame(columns=list(candidates.columns) + ['min_plant_back_interval_months', 'restrictions'])
        invalid_candidates = pd.DataFrame(columns=list(candidates.columns) + ['min_plant_back_interval_months', 'restrictions'])
        ############################################################################################
        
        for _, row in candidates.iterrows():
           
            treatment_ori = row['Herbicide Treatment']
            #print("treatment_ori:", treatment_ori)

            # Initialize an empty list to collect product names
            product_names = []

            # Iterate over each herbicide product and check if it appears in treatment_ori
            for product in Herbicide_products:
                if product in treatment_ori:
                    product_names.append(product)

            #print("product_names:", product_names)

            # Skip the row if any product is in the taboo list
            if any(product in product_names for product in taboo_list):
                continue

            wait_months_list = []
            other_restrictions_list = []

            for product in product_names:
                #print(product)
                match = wait_time_df[
                    (wait_time_df['Herbicide product'].str.lower() == product.lower()) &
                    (wait_time_df['Next crop'].str.lower() == standardized_crop.lower()) &
                    (wait_time_df['location group']  == location_group_num)
                ]
                
                if not match.empty:
                    wait_months = match['Shortest time interval'].values[0]
                    wait_months_list.append(wait_months)

                    other_restrictions = match['Restriction'].values[0]
                    if not pd.isna(other_restrictions):
                        other_restrictions_list.append(other_restrictions)
                    
                else:
                    wait_months_list.append(None)  # or handle it differently if needed
    
            # Convert wait_months_list to native Python ints
            wait_months_list = [int(w) if isinstance(w, numbers.Integral) else w for w in wait_months_list]

            # Check if all wait times are greater than or equal to the interval
            all_valid = all(wait is not None and interval >= wait for wait in wait_months_list)

            #print(all_valid)
            #print(interval, wait_months_list)

            #recommendation =  f"For corn fields, to control weed {weed_latin}, with next crop {standardized_crop} to be planted in {planting_date_ori} the recommendation is {treatment_ori}."
            # Pack the payload the way Bedrock wants it
            other_restrictions_list = list(set(other_restrictions_list)) # drop duplicates            
            joined_restrictions = ' '.join(other_restrictions_list)

            # Add the row to valid_candidates with the restrictions
            row_with_restrictions = row.copy()
            row_with_restrictions['restrictions'] = joined_restrictions
            row_with_restrictions['min_plant_back_interval_months'] = max((w for w in wait_months_list if w is not None), default=0)
            #print("max(wait_months_list):", max(wait_months_list))

            # Check if restriction text blocks this treatment
            if all_valid:
                valid_candidates = pd.concat([valid_candidates, pd.DataFrame([row_with_restrictions])], ignore_index=True)
            else:
                invalid_candidates = pd.concat([invalid_candidates, pd.DataFrame([row_with_restrictions])], ignore_index=True)

        #print("valid_candidates:", valid_candidates)
        if not valid_candidates.empty:
            # Keep the first row + all rows with lower score >= 3
            valid_candidates = pd.concat(
                [
                    valid_candidates.iloc[[0]],                 # always keep first row
                    valid_candidates.iloc[1:][valid_candidates.iloc[1:]["lower score"] >= 3]
                ],
                ignore_index=False
            )
            valid_candidates = valid_candidates.reset_index(drop=True)

        if not invalid_candidates.empty:                   
            invalid_candidates = invalid_candidates[invalid_candidates["lower score"] > 3].reset_index(drop=True)

##################################################################################################
        #if not invalid_candidates.empty:
        #    # Keep all row   with lower score >= 4
        #    invalid_candidates = invalid_candidates[invalid_candidates["lower score"] >= 4].reset_index(drop=True)     

        
        if len(valid_candidates) == 0:
            pretext = "There is no applicable treatments or no treatment meets the residue degradation standard of the specified next crop."
                    
            if len(invalid_candidates) > 0:
                # Create a formatted string for each row: "Treatment Name (X months)"
                formatted_reasons = invalid_candidates.apply(
                    lambda x: f"{x['Herbicide Treatment']} ({x['min_plant_back_interval_months']} months)", 
                    axis=1
                )
                
                # Join them with a comma and space
                pretext += " The following treatments are not recommended because they do not meet the plant back interval required by the specified next crop: " + formatted_reasons.str.cat(sep=', ')
            
            payload = json.dumps({
                "primary_herbicide_recommendation_and_agronomic_requirement": pretext 
            })

            response = build_bedrock_response(event, 200, payload, updated_session_attributes)
            return response

        
        # Start with an empty dictionary or initial payload
        payload_dict = {}       
        if len(weed_latin_list) >1:
            if location_group_num == 2 and timing.lower() == "post-emergence":
                pretext = f"For corn fields in {location}, to control weed {weed_1} and {weed_2} at stage of {stage}, with {standardized_crop} to be planted in next season, the {timing} treatment recommendation is:"
            elif soil_type is not None:            
                pretext = f"For corn fields in {location} with {soil_type} soil, to control weed {weed_1} and {weed_2}, with {standardized_crop} to be planted in next season, the {timing} treatment recommendation is:"
            else:
                pretext = f"For corn fields in {location}, to control weed {weed_1} and {weed_2}, with {standardized_crop} to be planted in next season, the {timing} treatment recommendation is:"

        else:
            if location_group_num == 2 and timing.lower() == "post-emergence":
                pretext = f"For corn fields in {location}, to control weed {weed_1} at stage of {stage}, with {standardized_crop} to be planted in next season, the {timing} treatment recommendation is:"
            elif soil_type is not None: 
                pretext = f"For corn fields in {location} with {soil_type} soil, to control weed {weed_1}, with {standardized_crop} to be planted in next season, the {timing} treatment recommendation is:"
            else:
                pretext = f"For corn fields in {location}, to control weed {weed_1}, with {standardized_crop} to be planted in next season, the {timing} treatment recommendation is:"

        top_score = valid_candidates.iloc[0]['lower score']  # does not need sorting since in the original data sheet the treatments are sorted by the score
        top_global_score = valid_candidates.iloc[0]['global score']

        herbicide_treatment_1 = valid_candidates.iloc[0]['Herbicide Treatment']
        restrictions_1 =        valid_candidates.iloc[0]['restrictions']

        if pd.notna(restrictions_1) and restrictions_1 != "":
            recommendation_1 = pretext + f"\n\n**{herbicide_treatment_1}**, with the agronomic requirement for the planting of the next crop: {restrictions_1}."
        else:
            recommendation_1 = pretext + f"\n\n**{herbicide_treatment_1}**"

        if top_score == 3:
            recommendation_1 += "\n\nPlease notice: The treatment has limited efficacy."
            follow_up_suggestion = "Would you consider adjusting the application timing or the next crop to potentially unlock more effective solutions?"
        elif top_score < 3:
            recommendation_1 += "\n\nPlease notice: The treatment has very limited efficacy."
            follow_up_suggestion = "Would you consider adjusting the application timing or the next crop to potentially unlock more effective solutions?"

        if top_score <= 3 and len(invalid_candidates) > 0:
            # Create a formatted string for each row: "Treatment Name (X months)"
            formatted_reasons = invalid_candidates.apply(
                lambda x: f"{x['Herbicide Treatment']} ({x['min_plant_back_interval_months']} months)", 
                axis=1
            )
            
            # Join them with a comma and space
            reason_of_exclusion= " The following treatments are not recommended because they do not meet the plant back interval required by the specified next crop: " + formatted_reasons.str.cat(sep=', ')
        else:
            reason_of_exclusion= ""
      
        if "Monsoon" in herbicide_treatment_1:
            recommendation_1 += "\n\nWarning: Apply Monsoon only before the corn reaches the 6-leaf stage."

        # Add primary recommendation
        payload_dict["primary_herbicide_recommendation_and_agronomic_requirement"] = recommendation_1  
        payload_dict["reason_of_exclusion"] = reason_of_exclusion
        payload_dict["follow_up_suggestion"] = follow_up_suggestion

        # Compare efficacy with the first treatment
        if len(valid_candidates) > 1:
            herbicide_treatment_2 = valid_candidates.iloc[1]['Herbicide Treatment']
            restrictions_2 =        valid_candidates.iloc[1]['restrictions']
            rank_2_score =          valid_candidates.iloc[1]['lower score']
            rank_2_global_score =   valid_candidates.iloc[1]['global score']

            if pd.notna(restrictions_2) and restrictions_2 != "":
                recommendation_2 = pretext + f"\nAn alternative treatment is {herbicide_treatment_2}, with the agronomic requirement for the planting of the next crop: {restrictions_2}."
            else:
                recommendation_2 = pretext + f"\nAn alternative treatment is {herbicide_treatment_2}."

            if rank_2_score == top_score : 
                recommendation_2 += "\nThis treatment provides a similar efficacy to the primary recommendation."
            elif rank_2_score == 4 and top_score ==5:                
                recommendation_2 += "\nThis treatment provides slightly lower efficacy compared to the primary recommendation."
            elif rank_2_score <top_score:
                recommendation_2 += "\nThis treatment provides lower efficacy compared to the primary recommendation."

            if "Monsoon" in herbicide_treatment_2:
                recommendation_2 += "\n\nWarning: Apply Monsoon only before the corn reaches the 6-leaf stage."
            # Add alternative recommendation
            payload_dict["alternative_herbicide_recommendation_and_agronomic_requirement"] = recommendation_2

        # Compare efficacy with the first treatment
        if len(valid_candidates) > 2:
            herbicide_treatment_3 = valid_candidates.iloc[2]['Herbicide Treatment']
            restrictions_3 =        valid_candidates.iloc[2]['restrictions']
            rank_3_score =          valid_candidates.iloc[2]['lower score']
            rank_3_global_score =   valid_candidates.iloc[2]['global score']

            if pd.notna(restrictions_3) and restrictions_3 != "":
                recommendation_3 = f"\nAnother alternative treatment is {herbicide_treatment_3}, with the agronomic requirement for the planting of the next crop: {restrictions_3}."
            else:
                recommendation_3 = f"\nAnother alternative treatment is {herbicide_treatment_3}."

            if rank_3_score == top_score : 
                recommendation_3 += "\nThis treatment provides a similar efficacy to the primary recommendation."
            elif rank_3_score == 4 and top_score ==5:                
                recommendation_3 += "\nThis treatment provides slightly lower efficacy compared to the primary recommendation."

            elif rank_3_score < top_score:
                # Start with the base sentence
                msg = "This treatment provides lower efficacy than the primary recommendation"
                
                # Add second comparison if true
                if rank_3_score < rank_2_score:
                    msg += " and is also less effective than the first alternative"
                
                # Finish with a period and append
                recommendation_3 += f"\n{msg}."


            if "Monsoon" in herbicide_treatment_3:
                recommendation_3 += "\n\nWarning: Apply Monsoon only before the corn reaches the 6-leaf stage."
            # Add another alternative recommendation
            payload_dict["another_alternative_herbicide_recommendation_and_agronomic_requirement"] = recommendation_3


        #print(recommendation)
        # Convert to JSON
        payload = json.dumps(payload_dict)
        
        response = build_bedrock_response(event, 200, payload, updated_session_attributes)

    elif (location_group_num == 3 and (weed_1.lower() == 'amaranthus palmeri' or weed_2.lower() == 'amaranthus palmeri')): # one weed is amaranthus palmeri
        if len(weed_latin_list) >1:
            pretext = f"For corn fields in {location}, to control weed {weed_1} and {weed_2}, with {standardized_crop} to be planted in next season, "
        else:
            pretext = f"For corn fields in {location}, to control weed {weed_1}, with {standardized_crop} to be planted in next season, "
        
        if weed_pressure_level == 'high':
            additional_text = "in cases of high weed pressure, the recommended solution is a consecutive application scheme consisting of both pre-emergence and post-emergence treatments. The pre-emergence application involves using Spade Flexx (0.33 L/HA) together with Dimetenamida 72% (1.4 L/HA) + Diflufenican 50% (0.24 KG/HA). This should be followed by a post-emergence application of Fluva (0.3 L/HA) + Oizysa (0.5 L/HA); "
        else:
            additional_text = "in cases of low weed pressure, the recommendation is pre-emergence applications of Spade Flexx (0.33 L/HA) together with Dimetenamida 72% (1.4 L/HA) + Diflufenican 50% (0.24 KG/HA). "
            additional_text = additional_text + "A single post-emergence treatment is not sufficient, even under moderate pressure."
        ##########################################################################

        if weed_pressure_level == 'high':
            product_names = [ "Spade Flexx", "Fluva", "Oizysa", "Dimetenamida 72%", "Diflufenican 50%" ]
        else:
            product_names = [ "Spade Flexx", "Dimetenamida 72%", "Diflufenican 50%" ]

        additional_text, _ = check_wait_times(product_names, standardized_crop, location_group_num, interval, additional_text, next_crop)

        payload = json.dumps({
            "primary_herbicide_recommendation_and_agronomic_requirement":  pretext + additional_text,
            "override_timing": "true"
             })

        #print(payload)
        response = build_bedrock_response(event, 200, payload, updated_session_attributes)

    elif (location_group_num == 1 and (    weed_1.lower() == 'cyperus esculentus' or  weed_2.lower() == 'cyperus esculentus'
                                        or weed_1.lower() == 'cyperus rotundus'   or  weed_2.lower() == 'cyperus rotundus')
       ): 

        if len(weed_latin_list) >1:
            pretext = f"For corn fields in {location}, to control weed {weed_1} and {weed_2}, with {standardized_crop} to be planted in next season, "
        else:
            pretext = f"For corn fields in {location}, to control weed {weed_1}, with {standardized_crop} to be planted in next season, "
        
        if weed_pressure_level == 'high':
            additional_text = "in cases of high weed pressure, the recommended solution is a consecutive application scheme consisting of both pre-emergence and post-emergence treatments. The pre-emergence application involves using Spade Flexx (0.33 L/HA) together with Dimetenamida 72% (1.0 L/HA); This should be followed by a post-emergence application of Monsoon (1.5 L/HA) together with Fluva (0.3 L/HA)."
        elif  timing == "pre-emergence":
            additional_text = "in cases of low weed pressure and a pre-emergence treatment, the recommendation is applications of Spade Flexx (0.33 L/HA) together with Fluva (0.3 L/HA)"  
        elif  timing == "post-emergence":
            additional_text = "in cases of low weed pressure and a post-emergence treatment, the recommendation is applications of Monsoon (1.5 L/HA) together with Fluva (0.3 L/HA)"

        ##########################################################################

        if weed_pressure_level == 'high':
            product_names = [ "Spade Flexx", "Fluva", "Monsoon", "Dimetenamida 72%" ]
        elif timing == "pre-emergence":
            product_names = [   "Spade Flexx", "Fluva"  ]
        elif timing == "post-emergence":
            product_names = [   "Monsoon", "Fluva"  ]

        additional_text, _ = check_wait_times(product_names, standardized_crop, location_group_num, interval, additional_text, next_crop)

        payload = json.dumps({
            "primary_herbicide_recommendation_and_agronomic_requirement":  pretext + additional_text,
            "override_timing": "true"
             })

        response = build_bedrock_response(event, 200, payload, updated_session_attributes)

    elif (location_group_num == 1 and (    weed_1.lower() == 'setaria verticilata' or  weed_2.lower() == 'setaria verticilata'
                                        or weed_1.lower() == 'setaria viridis'     or  weed_2.lower() == 'setaria viridis')
       ) :

        if len(weed_latin_list) >1:
            pretext = f"For corn fields in {location}, to control weed {weed_1} and {weed_2}, with {standardized_crop} to be planted in next season, "
        else:
            pretext = f"For corn fields in {location}, to control weed {weed_1}, with {standardized_crop} to be planted in next season, "

        additional_text = "in cases of high weed pressure, the recommended solution is a consecutive application scheme consisting of both pre-emergence and post-emergence treatments. The pre-emergence application involves using Spade Flexx (0.33 L/HA) together with Dimetenamida 72% (1.4 L/HA); This should be followed by a post-emergence application of Monsoon (1.5 L/HA) together with Cubix (1.5 L/HA). It is important to closely monitor weed emergence and treat Setaria as soon as it appears, while it is still small."
        ##########################################################################

        product_names = [ "Spade Flexx", "Cubix", "Monsoon", "Dimetenamida 72%" ]

        additional_text, _ = check_wait_times(product_names, standardized_crop, location_group_num, interval, additional_text, next_crop)

        payload = json.dumps({
            "primary_herbicide_recommendation_and_agronomic_requirement":  pretext + additional_text,
            "override_timing": "true"
             })

        response = build_bedrock_response(event, 200, payload, updated_session_attributes)
    return response