import os
import json
import argparse
from datetime import datetime, timedelta, timezone
import boto3
import requests

EXCLUDED_TAGS = {"DoNotDelete": "true", "Environment": "prod"}

# Monthly cost estimates (USD, ap-south-1 approximate pricing)
INSTANCE_MONTHLY_ESTIMATE = {
    "t2.micro": 8.50,
    "t3.micro": 7.60,
    "t3.small": 15.20,
    "t3.medium": 30.40,
}
EBS_GB_MONTHLY_RATE = 0.08       # gp3/gp2 ~$0.08/GB-month
UNATTACHED_EIP_MONTHLY = 3.65    # $0.005/hr * 730 hrs
SNAPSHOT_GB_MONTHLY_RATE = 0.05  # $0.05/GB-month


def is_excluded(tags):
    if not tags:
        return False
    for tag in tags:
        key, val = tag.get("Key", ""), tag.get("Value", "")
        if key in EXCLUDED_TAGS and val.lower() == EXCLUDED_TAGS[key].lower():
            return True
    return False


def get_avg_cpu_7d(cw_client, instance_id):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    resp = cw_client.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start,
        EndTime=end,
        Period=86400,
        Statistics=["Average"],
    )
    datapoints = resp.get("Datapoints", [])
    if not datapoints:
        return 0.0
    return sum(d["Average"] for d in datapoints) / len(datapoints)


def scan_aws_account(region="ap-south-1"):
    ec2 = boto3.client("ec2", region_name=region)
    cw = boto3.client("cloudwatch", region_name=region)
    findings = []

    # 1. Idle Running EC2 Instances (Avg CPU < 5% over 7 days)
    reservations = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    ).get("Reservations", [])
    for res in reservations:
        for inst in res.get("Instances", []):
            if is_excluded(inst.get("Tags")):
                continue
            inst_id = inst["InstanceId"]
            inst_type = inst.get("InstanceType", "t3.micro")
            avg_cpu = get_avg_cpu_7d(cw, inst_id)
            if avg_cpu < 5.0:
                savings = INSTANCE_MONTHLY_ESTIMATE.get(inst_type, 10.00)
                findings.append({
                    "type": "Idle EC2 Instance",
                    "id": inst_id,
                    "detail": f"{inst_type} (7d avg CPU: {avg_cpu:.2f}%)",
                    "monthly_savings": round(savings, 2),
                })

    # 2. Unattached EBS Volumes (status == available)
    volumes = ec2.describe_volumes(
        Filters=[{"Name": "status", "Values": ["available"]}]
    ).get("Volumes", [])
    for vol in volumes:
        if is_excluded(vol.get("Tags")):
            continue
        size_gb = vol.get("Size", 8)
        savings = size_gb * EBS_GB_MONTHLY_RATE
        findings.append({
            "type": "Unattached EBS Volume",
            "id": vol["VolumeId"],
            "detail": f"{size_gb} GB ({vol.get('VolumeType', 'gp3')})",
            "monthly_savings": round(savings, 2),
        })

    # 3. Unused Elastic IPs (no AssociationId)
    addresses = ec2.describe_addresses().get("Addresses", [])
    for addr in addresses:
        if is_excluded(addr.get("Tags")):
            continue
        if "AssociationId" not in addr:
            findings.append({
                "type": "Unused Elastic IP",
                "id": addr.get("AllocationId", addr.get("PublicIp")),
                "detail": f"Unassociated IP ({addr.get('PublicIp')})",
                "monthly_savings": UNATTACHED_EIP_MONTHLY,
            })

    # 4. Old EBS Snapshots (> 30 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    snapshots = ec2.describe_snapshots(OwnerIds=["self"]).get("Snapshots", [])
    for snap in snapshots:
        if is_excluded(snap.get("Tags")):
            continue
        if snap["StartTime"] < cutoff:
            size_gb = snap.get("VolumeSize", 8)
            savings = size_gb * SNAPSHOT_GB_MONTHLY_RATE
            findings.append({
                "type": "Old EBS Snapshot (>30d)",
                "id": snap["SnapshotId"],
                "detail": f"{size_gb} GB created {snap['StartTime'].strftime('%Y-%m-%d')}",
                "monthly_savings": round(savings, 2),
            })

    return findings


def get_demo_findings():
    return [
        {"type": "Idle EC2 Instance", "id": "i-0a1b2c3d4e5f001", "detail": "t3.micro (7d avg CPU: 1.40%)", "monthly_savings": 7.60},
        {"type": "Unattached EBS Volume", "id": "vol-0987654321abc01", "detail": "20 GB (gp3)", "monthly_savings": 1.60},
        {"type": "Unattached EBS Volume", "id": "vol-0987654321abc02", "detail": "50 GB (gp2)", "monthly_savings": 4.00},
        {"type": "Unused Elastic IP", "id": "eipalloc-0123456789a", "detail": "Unassociated IP (13.233.44.11)", "monthly_savings": 3.65},
        {"type": "Unused Elastic IP", "id": "eipalloc-0123456789b", "detail": "Unassociated IP (65.2.80.19)", "monthly_savings": 3.65},
        {"type": "Old EBS Snapshot (>30d)", "id": "snap-0fedcba98765401", "detail": "100 GB created 2026-08-14", "monthly_savings": 5.00},
        {"type": "Old EBS Snapshot (>30d)", "id": "snap-0fedcba98765402", "detail": "250 GB created 2026-07-02", "monthly_savings": 12.50},
    ]


def format_report(findings, account_id, region, dry_run=True):
    total_savings = sum(item["monthly_savings"] for item in findings)
    mode_label = "DRY-RUN (Read-Only Audit)" if dry_run else "ACTIVE REPORT"
    lines = [
        f"*AWS Cost Optimization Report* (`{mode_label}`)",
        f"• *Account:* `{account_id}` | *Region:* `{region}`",
        f"• *Wasteful Resources Found:* `{len(findings)}` | *Est. Monthly Savings:* `${total_savings:.2f}/mo`",
        "",
    ]
    if not findings:
        lines.append("✅ No wasteful or idle resources detected in this region.")
    else:
        for item in findings:
            lines.append(
                f"• *{item['type']}* — `{item['id']}` ({item['detail']}) → *Save ${item['monthly_savings']:.2f}/mo*"
            )
    return "\n".join(lines), total_savings


def post_to_slack(webhook_url, message):
    if not webhook_url:
        print("ℹ️ SLACK_WEBHOOK_URL not set; skipping Slack notification.")
        return
    resp = requests.post(webhook_url, json={"text": message}, timeout=10)
    if resp.status_code == 200:
        print("✅ Cost optimization report posted to Slack!")
    else:
        print(f"⚠️ Slack webhook returned HTTP {resp.status_code}: {resp.text}")


def run_scan(region="ap-south-1", dry_run=True, demo=False, slack_webhook=None):
    sts = boto3.client("sts", region_name=region)
    account_id = sts.get_caller_identity()["Account"]
    findings = get_demo_findings() if demo else scan_aws_account(region=region)
    report, total_savings = format_report(findings, account_id, region, dry_run=dry_run)
    print(report)
    post_to_slack(slack_webhook, report)
    return {
        "statusCode": 200,
        "body": json.dumps({
            "account_id": account_id,
            "region": region,
            "dry_run": dry_run,
            "resources_found": len(findings),
            "estimated_monthly_savings_usd": round(total_savings, 2),
        }),
    }


def lambda_handler(event, context):
    region = os.environ.get("AWS_SCAN_REGION", "ap-south-1")
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    return run_scan(region=region, dry_run=dry_run, demo=False, slack_webhook=slack_webhook)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AWS Cost Optimization & Waste Scanner")
    parser.add_argument("--region", default="ap-south-1", help="AWS region to scan")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Run in read-only dry-run mode")
    parser.add_argument("--demo", action="store_true", help="Simulate multi-resource waste findings for demo/testing")
    args = parser.parse_args()

    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    run_scan(region=args.region, dry_run=args.dry_run, demo=args.demo, slack_webhook=webhook)
