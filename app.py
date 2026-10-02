import json
import os
import uuid
from functools import lru_cache
from pathlib import Path

import boto3
import pymysql
from botocore.config import Config
from flask import Flask, render_template, request
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@lru_cache(maxsize=1)
def get_s3_client():
    # boto3 uses the EC2 instance role in production; no keys belong in source.
    return boto3.client(
        "s3",
        region_name=os.environ["AWS_DEFAULT_REGION"],
        config=Config(s3={"addressing_style": "path"}),
    )


@lru_cache(maxsize=1)
def get_db_secret():
    client = boto3.client("secretsmanager", region_name=os.environ["AWS_DEFAULT_REGION"])
    response = client.get_secret_value(SecretId=os.environ["DB_SECRET_ARN"])
    return json.loads(response["SecretString"])


def get_registration_config():
    required = (
        "DB_HOST",
        "DB_SECRET_ARN",
        "DB_NAME",
        "S3_BUCKET",
        "AWS_DEFAULT_REGION",
    )
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError("Required application configuration is missing")

    try:
        db_port = int(os.environ.get("DB_PORT", "3306"))
    except ValueError as exc:
        raise ValueError("DB_PORT must be an integer") from exc
    if not 1 <= db_port <= 65535:
        raise ValueError("DB_PORT is out of range")

    return {
        "host": os.environ["DB_HOST"],
        "port": db_port,
        "database": os.environ["DB_NAME"],
        "secret_arn": os.environ["DB_SECRET_ARN"],
        "bucket": os.environ["S3_BUCKET"],
    }


def valid_image(photo, extension, content_type):
    if IMAGE_TYPES.get(extension) != content_type:
        return False

    header = photo.stream.read(12)
    photo.stream.seek(0)
    # ponytail: signature checks avoid an image-library dependency; add full decoding if uploaded images must be transformed.
    if len(header) != 12:
        return False
    if content_type == "image/jpeg":
        return header.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    return len(header) == 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register", methods=["POST"])
def register():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    course = request.form.get("course", "").strip()
    photo = request.files.get("photo")

    if not name or not email or not course or not photo or not photo.filename:
        return "Name, email, course, and photo are required.", 400

    extension = Path(secure_filename(photo.filename)).suffix.lower()
    content_type = IMAGE_TYPES.get(extension)
    if not content_type or not valid_image(photo, extension, photo.mimetype):
        return "Upload a valid JPEG, PNG, or WebP photo.", 400

    try:
        config = get_registration_config()
    except ValueError:
        app.logger.exception("Registration configuration is invalid or incomplete.")
        return "Registration service is not configured.", 503

    try:
        db_secret = get_db_secret()
        connection = pymysql.connect(
            host=config["host"],
            port=config["port"],
            user=db_secret["username"],
            password=db_secret["password"],
            database=config["database"],
            connect_timeout=5,
            charset="utf8mb4",
            autocommit=False,
        )
    except Exception:
        app.logger.exception("Database connection failed.")
        return "Registration service is unavailable.", 503

    object_key = f"uploads/{uuid.uuid4().hex}{extension}"
    uploaded = False
    try:
        try:
            s3 = get_s3_client()
            s3.put_object(
                Bucket=config["bucket"],
                Key=object_key,
                Body=photo.stream,
                ContentType=content_type,
            )
            uploaded = True
            photo_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": config["bucket"], "Key": object_key},
                ExpiresIn=300,
            )

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO students (name, email, course, photo_url)
                    VALUES (%s, %s, %s, %s)
                    """,
                    # Keep the existing schema; photo_url stores this private object key.
                    (name, email, course, object_key),
                )
        except Exception:
            try:
                connection.rollback()
            except pymysql.MySQLError:
                app.logger.exception("Database rollback failed.")
            if uploaded:
                try:
                    s3.delete_object(Bucket=config["bucket"], Key=object_key)
                except Exception:
                    app.logger.exception("Could not remove the uploaded photo after registration failed.")
            app.logger.exception("Student registration failed.")
            return "Registration failed. Please try again.", 503

        try:
            connection.commit()
        except Exception:
            # ponytail: preserve the photo when commit status is ambiguous; add orphan cleanup if failures become common.
            app.logger.exception("Database commit failed; keeping the photo because the commit may have succeeded.")
            return "Registration failed. Please try again.", 503
    finally:
        connection.close()

    return render_template("success.html", photo_url=photo_url), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
