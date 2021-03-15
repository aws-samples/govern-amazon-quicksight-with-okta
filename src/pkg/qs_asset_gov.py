"""
Apply QuickSight Group/DataSet Permissions:
    a. Get Manifest file from S3 containing QS Group Permissions for DataSets.
    b. Update permissions based on manifest


Sample Asset Governance Manifest File:

{
   "assets":[
      {
         "name": "dataset_example_1",
         "category":"dataset",
         "namespace":"default",
         "groups": [
            "qs_group_ops",
            "qs_group_finance"
         ],
         "permission": "READ"
      },
      {
         "name": "dataset_example_2",
         "category":"dataset",
         "namespace":"default",
         "groups": [
            "qs_group_hr"
         ],
         "permission": "READ"
      }
   ]
}


"""

import os
import traceback
import json
import logging
from dataclasses import dataclass
import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

FAILURE_RESPONSE = {
    'statusCode': 400,
    'body': json.dumps('QuickSight asset governance has failed'),
}

SUCCESS_RESPONSE = {
    'statusCode': 200,
    'body': json.dumps('QuickSight asset governance execution complete'),
}

# Boto3
QS_CLIENT = boto3.client('quicksight')
S3_CLIENT = boto3.client('s3')
REGION = QS_CLIENT.meta.region_name

# Environment Variables
BUCKET = os.environ['QS_GOVERNANCE_BUCKET']
KEY = os.environ['QS_ASSET_GOVERNANCE_KEY']

# group permissions Variables
READ_ACTIONS = [
    "quicksight:DescribeDataSet",
    "quicksight:DescribeDataSetPermissions",
    "quicksight:PassDataSet",
    "quicksight:DescribeIngestion",
    "quicksight:ListIngestions",
]


@dataclass
class QuickSightAsset:
    """
    Quicksight Asset data class. Holds information regarding a QuickSight asset
    and its permission assignments
    """

    name: str
    category: str
    namespace: str
    groups: [str]
    permission: str
    account_id: str


def handler(event, context):
    """
    Handler
        - Runs QuickSight Asset Governance
        - Update QuickSight with asset permissions based on a supplied manifest
        file.
    """

    LOGGER.info(f"event: {event}")

    account_id = context.invoked_function_arn.split(":")[4]
    all_datasets = get_all_datasets(account_id)
    manifest = get_asset_manifest(account_id)

    try:
        for asset in manifest:
            if asset.category == "dataset":
                dataset_id = get_dataset_id(asset, all_datasets)
                reset_dataset_permissions(asset, dataset_id)
                apply_dataset_governance(asset, dataset_id)
            # elif asset.category == "dashboard":
            #     # Dashboard Asset Governance
            #     continue
            # elif asset.category == "theme":
            #     continue
            #     # Theme Asset Governance
            # elif asset.category == "analyses":
            #     continue
            #     # Analysis Asset Governance
        return SUCCESS_RESPONSE

    except Exception as err:
        LOGGER.error(traceback.format_exc())
        raise Exception(FAILURE_RESPONSE) from err


def get_all_datasets(account_id):
    """
    Paginate through all list_data_sets responses and build a list of every
    dataset in the QuickSight account.
    """
    all_datasets = []
    response = QS_CLIENT.list_data_sets(AwsAccountId=account_id)
    for dset in response['DataSetSummaries']:
        all_datasets.append(dset)
    while response.get("NextToken", None) is not None:
        response = QS_CLIENT.list_data_sets(
            AwsAccountId=account_id, NextToken=response.get("NextToken")
        )
        for dset in response['DataSetSummaries']:
            all_datasets.append(dset)
    return all_datasets


def get_asset_manifest(account_id):
    """
    Retrieve manifest file and generate list of asset objects
    """
    assets = {}
    try:
        data = S3_CLIENT.get_object(Bucket=BUCKET, Key=KEY)
        json_data = json.loads(data['Body'].read().decode('utf-8'))
        assets = json_data['assets']
        for asset in assets:
            asset['account_id'] = account_id
    except ClientError as err:
        LOGGER.info(f"Could not retrieve manifest file. Error: {str(err)}")
    return [QuickSightAsset(**asset) for asset in assets]


def apply_dataset_governance(asset, dataset_id):
    """
    Use governed asset information to update the permissions of a QuickSight
    Dataset. Give permissions to all groups in that asset entry.
    """

    actions = ''
    if asset.permission == "READ":
        actions = READ_ACTIONS

    for group in asset.groups:
        principal = (
            f"arn:aws:quicksight:{REGION}:{asset.account_id}:group/" f"{asset.namespace}/{group}"
        )

        try:
            QS_CLIENT.update_data_set_permissions(
                AwsAccountId=asset.account_id,
                DataSetId=dataset_id,
                GrantPermissions=[
                    {'Principal': principal, 'Actions': actions},
                ],
            )

            LOGGER.info(
                f"Dataset [{asset.name}] permissions given to group [{group}] in "
                f"namespace [{asset.namespace}]"
            )
        except ClientError as err:
            if err.response['Error']['Code'] == 'InvalidParameterValueException':
                LOGGER.info(
                    "Failed to apply permissions. Please validate that "
                    f"[{asset.category}] [{asset.name}], namespace "
                    f"[{asset.namespace}], and group [{group}] all exist in "
                    "QuickSight."
                )


def reset_dataset_permissions(asset, dataset_id):
    """
    Revoke all permissions assigned to a specific dataset.
    """

    response = QS_CLIENT.describe_data_set_permissions(
        AwsAccountId=asset.account_id, DataSetId=dataset_id
    )
    permissions = response['Permissions']

    for permission in permissions:
        principal = permission['Principal']
        actions = permission['Actions']
        if "group" in principal:
            try:
                QS_CLIENT.update_data_set_permissions(
                    AwsAccountId=asset.account_id,
                    DataSetId=dataset_id,
                    RevokePermissions=[
                        {'Principal': principal, 'Actions': actions},
                    ],
                )
            except ClientError as err:
                if err.response['Error']['Code'] == 'InvalidParameterValueException':
                    LOGGER.info(
                        "Failed to apply permissions. Please validate that "
                        f"[{asset.category}] [{asset.name}], namespace "
                        f"[{asset.namespace}], and principal [{principal}] all exist in "
                        "QuickSight."
                    )
    LOGGER.info(f"Permissions reset for [{asset.category}] [{asset.name}]")


def get_dataset_id(asset: QuickSightAsset, all_datasets: dict):
    """
    Get the DataSetID based on a DataSet Name
    """
    ds_id = ''
    for dset in all_datasets:
        if dset['Name'] == asset.name:
            ds_id = dset['DataSetId']
            break
    return ds_id
