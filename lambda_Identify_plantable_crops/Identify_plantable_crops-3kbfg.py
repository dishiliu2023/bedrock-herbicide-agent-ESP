
import boto3
import pandas as pd
import io
import os
from datetime import datetime
import re
import numpy as np
import json

## Show all columns in the DataFrame
#pd.set_option('display.max_columns', None)

## Optional: Show full width of each column
#pd.set_option('display.max_colwidth', None)

## Optional: Prevent wrapping of wide DataFrames
#pd.set_option('display.width', None)


s3 = boto3.client('s3')

# These should be set as environment variables in your Lambda function

PLANTING_DATES_S3_KEY = os.environ['PLANTING_DATES_S3_KEY']
WAIT_TIME_TABLE_S3_KEY = os.environ['WAIT_TIME_TABLE_S3_KEY']
S3_BUCKET_NAME = os.environ['S3_BUCKET_NAME']

def lambda_handler(event, context):
    
    #print("EVENT:", json.dumps(event)) # Add this line to inspect the structure    

    # Extract the list of properties
    try:
        properties = event["requestBody"]["content"]["application/json"]["properties"]
    except KeyError:
        return {
            "statusCode": 400,
            "body": "Missing or malformed requestBody content."
        }

    # Convert list of properties to a dictionary
    params = {prop["name"]: prop["value"] for prop in properties}


    # Now safely access the values
    herbicide_names = params.get("herbicide_names")
 
    #print("herbicide_names:", herbicide_names) 

    ## Convert the string to a list

    # Remove the brackets and split the string
    herbicide_list = herbicide_names.strip("[]").split(", ")

    # Remove any surrounding quotes from each element
    herbicide_list = [item.strip("'\"") for item in herbicide_list]

    herbi_1 = herbicide_list[0]
    if len(herbicide_list) >1:        
        herbi_2 = herbicide_list[1]
    else:
        herbi_2 = "None"

    #print("herbi_1:", herbi_1)
    #print("herbi_2:", herbi_2)	


    # Load planting dates
    planting_obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=PLANTING_DATES_S3_KEY)
    planting_df  = pd.read_csv(io.BytesIO(planting_obj['Body'].read()))

    # Load wait time table
    wait_time_obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=WAIT_TIME_TABLE_S3_KEY)    
    wait_time_df = pd.read_csv(io.BytesIO(wait_time_obj['Body'].read()) )

    # Define the reference month (June) which is the herbicide application time
    reference_month = datetime.strptime("June", "%B")

    # Function to format planting date
    def format_planting_date(planting_date):
        if "next year" in planting_date:
            planting_date = planting_date.replace(" next year", "")
            planting_date = datetime.strptime(planting_date, "%B")
            planting_date = planting_date.replace(year=reference_month.year + 1)
        else:
            planting_date = datetime.strptime(planting_date, "%B")
            planting_date = planting_date.replace(year=reference_month.year)
        return planting_date

    # Apply the function to create the new column
    planting_df['formatted_planting_date'] = planting_df['Earliest Planting Date'].apply(format_planting_date)
    

    #print(planting_df['formatted_planting_date'] )
    
    # Function to calculate the interval in months
    def calculate_interval(formatted_date):
        return (formatted_date.year - reference_month.year) * 12 + (formatted_date.month - reference_month.month)

    # Apply the function to create the 'Interval' column
    planting_df['Interval'] = planting_df['formatted_planting_date'].apply(calculate_interval)

    #planting_df = planting_df.sort_values(by='Crop').reset_index(drop=True)

    #print(planting_df[['formatted_planting_date', 'Interval'] ] )

    if len(herbicide_list) >1:        
        wait_time_1 = wait_time_df[wait_time_df['Herbicide product'].str.lower() == herbi_1.lower()][['Next crop', 'Shortest time interval','Restriction']]
        wait_time_2 = wait_time_df[wait_time_df['Herbicide product'].str.lower() == herbi_2.lower()][['Next crop', 'Shortest time interval','Restriction']]

        # Merge DataFrames on 'Next crop'
        merged_df = pd.merge(wait_time_1, wait_time_2, on='Next crop', suffixes=('_1', '_2'))

        # Create WAIT_TIME DataFrame with the maximum 'Shortest time interval'
        WAIT_TIME = merged_df[['Next crop']].copy()
        WAIT_TIME['Shortest time interval'] = merged_df[['Shortest time interval_1', 'Shortest time interval_2']].max(axis=1)
        WAIT_TIME['Restriction'] = merged_df[['Restriction_1', 'Restriction_2']].apply(lambda x: ', and '.join(x.dropna().astype(str)) if x.notna().any() else pd.NA, axis=1)
    else:
        WAIT_TIME = wait_time_df[wait_time_df['Herbicide product'].str.lower() == herbi_1.lower()][['Next crop', 'Shortest time interval','Restriction']]
    
    #WAIT_TIME = WAIT_TIME.sort_values(by='Next crop').reset_index(drop=True)

    #print(wait_time_1)
    #print()
    #print(wait_time_2)
    #print()
    #print(WAIT_TIME )

    #set_crop = set(planting_df['Crop'].unique())
    #set_next_crop = set(WAIT_TIME['Next crop'].unique())

    ## Check if they are exactly the same
    #print(set_crop == set_next_crop)

    ## To see differences
    #print("In Crop but not in Next crop:", set_crop - set_next_crop)
    #print("In Next crop but not in Crop:", set_next_crop - set_crop)

    # Merge DataFrames on 'Next crop'
    merged_df = pd.merge(planting_df, WAIT_TIME, left_on='Crop', right_on='Next crop', how='inner' )
    #print(WAIT_TIME['Next crop'])
    #print(merged_df[['Crop','Next crop']]) #, 'Interval','Shortest time interval']])
    
    merged_df['plantable'] = merged_df['Interval'] >= merged_df['Shortest time interval']
    #print(merged_df[['Crop', 'Interval','Shortest time interval', 'plantable']])  

    plantable_df = merged_df[merged_df['plantable']]
    print(plantable_df)

    crops_with_restrictions = []
    for index, row in plantable_df.iterrows():
        if pd.notna(row['Restriction']):
            crops_with_restrictions.append(f"{row['Crop']} ({row['Restriction']})")
        else:
            crops_with_restrictions.append(row['Crop'])
    
    if len(crops_with_restrictions) == 0:
        plantable_crop = "None"
    else:
        # Join the crops with restrictions into a single string
        if len(crops_with_restrictions) > 1:
            plantable_crop = ', '.join(crops_with_restrictions[:-1]) + ', and ' + crops_with_restrictions[-1]
        else:
            plantable_crop = crops_with_restrictions[0]
        
    #print(plantable_crop)

    payload = json.dumps({
        "crop_list": plantable_crop
    })
 
    response = {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup"),
            "apiPath": event.get("apiPath"),
            "httpMethod": event.get("httpMethod"),
            "httpStatusCode": 200,
            "responseBody": {
                "application/json": {
                    "body": payload          # <-- string-encoded JSON
                }
            }
        }
    }
    #print("RESPONSE:", json.dumps(response))

    return response