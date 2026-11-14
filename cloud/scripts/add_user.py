"""Invite a team member to the dashboard (self sign-up is off).

  uv run python cloud/scripts/add_user.py someone@example.com

Cognito emails them a temporary password; they set a new one at first sign-in.
"""

import sys

import boto3

REGION = "us-east-2"


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    email = sys.argv[1]
    cf = boto3.client("cloudformation", region_name=REGION)
    outs = {o["OutputKey"]: o["OutputValue"]
            for o in cf.describe_stacks(StackName="ForkliftFleet")["Stacks"][0]["Outputs"]}
    boto3.client("cognito-idp", region_name=REGION).admin_create_user(
        UserPoolId=outs["UserPoolId"], Username=email,
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        DesiredDeliveryMediums=["EMAIL"],
    )
    print(f"invited {email}; they'll get a temporary password by email")


if __name__ == "__main__":
    main()
