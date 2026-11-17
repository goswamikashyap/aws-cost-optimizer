# AWS Cost Optimization & FinOps Audit Tool

An automated, serverless FinOps auditing solution built with **Python (`boto3`)**, **Amazon CloudWatch**, **AWS Lambda**, **Amazon EventBridge**, and **Slack Webhooks**. It continuously scans AWS environments on a daily schedule to uncover idle or unattached cloud resources, calculates estimated monthly savings, and delivers actionable reports to Slack.

---

## Architecture & Workflow

```text
┌─────────────────────────┐      ┌──────────────────────────────────┐
│   Amazon EventBridge    │      │         AWS Lambda (Python)      │
│  Schedule: rate(1 day)  ├─────►│  cost_optimizer.lambda_handler   │
└─────────────────────────┘      └────────────────┬─────────────────┘
                                                  │
                     ┌────────────────────────────┼────────────────────────────┐
                     ▼                            ▼                            ▼
        ┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
        │   EC2 & CloudWatch API  │  │    EBS & Snapshots API  │  │   Elastic IPs (VPC) API │
        │  7-day CPU Avg (< 5%)   │  │ Unattached / >30d Old   │  │  Unassociated Addresses │
        └────────────┬────────────┘  └────────────┬────────────┘  └────────────┬────────────┘
                     └────────────────────────────┼────────────────────────────┘
                                                  ▼
                                   ┌─────────────────────────────┐
                                   │   Tag Exclusion & Dry-Run   │
                                   │ (DoNotDelete / Prod Filter) │
                                   └──────────────┬──────────────┘
                                                  ▼
                                   ┌─────────────────────────────┐
                                   │      Slack Webhook API      │
                                   │  Formatted Savings Report   │
                                   └─────────────────────────────┘
```

---

## Key Engineering Highlights

* **Multi-Resource Waste Detection:**
  * **Idle EC2 Instances:** Queries **CloudWatch** `CPUUtilization` metrics over a trailing 7-day window and flags running instances averaging **< 5% CPU**.
  * **Unattached EBS Volumes:** Detects `available` EBS storage volumes detached from any EC2 instance.
  * **Unused Elastic IPs:** Identifies allocated public IPv4 addresses not associated with an active network interface (`AssociationId` missing).
  * **Aging EBS Snapshots:** Scans self-owned EBS snapshots created more than **30 days ago**.
* **Enterprise Safety & Governance:**
  * **Least-Privilege Read-Only IAM:** Executes strictly with `Describe*` and `GetMetricStatistics` permissions (`iam/readonly-cost-policy.json`)—incapable of mutating or deleting production workloads.
  * **Non-Destructive `--dry-run` Mode:** Audits and reports findings safely without triggering resource termination.
  * **Tag-Based Exclusions:** Automatically skips critical infrastructure tagged with `DoNotDelete=true` or `Environment=prod`.
* **Serverless Scheduled Execution:** Deployed on **AWS Lambda** (Python runtime, 128 MB RAM) and triggered automatically every 24 hours by **Amazon EventBridge** (`rate(1 day)`).

---

## Cost Savings Estimation Model

| Resource Category | Detection Criteria | Monthly Savings Formula (USD) |
| :--- | :--- | :--- |
| **Idle EC2 Instance** | State = `running` AND 7-day avg CPU `< 5.0%` | Instance-type hourly rate × 730 hrs (e.g., `$7.60/mo` for `t3.micro`) |
| **Unattached EBS Volume** | Volume status = `available` | `VolumeSize (GB) × $0.08/GB-month` |
| **Unused Elastic IP** | No `AssociationId` attached | `$0.005/hr × 730 hrs = $3.65/month` |
| **Old EBS Snapshot** | Snapshot age `> 30 days` | `VolumeSize (GB) × $0.05/GB-month` |

---

## Repository Structure

```text
├── iam/
│   └── readonly-cost-policy.json   # Least-privilege read-only IAM policy
├── src/
│   └── cost_optimizer.py           # Core boto3 scanner & AWS Lambda handler
├── requirements.txt                # Python dependencies (boto3, requests)
├── .gitignore
└── README.md
```

---

## Quick Start (Local CLI Usage)

1. **Clone the repository and set up a virtual environment:**
   ```bash
   git clone https://github.com/goswamikashyap/aws-cost-optimizer.git
   cd aws-cost-optimizer
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure AWS credentials and Slack Webhook:**
   ```bash
   aws configure
   export SLACK_WEBHOOK_URL="https://slack.com........your own url"
   ```

3. **Run a live read-only scan or multi-resource simulation:**
   ```bash
   # Live read-only audit of your AWS account
   python3 src/cost_optimizer.py --region ap-south-1 --dry-run

   # Multi-resource waste simulation (\$38.00/mo sample savings)
   python3 src/cost_optimizer.py --region ap-south-1 --dry-run --demo
   ```

---

## AWS Lambda Deployment Guide

1. **Package the deployment bundle:**
   ```bash
   mkdir -p package && pip install --target ./package requests
   cp src/cost_optimizer.py package/
   cd package && zip -r ../cost_optimizer_lambda.zip . && cd .. && rm -rf package
   ```
2. **Create the Lambda Function:** Upload `cost_optimizer_lambda.zip` to AWS Lambda, set the handler to `cost_optimizer.lambda_handler`, and increase the timeout to `30 seconds`.
3. **Configure Environment Variables:** Add `AWS_SCAN_REGION` (`ap-south-1`), `DRY_RUN` (`true`), and `SLACK_WEBHOOK_URL`.
4. **Attach IAM Permissions & Trigger:** Attach `AmazonEC2ReadOnlyAccess` and `CloudWatchReadOnlyAccess` to the Lambda execution role, and add an **EventBridge** trigger with schedule expression `rate(1 day)`.

---

## Live Proof of Execution

1. AWS Lambda Scheduled Execution (`statusCode: 200`)

<img width="1272" height="711" alt="1" src="https://github.com/user-attachments/assets/fd1cfae2-24fe-47d9-9ce9-7e0d57163e93" />


2. Automated Slack Cost Optimization Report

<img width="1272" height="711" alt="1" src="https://github.com/user-attachments/assets/c3a76e0d-0d92-4d6e-904f-048c84572bde" />


3. CLI Multi-Resource Savings Simulation (`--demo`)


<img width="1280" height="293" alt="3" src="https://github.com/user-attachments/assets/dbe8ddbf-079b-494f-9574-3e4e9368570b" />
