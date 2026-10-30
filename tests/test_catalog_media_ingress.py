"""GuardOath's JSON-only input boundary must not be confused with image analysis.

These tests exercise the existing HTTP ingress contract with synthetic
non-sensitive payloads. They do not implement OCR, image or medical checks.
"""
from fastapi.testclient import TestClient
import pytest

from guardoath.app import create_app


@pytest.mark.parametrize("media_type", [
    "image/png", "image/jpeg", "image/webp", "application/dicom",
    "application/pdf", "multipart/form-data; boundary=demo",
    "audio/wav", "video/mp4"])
def test_uc031_032_034_035_036_binary_media_is_not_analyzed(media_type):
    client = TestClient(create_app())
    response = client.post("/v1/checks/input",
                           content=b"synthetic-not-a-real-document",
                           headers={"Content-Type": media_type})
    assert response.status_code == 415
    assert response.json()["detail"] == "A JSON content type is required"


@pytest.mark.parametrize("media_type", [
    "application/json; charset=iso-8859-1",
    "application/json; charset=utf-16"])
def test_document_json_ingress_requires_utf8(media_type):
    client = TestClient(create_app())
    response = client.post("/v1/checks/input",
                           content=b'{"content":"demo"}',
                           headers={"Content-Type": media_type})
    assert response.status_code == 415
    assert response.json()["detail"] == "JSON requests must use UTF-8"
