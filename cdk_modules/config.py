"""Constants file for resource naming and env values"""
import os

###################################
# Per Account/App Setup (Edit as Needed)
###################################
ACCOUNT = "<account id>"
REGION = "<region>"
CDK_ENV = {"account": ACCOUNT, "region": REGION}

###################################
# Project Specific Setup
###################################
PROJECT = "QSGovernance"
OKTA_SECRET = "okta_info"
OKTA_IDP_NAME = "Okta"
OKTA_ROLE_NAME = "FederatedQuickSightRole"
OKTA_GROUP_QS_PREFIX = "qs_"
QS_ADMIN_OKTA_GROUP = "qs_role_admin"
QS_AUTHOR_OKTA_GROUP = "qs_role_author"
QS_READER_OKTA_GROUP = "qs_role_reader"

###################################
# Manifest Data
###################################
QS_USER_GOVERNANCE_KEY = "qs-user-governance.json"
QS_ASSET_GOVERNANCE_KEY = "qs-asset-governance.json"

###################################
# Setting up repo paths
###################################
PATH_CDK = os.path.dirname(os.path.abspath(__file__))
PATH_ROOT = os.path.dirname(PATH_CDK)
PATH_SRC = os.path.join(PATH_ROOT, 'src')