# export_openapi.py
import json
from pathlib import Path

from app.main import api  # adjust if your FastAPI app is in another module


def export_openapi(path: str = "openapi.json"):
    schema = api.openapi()
    Path(path).write_text(json.dumps(schema, indent=2))
    print(f"✅ Wrote OpenAPI schema to {path}")


if __name__ == "__main__":
    export_openapi("openapi.json")
