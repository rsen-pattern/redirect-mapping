"""SEO Redirect Mapper — Flask entry point."""

from __future__ import annotations

import os

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv
from flask import Flask, jsonify
from snowflake.connector import connect

from web.routes import bp as mapper_bp

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-in-production")
app.register_blueprint(mapper_bp)


@app.before_request
def reload_env():
    if app.debug:
        load_dotenv(override=True)


def get_private_key():
    private_key_path = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
    private_key_content = os.getenv("SNOWFLAKE_PRIVATE_KEY")
    passphrase = os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")

    if passphrase:
        passphrase = passphrase.strip().strip('"').strip("'")

    if private_key_path:
        private_key_path = private_key_path.strip().strip('"').strip("'")
        if "\r" in private_key_path and "\r" != os.linesep:
            private_key_path = private_key_path.replace("\r", "\\r")
        private_key_path = os.path.normpath(private_key_path)
        if not private_key_path:
            raise Exception("SNOWFLAKE_PRIVATE_KEY_PATH is set but empty")
        if not os.path.exists(private_key_path):
            raise Exception(f"Private key file not found at path: {private_key_path}")
        if not os.path.isfile(private_key_path):
            raise Exception(f"Private key path is not a file: {private_key_path}")
        with open(private_key_path, "rb") as key_file:
            key_data = key_file.read()
            if not key_data:
                raise Exception(f"Private key file is empty: {private_key_path}")
            p_key = serialization.load_pem_private_key(
                key_data,
                password=passphrase.encode() if passphrase else None,
                backend=default_backend(),
            )
    elif private_key_content:
        private_key_content = private_key_content.strip().strip('"').strip("'")
        if not private_key_content:
            raise Exception("SNOWFLAKE_PRIVATE_KEY is set but empty")
        key_content = private_key_content.replace("\\n", "\n")
        p_key = serialization.load_pem_private_key(
            key_content.encode(),
            password=passphrase.encode() if passphrase else None,
            backend=default_backend(),
        )
    else:
        raise Exception("Either SNOWFLAKE_PRIVATE_KEY_PATH or SNOWFLAKE_PRIVATE_KEY must be set")

    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_snowflake_connection():
    private_key = get_private_key()
    user = os.getenv("SNOWFLAKE_USERNAME")
    if not user:
        raise Exception("SNOWFLAKE_USERNAME must be set")

    conn_params = {
        "user": user,
        "private_key": private_key,
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
        "database": os.getenv("SNOWFLAKE_DATABASE"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"),
    }
    role = os.getenv("SNOWFLAKE_ROLE")
    if role:
        conn_params["role"] = role
    return connect(**conn_params)


@app.route("/test-connection", methods=["POST"])
def test_connection():
    conn = None
    cursor = None
    try:
        conn = get_snowflake_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT CURRENT_VERSION()")
        version = cursor.fetchone()[0]
        cursor.execute("SELECT CURRENT_DATABASE(), CURRENT_SCHEMA()")
        current_db, current_schema = cursor.fetchone()
        cursor.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()")
        current_user, current_role, current_warehouse = cursor.fetchone()
        cursor.close()
        conn.close()
        return jsonify(
            {
                "success": True,
                "message": "Successfully connected to Snowflake and verified data access!",
                "version": version,
                "database": current_db,
                "schema": current_schema,
                "user": current_user,
                "role": current_role,
                "warehouse": current_warehouse,
            }
        )
    except Exception as exc:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        return jsonify({"success": False, "message": f"Connection test failed: {exc}"}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=3000)
