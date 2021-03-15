"""
Get Okta User Information for a specific Okta App. Organize and upload that info
to S3
"""

import os
import traceback
import json
import logging
import boto3
import urllib3

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

FAILURE_RESPONSE = {
    'statusCode': 400,
    'body': json.dumps("Okta User Information Retrieval execution has failed"),
}

SUCCESS_RESPONSE = {
    'statusCode': 200,
    'body': json.dumps("Okta User Information Retrieval execution complete"),
}

# Boto3
S3_RESOURCE = boto3.resource('s3')
SECRETS_CLIENT = boto3.client('secretsmanager')

# Environment Variables
BUCKET = os.environ['QS_GOVERNANCE_BUCKET']
KEY = os.environ['QS_USER_GOVERNANCE_KEY']
OKTA_SECRET = os.environ['OKTA_SECRET']

# Urllib3
HTTP = urllib3.PoolManager()

# Okta Specific Secrets
response = SECRETS_CLIENT.get_secret_value(SecretId=OKTA_SECRET)
OKTA_ACCT_ID = json.loads(response['SecretString'])['okta-account-id-secret']
OKTA_APP_ID = json.loads(response['SecretString'])['okta-app-id-secret']
OKTA_APP_TOKEN = json.loads(response['SecretString'])['okta-app-token-secret']
OKTA_URL = f"https://{OKTA_ACCT_ID}.okta.com/api/v1"
OKTA_AUTH = f"SSWS {OKTA_APP_TOKEN}"


def handler(event, _):
    """
    - Get User Info from Okta via API
    - Build User Governance Manifest File
    - Upload the Manifest to S3
    """

    LOGGER.info(f"event: {event}")

    try:
        users = get_users()
        manifest = build_user_governance_manifest(users)
        upload_to_s3(manifest)
        return SUCCESS_RESPONSE

    except Exception as err:
        LOGGER.error(traceback.format_exc())
        raise Exception(FAILURE_RESPONSE) from err


def get_users():
    """
    Use urllib3 to make a REST call to get list of Okta
    Users for a given Okta Application
    """
    request_url = f"{OKTA_URL}/apps/{OKTA_APP_ID}/users"
    okta_users_request = HTTP.request(
        'GET',
        request_url,
        headers={'Content-Type': 'application/json', 'Authorization': OKTA_AUTH},
        retries=False,
    )
    LOGGER.info(f"Retrieved Okta Users Information from {request_url}")
    users = json.loads(okta_users_request.data.decode('utf-8'))
    return users


def get_users_groups(okta_user_id):
    """
    Use urllib3 to make a REST call to get list of Okta
    Users Groups Memberships from a specific okta user id
    """
    request_url = f"{OKTA_URL}/users/{okta_user_id}/groups"
    group_memberships_request = HTTP.request(
        'GET',
        request_url,
        headers={'Content-Type': 'application/json', 'Authorization': OKTA_AUTH},
        retries=False,
    )
    LOGGER.info(f"Retrieved Okta Users Groups Memberships from {request_url}")
    group_memberships = json.loads(group_memberships_request.data.decode('utf-8'))
    return group_memberships


def build_user_governance_manifest(users):
    """
    Build QuickSight Users manifest from the HTTP Request json
    """
    user_manifest = {"users": []}
    for usr in users:
        groups = []
        group_memberships = get_users_groups(usr['id'])
        for grp in group_memberships:
            groups.append(grp['profile']['name'])

        user_manifest['users'].append(
            {
                "username": usr['credentials']['userName'],
                "email": usr['credentials']['userName'],
                "groups": groups,
            }
        )

    LOGGER.info(user_manifest)
    return user_manifest


def upload_to_s3(json_data):
    """
    upload json data to an S3 object
    """
    s3object = S3_RESOURCE.Object(BUCKET, KEY)
    s3object.put(Body=(bytes(json.dumps(json_data).encode('UTF-8'))))
    LOGGER.info(f"Manifest uploaded to s3://{BUCKET}/{KEY}")
