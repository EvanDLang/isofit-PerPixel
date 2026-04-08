import os
import time
import boto3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

REGION    = os.environ.get("AWS_REGION", "us-west-2")
JOB_TABLE = os.environ["DYNAMODB_TABLE"]
TTL_DAYS  = 1

dynamodb = boto3.client("dynamodb", region_name=REGION)


def update_job_status(
    job_id: str,
    status: str,
    pixels_processed: int = 0,
    attempt: int = 0,
    error: str = None,
):
    ttl = int(time.time()) + TTL_DAYS * 24 * 3600
    update_expr = (
        "SET #status = :s, "
        "job_type = :jt, "
        "pixels_processed = :p, "
        "updated_at = :u, "
        "expire_at = :ttl, "
        "attempt_number = :a"
    )
    expr_values = {
        ":s":  {"S": status},
        ":jt": {"S": "inversion"},
        ":p":  {"N": str(pixels_processed)},
        ":u":  {"S": datetime.now(timezone.utc).isoformat()},
        ":ttl":{"N": str(ttl)},
        ":a":  {"N": str(attempt)},
    }
    if error:
        update_expr += ", error_message = :e"
        expr_values[":e"] = {"S": error}

    dynamodb.update_item(
        TableName=JOB_TABLE,
        Key={"job_id": {"S": job_id}},
        UpdateExpression=update_expr,
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues=expr_values,
    )
    logger.info(f"Job {job_id} status → {status}")


def record_attempt_event(
    job_id: str,
    event: str,
    attempt: int,
    detail: str = None,
):
    entry = {
        "M": {
            "attempt":   {"N": str(attempt)},
            "event":     {"S": event},
            "timestamp": {"S": datetime.now(timezone.utc).isoformat()},
            "detail":    {"S": detail or ""},
        }
    }

    update_expr = (
        "SET attempt_history = list_append("
        "if_not_exists(attempt_history, :empty), :entry"
        ")"
    )
    expr_values = {
        ":entry": {"L": [entry]},
        ":empty": {"L": []},
    }

    if event == "interrupted":
        update_expr += ", interruptions = if_not_exists(interruptions, :zero) + :inc"
        expr_values[":zero"] = {"N": "0"}
        expr_values[":inc"]  = {"N": "1"}

    dynamodb.update_item(
        TableName=JOB_TABLE,
        Key={"job_id": {"S": job_id}},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )
    logger.info(f"Job {job_id} event → {event}: {detail or ''}")