# Herbicide Recommendation Decision Flowchart

Open this file in a Mermaid-compatible viewer (GitHub, VS Code with Mermaid extension, mermaid.live, etc.)

## Full Decision Flow

```mermaid
flowchart TD
    START([Lambda Handler Invoked]) --> PARSE[Parse parameters from<br>Bedrock Agent event]
    PARSE --> TIMING{timing contains<br>'post' or 'pre'?}
    TIMING -->|contains 'post'| SET_POST[timing = post-emergence]
    TIMING -->|contains 'pre'| SET_PRE[timing = pre-emergence]
    TIMING -->|neither| ASK_TIMING[/Ask user: pre or post-emergence?/]

    SET_POST --> PARSE2[Parse follow_up_treatment,<br>taboo_list,<br>previously_applied_products]
    SET_PRE --> PARSE2

    PARSE2 --> TABOO_EXTEND[Extend taboo list with<br>previously applied products]
    TABOO_EXTEND --> ADENGO_TABOO{Adengo in<br>previously applied?}
    ADENGO_TABOO -->|Yes| ADD_TABOO[Add Spade Flexx &<br>Monsoon to taboo]
    ADENGO_TABOO -->|No| VAL1
    ADD_TABOO --> VAL1

    %% ── Validation cascade ──
    VAL1{follow_up_treatment<br>but no products listed?}
    VAL1 -->|Yes| ASK_PREV[/Ask user for previously<br>applied products/]
    VAL1 -->|No| VAL2

    VAL2{location<br>missing?}
    VAL2 -->|Yes| ASK_LOC[/Ask user for province/]
    VAL2 -->|No| VAL3

    VAL3{location_group_num<br>missing?}
    VAL3 -->|Yes| ASK_GRP[/Ask LLM to classify<br>location into group/]
    VAL3 -->|No| VAL4

    VAL4{next_crop<br>missing?}
    VAL4 -->|Yes| ASK_CROP[/Ask user for next crop/]
    VAL4 -->|No| VAL5

    VAL5{Group 2 AND<br>post-emergence AND<br>stage not set?}
    VAL5 -->|Yes| ASK_STAGE[/Ask: less than 3 leaves<br>or 3 leaves or more?/]
    VAL5 -->|No| VAL6

    VAL6{Amaranthus palmeri<br>AND Group 3 AND<br>pressure not set?}
    VAL6 -->|Yes| ASK_PRESS_AP[/Ask weed pressure:<br>high or low?/]
    VAL6 -->|No| VAL7

    VAL7{NOT Amaranthus palmeri<br>AND Group 3 AND<br>pre-emergence AND<br>soil_type not set?}
    VAL7 -->|Yes| ASK_SOIL[/Ask soil type:<br>sandy or not sandy?/]
    VAL7 -->|No| VAL8

    VAL8{Setaria AND<br>Group 1 AND<br>pressure not set?}
    VAL8 -->|Yes| ASK_PRESS_S[/Ask weed pressure:<br>high or low?/]
    VAL8 -->|No| VAL9

    VAL9{Cyperus AND<br>Group 1 AND<br>pressure not set?}
    VAL9 -->|Yes| ASK_PRESS_C[/Ask weed pressure:<br>high or low?/]
    VAL9 -->|No| DOSE
```

## Dose Determination

```mermaid
flowchart TD
    DOSE[Determine Dose Level] --> D1{Group 2 AND<br>pre-emergence?}
    D1 -->|Yes| LOW[dose = low]
    D1 -->|No| D2{Group 2 AND<br>stage = less than<br>3 leaves?}
    D2 -->|Yes| LOW
    D2 -->|No| D3{Group 3 AND<br>NOT Amaranthus palmeri<br>AND pre-emergence<br>AND sandy soil?}
    D3 -->|Yes| MED[dose = medium]
    D3 -->|No| HIGH[dose = high]

    LOW --> RESOLVE
    MED --> RESOLVE
    HIGH --> RESOLVE
    RESOLVE[Resolve crop & weed names<br>via embeddings]
```

## Name Resolution

```mermaid
flowchart TD
    RESOLVE[Name Resolution] --> CROP_EMBED[Embed next_crop text<br>via Titan Embed v2]
    CROP_EMBED --> CROP_MATCH[Find top-3 cosine matches<br>in crop canonical embeddings]
    CROP_MATCH --> CROP_SIM{Best score<br>≥ 0.6?}
    CROP_SIM -->|No| CROP_ERR[/Return: crop not in database/]
    CROP_SIM -->|Yes| CROP_STD[Map through CROP_VAR_TO_STANDARD]
    CROP_STD --> CROP_VALID{standardized_crop<br>in CROP_SET?}
    CROP_VALID -->|No| CROP_ERR2[/Return: crop not in list/]
    CROP_VALID -->|Yes| WEED_RES

    WEED_RES[Resolve Weed 1] --> W1_EMBED[Embed weed_1 text]
    W1_EMBED --> W1_MATCH[Find top-3 matches<br>threshold ≥ 0.15]
    W1_MATCH --> W1_ANY{Any matches<br>found?}
    W1_ANY -->|No| W1_ERR[/Return: weed not found,<br>no similar matches/]
    W1_ANY -->|Yes| W1_SIM{Best score<br>≥ 0.6?}
    W1_SIM -->|No| W1_SUGGEST[/Return: Did you mean<br>1: X, 2: Y, 3: Z?/]
    W1_SIM -->|Yes| W1_OK[Map via weed_dict<br>to Latin name]

    W1_OK --> W2_CHECK{More than<br>1 weed?}
    W2_CHECK -->|No| MAIN_BRANCH
    W2_CHECK -->|Yes| W2_RES[Resolve Weed 2<br>same process as Weed 1]
    W2_RES --> MAIN_BRANCH[Main Treatment Branch]
```

## Main Treatment Branch

```mermaid
flowchart TD
    MAIN{Which path?} -->|See conditions below| PATH_A
    MAIN --> PATH_B
    MAIN --> PATH_C
    MAIN --> PATH_D

    subgraph CONDITIONS [Branch Selection Logic]
        direction TB
        NOTE1["<b>Path A — Standard Table Lookup</b><br>Group 1 + (low pressure OR non-special weeds) OR<br>Group 2 OR<br>Group 3 + NOT Amaranthus palmeri"]
        NOTE2["<b>Path B — Amaranthus palmeri special</b><br>Group 3 + weed is Amaranthus palmeri"]
        NOTE3["<b>Path C — Cyperus special</b><br>Group 1 + weed is Cyperus rotundus/esculentus"]
        NOTE4["<b>Path D — Setaria special</b><br>Group 1 + weed is Setaria verticilata/viridis<br>(high pressure)"]
    end

    subgraph PATH_A [Path A: Standard Table Lookup]
        direction TB
        A1[Filter herbicide_df by:<br>weed1 + weed2 combo<br>+ timing + dose level<br>sorted by Rank] --> A2[Loop through candidates]
        A2 --> A4{Any product in<br>taboo list?}
        A4 -->|Yes| A_SKIP[Skip this candidate]
        A4 -->|No| A5[Look up wait time<br>for each product in treatment<br>against next crop + location group]
        A5 --> A6{All wait times ≤<br>planting interval?}
        A6 -->|Yes| A_VALID[Add to valid_candidates]
        A6 -->|No| A_INVALID[Add to invalid_candidates]
        A_SKIP --> A2
        A_VALID --> A2
        A_INVALID --> A2
    end

    subgraph PATH_B [Path B: Amaranthus palmeri — Group 3]
        direction TB
        B1{Weed pressure<br>level?}
        B1 -->|High| B_HIGH["Consecutive scheme:<br><b>Pre:</b> Spade Flexx 0.33 L/HA<br>+ Dimetenamida 72% 1.4 L/HA<br>+ Diflufenican 50% 0.24 KG/HA<br><b>Post:</b> Fluva 0.3 L/HA<br>+ Oizysa 0.5 L/HA"]
        B1 -->|Low| B_LOW["Pre-emergence only:<br>Spade Flexx 0.33 L/HA<br>+ Dimetenamida 72% 1.4 L/HA<br>+ Diflufenican 50% 0.24 KG/HA<br><i>(post-emergence alone insufficient)</i>"]
        B_HIGH --> B_WAIT[Check wait times for all products]
        B_LOW --> B_WAIT
    end

    subgraph PATH_C [Path C: Cyperus — Group 1]
        direction TB
        C1{Weed pressure<br>level?}
        C1 -->|High| C_HIGH["Consecutive scheme:<br><b>Pre:</b> Spade Flexx 0.33 L/HA<br>+ Dimetenamida 72% 1.0 L/HA<br><b>Post:</b> Monsoon 1.5 L/HA<br>+ Fluva 0.3 L/HA"]
        C1 -->|Low + pre| C_PRE["Spade Flexx 0.33 L/HA<br>+ Fluva 0.3 L/HA"]
        C1 -->|Low + post| C_POST["Monsoon 1.5 L/HA<br>+ Fluva 0.3 L/HA"]
        C_HIGH --> C_WAIT[Check wait times for all products]
        C_PRE --> C_WAIT
        C_POST --> C_WAIT
    end

    subgraph PATH_D [Path D: Setaria — Group 1]
        direction TB
        D1["Consecutive scheme:<br><b>Pre:</b> Spade Flexx 0.33 L/HA<br>+ Dimetenamida 72% 1.4 L/HA<br><b>Post:</b> Monsoon 1.5 L/HA<br>+ Cubix 1.5 L/HA<br><i>(monitor & treat early)</i>"]
        D1 --> D_WAIT[Check wait times for all products]
    end
```

## Path A: Candidate Ranking & Response

```mermaid
flowchart TD
    FILTER[After candidate loop] --> F1[valid_candidates:<br>keep row 0 always +<br>rows with lower score ≥ 3]
    F1 --> F2[invalid_candidates:<br>keep only lower score > 3]
    F2 --> VC{valid_candidates<br>empty?}

    VC -->|Yes| NO_VALID{invalid_candidates<br>exist?}
    NO_VALID -->|No| NONE_RESP[/Return: no applicable treatments/]
    NO_VALID -->|Yes| NONE_WITH_REASON[/Return: no treatments meet<br>plant-back interval + list<br>excluded treatments with<br>required months/]

    VC -->|No| BUILD[Build primary recommendation<br>= rank 1 treatment]
    BUILD --> EFF1{top lower score<br>= 3?}
    EFF1 -->|Yes| WARN_LIMITED[Append: limited efficacy<br>+ suggest adjusting timing/crop]
    EFF1 -->|No| EFF2{top lower score<br>< 3?}
    EFF2 -->|Yes| WARN_VERY[Append: very limited efficacy]
    EFF2 -->|No| CHECK_ALT

    WARN_LIMITED --> CHECK_ALT
    WARN_VERY --> CHECK_ALT

    CHECK_ALT{More than 1<br>valid candidate?}
    CHECK_ALT -->|Yes| ALT[Add alternative recommendation<br>with efficacy comparison]
    CHECK_ALT -->|No| RESP

    ALT --> CHECK_ALT2{More than 2<br>valid candidates?}
    CHECK_ALT2 -->|Yes| ALT2[Add 2nd alternative<br>with efficacy comparison]
    CHECK_ALT2 -->|No| RESP

    ALT2 --> RESP[/Return Bedrock response<br>with session attributes/]
```

## Paths B/C/D: Wait Time Validation

```mermaid
flowchart TD
    WAIT[Check wait times for<br>all products in treatment] --> VALID{All product wait times<br>≤ planting interval?}
    VALID -->|Yes| ADD_RESTR[Append crop-specific<br>agronomic restrictions]
    VALID -->|No| CANT_PLANT[/Append warning: cannot plant<br>next_crop due to residue<br>restrictions/]
    ADD_RESTR --> MONSOON{Treatment includes<br>Monsoon?}
    CANT_PLANT --> MONSOON
    MONSOON -->|Yes| WARN_M[/Append: apply Monsoon only<br>before corn 6-leaf stage/]
    MONSOON -->|No| RETURN
    WARN_M --> RETURN[/Return Bedrock response<br>with override_timing = true/]
```
