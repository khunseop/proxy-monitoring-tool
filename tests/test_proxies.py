"""프록시 CRUD API 통합 테스트"""

PROXY_PAYLOAD = {
    "host": "192.168.1.1",
    "username": "admin",
    "password": "secret123",
    "port": 22,
}


def test_create_proxy(client):
    resp = client.post("/api/proxies", json=PROXY_PAYLOAD)
    assert resp.status_code in (200, 201)
    body = resp.json()
    assert body["host"] == "192.168.1.1"
    assert "id" in body
    # 비밀번호는 응답에 포함되지 않아야 함
    assert "password" not in body


def test_create_duplicate_proxy_returns_409(client):
    client.post("/api/proxies", json=PROXY_PAYLOAD)
    resp = client.post("/api/proxies", json=PROXY_PAYLOAD)
    assert resp.status_code == 409


def test_list_proxies(client):
    resp = client.get("/api/proxies")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body or isinstance(body, list)


def test_get_proxy(client):
    create_resp = client.post("/api/proxies", json={**PROXY_PAYLOAD, "host": "10.0.0.1"})
    assert create_resp.status_code in (200, 201)
    proxy_id = create_resp.json()["id"]

    resp = client.get(f"/api/proxies/{proxy_id}")
    assert resp.status_code == 200
    assert resp.json()["host"] == "10.0.0.1"


def test_get_nonexistent_proxy_returns_404(client):
    resp = client.get("/api/proxies/99999")
    assert resp.status_code == 404


def test_delete_proxy(client):
    create_resp = client.post("/api/proxies", json={**PROXY_PAYLOAD, "host": "10.0.0.2"})
    proxy_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/proxies/{proxy_id}")
    assert del_resp.status_code in (200, 204)

    get_resp = client.get(f"/api/proxies/{proxy_id}")
    assert get_resp.status_code == 404


def test_invalid_host_returns_422(client):
    resp = client.post("/api/proxies", json={**PROXY_PAYLOAD, "host": "not a valid host!!"})
    assert resp.status_code == 422


# --- 일괄 등록 (POST /api/proxies/bulk) ---

def _bulk_item(host, **extra):
    return {"host": host, "username": "admin", "password": "secret123", **extra}


def test_bulk_create_all_created(client):
    resp = client.post("/api/proxies/bulk", json=[_bulk_item("10.1.0.1"), _bulk_item("10.1.0.2")])
    assert resp.status_code == 201
    results = resp.json()
    assert len(results) == 2
    for i, r in enumerate(results):
        assert r["index"] == i
        assert r["status"] == "created"
        assert r["id"] is not None


def test_bulk_create_duplicate_host(client):
    create_resp = client.post("/api/proxies", json={**PROXY_PAYLOAD, "host": "10.1.1.1"})
    existing_id = create_resp.json()["id"]

    resp = client.post("/api/proxies/bulk", json=[_bulk_item("10.1.1.1"), _bulk_item("10.1.1.2")])
    assert resp.status_code == 201
    results = resp.json()
    assert results[0]["status"] == "duplicate"
    assert results[0]["id"] == existing_id
    # 중복 행이 있어도 나머지 행은 정상 처리
    assert results[1]["status"] == "created"


def test_bulk_create_with_group_name(client):
    group_resp = client.post("/api/proxy-groups", json={"name": "서울센터"})
    assert group_resp.status_code == 201
    group_id = group_resp.json()["id"]

    # 대소문자 무시 매칭 확인을 위해 영문 그룹도 생성
    client.post("/api/proxy-groups", json={"name": "SeoulDC"})

    resp = client.post("/api/proxies/bulk", json=[
        _bulk_item("10.1.2.1", group_name="서울센터"),
        _bulk_item("10.1.2.2", group_name="seouldc"),
    ])
    results = resp.json()
    assert results[0]["status"] == "created"
    assert results[1]["status"] == "created"

    proxy = client.get(f"/api/proxies/{results[0]['id']}").json()
    assert proxy["group_id"] == group_id


def test_bulk_create_group_not_found(client):
    resp = client.post("/api/proxies/bulk", json=[
        _bulk_item("10.1.3.1", group_name="없는그룹"),
        _bulk_item("10.1.3.2"),
    ])
    results = resp.json()
    assert results[0]["status"] == "error"
    assert "not found" in results[0]["detail"].lower()
    # 그룹 오류 행이 있어도 나머지 행은 정상 처리
    assert results[1]["status"] == "created"


def test_bulk_create_validation_errors(client):
    resp = client.post("/api/proxies/bulk", json=[
        {"host": "10.1.4.1", "username": "admin"},  # password 누락
        _bulk_item("not a valid host!!"),  # host 형식 오류
    ])
    results = resp.json()
    assert results[0]["index"] == 0
    assert results[0]["status"] == "error"
    assert results[1]["index"] == 1
    assert results[1]["status"] == "error"


def test_bulk_create_mixed_results(client):
    client.post("/api/proxies", json={**PROXY_PAYLOAD, "host": "10.1.5.1"})

    resp = client.post("/api/proxies/bulk", json=[
        _bulk_item("10.1.5.2"),               # created
        _bulk_item("10.1.5.1"),               # duplicate
        {"host": "10.1.5.3", "username": "admin"},  # error (password 누락)
    ])
    results = resp.json()
    assert [r["status"] for r in results] == ["created", "duplicate", "error"]
    assert [r["index"] for r in results] == [0, 1, 2]
