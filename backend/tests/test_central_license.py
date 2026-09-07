import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.services import central_license


@pytest.fixture()
def isolated_state(monkeypatch):
    state_file = Path("tests/.runtime-central-license.json")
    state_file.unlink(missing_ok=True)
    monkeypatch.setattr(central_license, "STATE_DIR", state_file.parent)
    monkeypatch.setattr(central_license, "STATE_FILE", state_file)
    yield state_file
    state_file.unlink(missing_ok=True)


def signed_body(state: dict, status: str = "active") -> tuple[dict, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.urlsafe_b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    decision = {
        "version": 1,
        "license_key": state["license_key"],
        "installation_id": state["installation_id"],
        "status": status,
        "server_time": central_license.utc_now().isoformat(),
        "blocked_until": None,
        "can_reclaim": False,
        "installations": [],
    }
    canonical = json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()
    signature = base64.urlsafe_b64encode(private_key.sign(canonical)).decode()
    return {"decision": decision, "signature": signature}, public_key


def test_signed_decision_is_persisted(isolated_state):
    state = central_license.default_state()
    state.update(
        {
            "license_key": "FPOS-AAAA-BBBB-CCCC-DDDD",
            "installation_token": "token",
            "license_server_url": "https://license.example.com",
        }
    )
    body, public_key = signed_body(state)
    state["public_key_b64"] = public_key

    result = central_license.apply_response(state, body)

    assert result["status"] == "active"
    assert isolated_state.exists()
    assert central_license.load_state()["last_check_date_wita"] is None


def test_modified_decision_is_rejected(isolated_state):
    state = central_license.default_state()
    state["license_key"] = "FPOS-AAAA-BBBB-CCCC-DDDD"
    body, public_key = signed_body(state)
    state["public_key_b64"] = public_key
    body["decision"]["status"] = "active-modified"

    with pytest.raises(ValueError, match="[Tt]anda tangan"):
        central_license.apply_response(state, body)


def test_offline_grace_blocks_after_72_hours(isolated_state):
    state = central_license.default_state()
    state.update(
        {
            "license_server_url": "https://license.example.com",
            "license_key": "FPOS-AAAA-BBBB-CCCC-DDDD",
            "installation_token": "token",
            "last_successful_check_at": (
                central_license.utc_now() - central_license.OFFLINE_GRACE - timedelta(minutes=1)
            ).isoformat(),
            "decision": {"status": "active"},
        }
    )
    assert central_license.public_status(state)["status"] == "offline_blocked"


def test_production_server_url_requires_https(monkeypatch):
    monkeypatch.setattr(central_license.settings, "LICENSE_ALLOW_HTTP", False)
    with pytest.raises(ValueError, match="HTTPS"):
        central_license.validate_server_url("http://license.example.com")


def test_configured_server_requires_activation(isolated_state):
    state = central_license.default_state()
    state.update(
        {
            "license_server_url": "https://license.example.com",
            "public_key_b64": base64.urlsafe_b64encode(b"x" * 32).decode(),
        }
    )
    assert central_license.public_status(state)["status"] == "setup_required"
