"""
Set up everything permission-related for QuickSight:
    a. Pull user information from S3 location
    b. Iterate through users, determine if they already exist in QuickSight.
    c. if the user's namespace doesn't exist, create it
    d. if user doesn't exist in the namespace, register them
    e. update the users roles. if the role is downgraded, delete the user.
    f. assign the user to its groups

Sample User Governance Manifest File:

{
   "users":[
      {
         "username":"qs4@exampletest.com",
         "email":"qs4@exampletest.com",
         "groups":[
            "Everyone",
            "qs_role_author",
            "aws_012345678901_QSGovernance-FederatedQuickSightRole"
         ]
      },
      {
         "username":"qs1@exampletest.com",
         "email":"qs1@exampletest.com",
         "groups":[
            "Everyone",
            "qs_role_admin",
            "aws_012345678901_QSGovernance-FederatedQuickSightRole"
         ]
      }
   ]
}

"""

import os
import traceback
import time
import json
import logging
from dataclasses import dataclass, field
import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

FAILURE_RESPONSE = {
    'statusCode': 400,
    'body': json.dumps('QuickSight User Governance execution has failed'),
}

SUCCESS_RESPONSE = {
    'statusCode': 200,
    'body': json.dumps('QuickSight User Governance execution complete'),
}

# Boto3 Clients
QS_CLIENT = boto3.client('quicksight')
S3_CLIENT = boto3.client('s3')

# Environment Variables
OKTA_ROLE_NAME = os.environ['OKTA_ROLE_NAME']
BUCKET = os.environ['QS_GOVERNANCE_BUCKET']
KEY = os.environ['QS_USER_GOVERNANCE_KEY']
QS_PREFIX = os.environ['OKTA_GROUP_QS_PREFIX']
QS_ADMIN_OKTA_GROUP = os.environ['QS_ADMIN_OKTA_GROUP']
QS_AUTHOR_OKTA_GROUP = os.environ['QS_AUTHOR_OKTA_GROUP']
QS_READER_OKTA_GROUP = os.environ['QS_READER_OKTA_GROUP']


@dataclass
class OktaUser:
    """
    Quicksight User data class. Holds information regarding an Okta User mapped
    to a QuickSight User and its permission assignments
    """

    username: str
    email: str
    groups: []
    account_id: str
    namespace: str
    qs_username: str = field(init=False)
    qs_groups: [str] = field(init=False)
    qs_role: str = field(init=False)

    def __post_init__(self):
        self.qs_username = f"{OKTA_ROLE_NAME}/{self.username}"
        self.qs_groups = [grp for grp in self.groups if grp.startswith(QS_PREFIX)]

        if QS_ADMIN_OKTA_GROUP in self.qs_groups:
            self.qs_role = "ADMIN"
        elif QS_AUTHOR_OKTA_GROUP in self.qs_groups:
            self.qs_role = "AUTHOR"
        elif QS_READER_OKTA_GROUP in self.qs_groups:
            self.qs_role = "READER"
        else:
            self.qs_role = ""


def handler(event, context):
    """
    Handler
        - Runs QuickSight User Governance
    """

    LOGGER.info(f"event: {event}")

    account_id = context.invoked_function_arn.split(":")[4]
    manifest = get_user_manifest(account_id)

    try:
        for user in manifest:
            apply_user_governance(user)
        return SUCCESS_RESPONSE
    except Exception as err:
        LOGGER.error(traceback.format_exc())
        raise Exception(FAILURE_RESPONSE) from err


def get_user_manifest(account_id):
    """
    Retrieve manifest file and create json object full of okta user information
    """
    users = {}
    try:
        data = S3_CLIENT.get_object(Bucket=BUCKET, Key=KEY)
        json_data = json.loads(data['Body'].read().decode('utf-8'))
        users = json_data["users"]
        for user in users:
            user['account_id'] = account_id
            user['namespace'] = "default"
    except ClientError as err:
        LOGGER.info(f"Could not retrieve manifest file. Error: {str(err)}")
    return [OktaUser(**user) for user in users]


def apply_user_governance(user):
    """
    - Add/Update users in QuickSight.
        - if the namespace does not exist, create it
        - if user does not exist, register the user
        - update the user role.
        - if user role was downgraded - exit.
        - otherwise,
        - if the user's group doesnt exist, create it
        - assign user to its groups
    """

    create_if_not_exists_namespace(user)

    register_if_not_exists_user(user)

    if update_role(user):
        if user.qs_groups:
            create_if_not_exists_groups(user)
            update_memberships(user)


def create_if_not_exists_namespace(user):
    """
    check to see if a namespace exists in a QuickSight Account.
    If not, create it.
    """

    try:
        QS_CLIENT.describe_namespace(AwsAccountId=user.account_id, Namespace=user.namespace)
    except ClientError:
        QS_CLIENT.create_namespace(
            AwsAccountId=user.account_id, Namespace=user.namespace, IdentityStore='QUICKSIGHT'
        )
        time.sleep(120)
        LOGGER.info(f"Namespace [{user.namespace}] created.")


def register_if_not_exists_user(user):
    """
    check to see if a user exists in a QuickSight namespace.
    If not, register it.
    """

    try:
        QS_CLIENT.describe_user(
            UserName=user.qs_username,
            AwsAccountId=user.account_id,
            Namespace=user.namespace,
        )
    except ClientError:
        if user.qs_role:
            QS_CLIENT.register_user(
                IdentityType='IAM',
                Email=user.email,
                UserRole=user.qs_role,
                IamArn=f'arn:aws:iam::{user.account_id}:role/{OKTA_ROLE_NAME}',
                SessionName=user.email,
                AwsAccountId=user.account_id,
                Namespace=user.namespace,
            )
            LOGGER.info(f"[{user.qs_username}] added to Namespace [{user.namespace}].")


def delete_user(user):
    """
    Remove the user from QuickSight
    """

    QS_CLIENT.delete_user(
        UserName=user.qs_username,
        AwsAccountId=user.account_id,
        Namespace=user.namespace,
    )
    LOGGER.info(f"[{user.qs_username}] deleted.")


def update_role(user):
    """
    Update QuickSight user role.
    If the User's role is downgraded, delete the user.
    """
    updated = False

    try:
        QS_CLIENT.update_user(
            UserName=user.qs_username,
            AwsAccountId=user.account_id,
            Namespace=user.namespace,
            Role=user.qs_role,
            Email=user.email,
        )
        LOGGER.info(f"[{user.qs_username}] role set to: {user.qs_role}")
        updated = True
    except ClientError as err:
        if (
                err.response['Error']['Code'] == 'ResourceNotFoundException'
                or err.response['Error']['Code'] == 'InvalidParameterValueException'
        ):
            delete_user(user)

    return updated


def create_if_not_exists_groups(user):
    """
    check to see if a group exists in a QuickSight namespace.
    If not, create it. (only create if prefixed appropriately)
    ex. dlp_qs_dev_
    """

    for grp in user.qs_groups:
        try:
            QS_CLIENT.describe_group(
                GroupName=grp, AwsAccountId=user.account_id, Namespace=user.namespace
            )
        except ClientError:

            QS_CLIENT.create_group(
                GroupName=grp, AwsAccountId=user.account_id, Namespace=user.namespace
            )
            time.sleep(3) # let group be created
            LOGGER.info(f"Group [{grp}] added to namespace [{user.namespace}]")


def get_memberships(user):
    """
    get list of current qs users groups
    """

    memberships = []

    list_users_response = QS_CLIENT.list_user_groups(
        UserName=user.qs_username,
        AwsAccountId=user.account_id,
        Namespace=user.namespace,
    )

    for grp in list_users_response['GroupList']:
        memberships.append(grp['GroupName'])

    return memberships


def update_memberships(user):
    """
    Assign a user to its new groups and remove the user from groups it no
    longer belongs to.
    """

    current_memberships = get_memberships(user)
    # assign user to new groups
    for grp in user.qs_groups:
        if grp not in current_memberships:
            QS_CLIENT.create_group_membership(
                MemberName=user.qs_username,
                GroupName=grp,
                AwsAccountId=user.account_id,
                Namespace=user.namespace,
            )
            LOGGER.info(f"[{user.qs_username}] assigned to Group [{grp}].")
    # remove user from old groups
    for grp in current_memberships:
        if grp not in user.qs_groups:
            QS_CLIENT.delete_group_membership(
                MemberName=user.qs_username,
                GroupName=grp,
                AwsAccountId=user.account_id,
                Namespace=user.namespace,
            )
            LOGGER.info(f"[{user.qs_username}] removed from Group [{grp}].")
