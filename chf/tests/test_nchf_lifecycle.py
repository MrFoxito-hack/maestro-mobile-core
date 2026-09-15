from conftest import charging_request


def test_create_update_release_and_accounting(client, admin, account):
    create = client.post(
        "/nchf-convergedcharging/v3/chargingdata", json=charging_request(0)
    )
    assert create.status_code == 201
    assert create.headers["location"].startswith(
        "/nchf-convergedcharging/v3/chargingdata/"
    )
    assert create.json()["multipleUnitInformation"][0]["grantedUnit"]["totalVolume"] == 1000
    ref = create.headers["location"].rsplit("/", 1)[-1]

    update = client.post(
        f"/nchf-convergedcharging/v3/chargingdata/{ref}/update",
        json=charging_request(1, requested=1000, used=800),
    )
    assert update.status_code == 200
    assert update.json()["multipleUnitInformation"][0]["grantedUnit"]["totalVolume"] == 1000

    release = client.post(
        f"/nchf-convergedcharging/v3/chargingdata/{ref}/release",
        json=charging_request(2, requested=0, used=200),
    )
    assert release.status_code == 204

    stored = admin.get("/admin/v1/accounts/imsi-999700000000001").json()
    assert stored["consumed_bytes"] == 1000
    assert stored["reserved_bytes"] == 0


def test_two_sessions_cannot_over_reserve(client, account):
    first = client.post(
        "/nchf-convergedcharging/v3/chargingdata",
        json=charging_request(0, requested=2000),
    )
    second = client.post(
        "/nchf-convergedcharging/v3/chargingdata",
        json=charging_request(0, requested=2000, charging_id=101),
    )
    assert first.json()["multipleUnitInformation"][0]["grantedUnit"]["totalVolume"] == 2000
    assert second.json()["multipleUnitInformation"][0]["grantedUnit"]["totalVolume"] == 500


def test_duplicate_update_is_idempotent(client, admin, account):
    create = client.post(
        "/nchf-convergedcharging/v3/chargingdata", json=charging_request(0)
    )
    ref = create.headers["location"].rsplit("/", 1)[-1]
    payload = charging_request(1, requested=500, used=400)
    first = client.post(
        f"/nchf-convergedcharging/v3/chargingdata/{ref}/update", json=payload
    )
    retry = client.post(
        f"/nchf-convergedcharging/v3/chargingdata/{ref}/update", json=payload
    )
    assert retry.status_code == 200
    assert retry.json() == first.json()
    stored = admin.get("/admin/v1/accounts/imsi-999700000000001").json()
    assert stored["consumed_bytes"] == 400


def test_duplicate_create_returns_same_resource_without_double_reservation(client, admin, account):
    payload = charging_request(0, requested=1000)
    first = client.post("/nchf-convergedcharging/v3/chargingdata", json=payload)
    retry = client.post("/nchf-convergedcharging/v3/chargingdata", json=payload)
    assert first.status_code == retry.status_code == 201
    assert first.headers["location"] == retry.headers["location"]
    stored = admin.get("/admin/v1/accounts/imsi-999700000000001").json()
    assert stored["reserved_bytes"] == 1000


def test_same_sequence_with_changed_payload_is_rejected(client, account):
    create = client.post(
        "/nchf-convergedcharging/v3/chargingdata", json=charging_request(0)
    )
    ref = create.headers["location"].rsplit("/", 1)[-1]
    client.post(
        f"/nchf-convergedcharging/v3/chargingdata/{ref}/update",
        json=charging_request(1, used=100),
    )
    conflict = client.post(
        f"/nchf-convergedcharging/v3/chargingdata/{ref}/update",
        json=charging_request(1, used=200),
    )
    assert conflict.status_code == 409
    assert conflict.json()["cause"] == "SEQUENCE_CONFLICT"


def test_unknown_subscriber_fails_closed(client):
    response = client.post(
        "/nchf-convergedcharging/v3/chargingdata", json=charging_request(0)
    )
    assert response.status_code == 403
    assert response.json()["cause"] == "USER_UNKNOWN"
