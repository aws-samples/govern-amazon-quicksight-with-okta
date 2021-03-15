#!/usr/bin/env python3

from aws_cdk import core
from qs_governance.qs_governance_stack import QSGovernanceStack
import config as cf

app = core.App()

QSGovernanceStack(app, "qs-governance", env=cf.CDK_ENV)

app.synth()
