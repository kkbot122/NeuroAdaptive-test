"""Private S3-compatible storage gateway (Supabase Storage in production)."""
from dataclasses import dataclass
import hashlib

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings


@dataclass(frozen=True)
class UploadIntent:
    upload_url: str
    required_headers: dict[str, str]


@dataclass(frozen=True)
class ObjectInfo:
    size_bytes: int


class StorageUnavailable(Exception):
    def __init__(self, configured=True):
        self.configured = configured
        super().__init__("Private storage is unavailable" if configured else "Private storage is not configured")


class S3PrivateStorage:
    def __init__(self) -> None:
        if not all((settings.STORAGE_S3_ENDPOINT, settings.STORAGE_S3_ACCESS_KEY, settings.STORAGE_S3_SECRET_KEY)):
            # No ambient AWS credential/metadata fallback in local development.
            raise StorageUnavailable(configured=False)
        try:
            self.client = boto3.client(
                "s3", endpoint_url=settings.STORAGE_S3_ENDPOINT or None,
                aws_access_key_id=settings.STORAGE_S3_ACCESS_KEY or None,
                aws_secret_access_key=settings.STORAGE_S3_SECRET_KEY or None,
                region_name=settings.STORAGE_S3_REGION,
                config=Config(signature_version="s3v4"),
            )
        except (BotoCoreError, ValueError, TypeError):
            raise StorageUnavailable() from None

    def create_upload_intent(self, key: str, content_type: str | None) -> UploadIntent:
        params = {"Bucket": settings.STORAGE_BUCKET, "Key": key,
                  "ContentType": content_type or "application/octet-stream"}
        return UploadIntent(
            self.client.generate_presigned_url("put_object", Params=params,
                ExpiresIn=settings.STORAGE_SIGNED_URL_TTL_SECONDS_V1),
            {"Content-Type": params["ContentType"]},
        )

    def inspect(self, key: str) -> ObjectInfo:
        try:
            result = self.client.head_object(Bucket=settings.STORAGE_BUCKET, Key=key)
        except (BotoCoreError, ClientError):
            raise StorageUnavailable() from None
        return ObjectInfo(result["ContentLength"])

    def checksum_sha256(self, key: str) -> str:
        try:
            body = self.client.get_object(Bucket=settings.STORAGE_BUCKET, Key=key)["Body"]
            digest = hashlib.sha256()
            for chunk in body.iter_chunks():
                digest.update(chunk)
            return digest.hexdigest()
        except (BotoCoreError, ClientError):
            raise StorageUnavailable() from None

    def read(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=settings.STORAGE_BUCKET, Key=key)["Body"].read()
        except (BotoCoreError, ClientError):
            raise StorageUnavailable() from None

    def delete(self, key: str) -> None:
        """Delete a private object; deleting an already absent key is safe."""
        try:
            self.client.delete_object(Bucket=settings.STORAGE_BUCKET, Key=key)
        except (BotoCoreError, ClientError):
            raise StorageUnavailable() from None

    def create_download_url(self, key: str) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": settings.STORAGE_BUCKET, "Key": key}, ExpiresIn=settings.STORAGE_SIGNED_URL_TTL_SECONDS_V1)
