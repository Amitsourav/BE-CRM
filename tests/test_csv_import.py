async def test_upload_csv(admin_client):
    content = b"Full Name,Email,Phone\nJohn Doe,john@test.com,+911111111111\n"
    resp = await admin_client.post(
        "/api/v1/csv/upload",
        files={"file": ("test.csv", content, "text/csv")},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["file_name"] == "test.csv"
    assert data["total_rows"] == 1
    assert data["status"] == "uploaded"


async def test_upload_csv_no_headers(admin_client):
    content = b""
    resp = await admin_client.post(
        "/api/v1/csv/upload",
        files={"file": ("empty.csv", content, "text/csv")},
    )
    assert resp.status_code == 400


async def test_preview_csv(admin_client):
    content = b"Full Name,Email\nJane Doe,jane@test.com\nBob Smith,bob@test.com\n"
    upload_resp = await admin_client.post(
        "/api/v1/csv/upload",
        files={"file": ("preview.csv", content, "text/csv")},
    )
    import_id = upload_resp.json()["id"]

    resp = await admin_client.post(f"/api/v1/csv/{import_id}/preview")
    assert resp.status_code == 200
    data = resp.json()
    assert "raw_headers" in data
    assert "suggested_mapping" in data


async def test_process_csv(admin_client, sample_lead_source):
    content = b"Full Name,Email,Phone\nProcess Lead,process@test.com,+912222222222\n"
    upload_resp = await admin_client.post(
        "/api/v1/csv/upload",
        files={"file": ("process.csv", content, "text/csv")},
    )
    import_id = upload_resp.json()["id"]

    resp = await admin_client.post(f"/api/v1/csv/{import_id}/process", json={
        "column_mapping": {
            "Full Name": "full_name",
            "Email": "email",
            "Phone": "phone",
        },
        "lead_source_id": str(sample_lead_source.id),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["success_count"] >= 1


async def test_process_csv_missing_full_name(admin_client):
    content = b"Email,Phone\nnoname@test.com,+913333333333\n"
    upload_resp = await admin_client.post(
        "/api/v1/csv/upload",
        files={"file": ("noname.csv", content, "text/csv")},
    )
    import_id = upload_resp.json()["id"]

    resp = await admin_client.post(f"/api/v1/csv/{import_id}/process", json={
        "column_mapping": {
            "Email": "email",
            "Phone": "phone",
        },
    })
    assert resp.status_code == 200
    assert resp.json()["failure_count"] >= 1


async def test_get_import_status(admin_client):
    content = b"Full Name\nStatus Lead\n"
    upload_resp = await admin_client.post(
        "/api/v1/csv/upload",
        files={"file": ("status.csv", content, "text/csv")},
    )
    import_id = upload_resp.json()["id"]

    resp = await admin_client.get(f"/api/v1/csv/{import_id}/status")
    assert resp.status_code == 200
    assert resp.json()["id"] == import_id


async def test_get_import_history_admin_only(admin_client):
    resp = await admin_client.get("/api/v1/csv/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_import_history_agent_forbidden(agent_client):
    resp = await agent_client.get("/api/v1/csv/history")
    assert resp.status_code == 403


async def test_download_template(admin_client):
    resp = await admin_client.get("/api/v1/csv/template")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    assert "Full Name" in resp.text
