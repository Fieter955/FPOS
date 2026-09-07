import base64


ADMIN = ("fieter", "test-password")


def device(installation_id: str, hostname: str) -> dict:
    return {
        "license_key": "FPOS-AAAA-BBBB-CCCC-DDDD",
        "installation_id": installation_id,
        "hostname": hostname,
        "hardware_fingerprint": f"hardware-{installation_id}",
    }


def test_single_server_then_conflict_and_selection(client):
    created = client.post(
        "/api/admin/licenses",
        auth=ADMIN,
        json={"license_key": "FPOS-AAAA-BBBB-CCCC-DDDD", "owner_name": "Toko Test"},
    )
    assert created.status_code == 200

    first = client.post("/api/v1/activate", json=device("installation-0001", "SERVER-A"))
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["decision"]["status"] == "active"
    assert first_body["installation_token"]

    second = client.post("/api/v1/activate", json=device("installation-0002", "SERVER-B"))
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["decision"]["conflict_pending"] is True

    first_check = device("installation-0001", "SERVER-A") | {
        "installation_token": first_body["installation_token"]
    }
    conflict = client.post("/api/v1/check", json=first_check)
    assert conflict.json()["decision"]["status"] == "conflict"

    selection = client.post(
        "/api/v1/select-server",
        json=first_check
        | {
            "selected_installation_id": "installation-0001",
            "confirmation_hostname": "SERVER-A",
        },
    )
    assert selection.status_code == 200
    assert selection.json()["decision"]["status"] == "active"

    second_check = device("installation-0002", "SERVER-B") | {
        "installation_token": second_body["installation_token"]
    }
    blocked = client.post("/api/v1/check", json=second_check)
    assert blocked.json()["decision"]["status"] == "blocked"
    assert blocked.json()["decision"]["blocked_until"]


def test_hostname_confirmation_is_exact(client):
    client.post(
        "/api/admin/licenses",
        auth=ADMIN,
        json={"license_key": "FPOS-AAAA-BBBB-CCCC-DDDD", "owner_name": "Toko Test"},
    )
    first = client.post("/api/v1/activate", json=device("installation-0001", "SERVER-A")).json()
    client.post("/api/v1/activate", json=device("installation-0002", "SERVER-B"))
    response = client.post(
        "/api/v1/select-server",
        json=device("installation-0001", "SERVER-A")
        | {
            "installation_token": first["installation_token"],
            "selected_installation_id": "installation-0001",
            "confirmation_hostname": "server-a",
        },
    )
    assert response.status_code == 422


def test_decision_has_ed25519_signature(client):
    client.post(
        "/api/admin/licenses",
        auth=ADMIN,
        json={"license_key": "FPOS-AAAA-BBBB-CCCC-DDDD", "owner_name": "Toko Test"},
    )
    body = client.post("/api/v1/activate", json=device("installation-0001", "SERVER-A")).json()
    assert len(base64.urlsafe_b64decode(body["signature"])) == 64


def test_admin_can_revoke_license(client):
    client.post(
        "/api/admin/licenses",
        auth=ADMIN,
        json={"license_key": "FPOS-AAAA-BBBB-CCCC-DDDD", "owner_name": "Toko Test"},
    )
    revoked = client.post(
        "/api/admin/licenses/FPOS-AAAA-BBBB-CCCC-DDDD/status",
        auth=ADMIN,
        json={"status": "revoked"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    activation = client.post("/api/v1/activate", json=device("installation-0001", "SERVER-A"))
    assert activation.status_code == 404


def test_admin_can_force_server_transfer(client):
    client.post(
        "/api/admin/licenses",
        auth=ADMIN,
        json={"license_key": "FPOS-AAAA-BBBB-CCCC-DDDD", "owner_name": "Toko Test"},
    )
    first = client.post("/api/v1/activate", json=device("installation-0001", "SERVER-A")).json()
    second = client.post("/api/v1/activate", json=device("installation-0002", "SERVER-B")).json()

    transferred = client.post(
        "/api/admin/licenses/FPOS-AAAA-BBBB-CCCC-DDDD/select/installation-0002",
        auth=ADMIN,
    )
    assert transferred.status_code == 200

    first_status = client.post(
        "/api/v1/check",
        json=device("installation-0001", "SERVER-A")
        | {"installation_token": first["installation_token"]},
    ).json()["decision"]["status"]
    second_status = client.post(
        "/api/v1/check",
        json=device("installation-0002", "SERVER-B")
        | {"installation_token": second["installation_token"]},
    ).json()["decision"]["status"]
    assert (first_status, second_status) == ("blocked", "active")
