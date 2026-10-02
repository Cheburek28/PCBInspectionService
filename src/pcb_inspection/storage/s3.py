from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from pcb_inspection.storage.base import BlobNotFound, validate_key

_MISSING = {"404", "NoSuchKey", "NotFound"}


class S3Storage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=validate_key(key), Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=validate_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:
                raise BlobNotFound(key) from None
            raise
        data: bytes = obj["Body"].read()
        return data

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=validate_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=validate_key(key))

    def ping(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)
