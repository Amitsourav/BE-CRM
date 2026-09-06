import uuid


# ── CREATE ─────────────────────────────────────────────────────────────

async def test_create_lead_full_fields(admin_client, sample_lead_source, agent_user):
    resp = await admin_client.post("/api/v1/leads", json={
        "full_name": "John Doe",
        "email": "john@test.com",
        "phone": "+911111111111",
        "city": "Delhi",
        "state": "Delhi",
        "country": "India",
        "highest_qualification": "B.Tech",
        "stream": "CS",
        "passing_year": 2024,
        "lead_source_id": str(sample_lead_source.id),
        "assigned_agent_id": str(agent_user.id),
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["full_name"] == "John Doe"
    assert data["current_stage"] == "lead"


async def test_create_lead_minimal(admin_client):
    resp = await admin_client.post("/api/v1/leads", json={"full_name": "Minimal Lead"})
    assert resp.status_code == 201
    assert resp.json()["full_name"] == "Minimal Lead"


async def test_create_lead_returns_201(admin_client):
    resp = await admin_client.post("/api/v1/leads", json={"full_name": "Status Check"})
    assert resp.status_code == 201


# ── LIST ───────────────────────────────────────────────────────────────

async def test_list_leads_admin_sees_all(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] >= 1


async def test_list_leads_agent_sees_own_only(agent_client, sample_lead, sample_lead_unassigned):
    resp = await agent_client.get("/api/v1/leads")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["assigned_agent_id"] == str(sample_lead.assigned_agent_id)


async def test_list_leads_pagination(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads", params={"page": 1, "page_size": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 1
    assert "total_pages" in data


async def test_list_leads_filter_by_stage(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads", params={"stage": "lead"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["current_stage"] == "lead"


# ── GET ────────────────────────────────────────────────────────────────

async def test_get_lead_by_id(admin_client, sample_lead):
    resp = await admin_client.get(f"/api/v1/leads/{sample_lead.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(sample_lead.id)


async def test_get_lead_not_found(admin_client):
    fake_id = uuid.uuid4()
    resp = await admin_client.get(f"/api/v1/leads/{fake_id}")
    assert resp.status_code == 404


async def test_get_lead_agent_forbidden_other_agent(
    agent_client, db_session, agent2_user
):
    from app.models.lead import Lead
    from app.core.constants import LeadStage

    lead = Lead(
        company_id=agent2_user.company_id,
        full_name="Other Agent Lead",
        phone="+919999999999",
        current_stage=LeadStage.LEAD,
        assigned_agent_id=agent2_user.id,
        created_by=agent2_user.id,
    )
    db_session.add(lead)
    await db_session.flush()

    resp = await agent_client.get(f"/api/v1/leads/{lead.id}")
    assert resp.status_code == 403


# ── SEARCH ─────────────────────────────────────────────────────────────

async def test_search_leads_by_name(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads/search", params={"q": "Test Lead"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


async def test_search_leads_by_email(admin_client, sample_lead):
    # sample_lead email is like "lead-abcdef@example.com" — search by the actual email
    resp = await admin_client.get("/api/v1/leads/search", params={"q": sample_lead.email})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


async def test_search_leads_by_phone(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads/search", params={"q": "9876543210"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


# ── UPDATE ─────────────────────────────────────────────────────────────

async def test_update_lead_partial(admin_client, sample_lead):
    resp = await admin_client.put(f"/api/v1/leads/{sample_lead.id}", json={"city": "Bangalore"})
    assert resp.status_code == 200
    assert resp.json()["city"] == "Bangalore"


async def test_update_lead_agent_can_update_own(agent_client, sample_lead):
    resp = await agent_client.put(f"/api/v1/leads/{sample_lead.id}", json={"notes": "updated"})
    assert resp.status_code == 200


async def test_update_lead_agent_cannot_update_other(
    agent_client, db_session, agent2_user
):
    from app.models.lead import Lead
    from app.core.constants import LeadStage

    lead = Lead(
        company_id=agent2_user.company_id,
        full_name="Other Lead",
        phone="+919999999998",
        current_stage=LeadStage.LEAD,
        assigned_agent_id=agent2_user.id,
        created_by=agent2_user.id,
    )
    db_session.add(lead)
    await db_session.flush()

    resp = await agent_client.put(f"/api/v1/leads/{lead.id}", json={"notes": "hack"})
    assert resp.status_code == 403


# ── DELETE ─────────────────────────────────────────────────────────────

async def test_delete_lead_admin_only(admin_client, sample_lead_unassigned):
    # Use lead without stage logs to avoid FK cascade issue with ORM delete
    resp = await admin_client.delete(f"/api/v1/leads/{sample_lead_unassigned.id}")
    assert resp.status_code == 200


async def test_delete_lead_agent_forbidden(agent_client, sample_lead):
    resp = await agent_client.delete(f"/api/v1/leads/{sample_lead.id}")
    assert resp.status_code == 403


async def test_delete_lead_not_found(admin_client):
    resp = await admin_client.delete(f"/api/v1/leads/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── TIMELINE / CALLS / TASKS ──────────────────────────────────────────

async def test_get_timeline(agent_client, sample_lead):
    resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}/timeline")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_lead_calls(agent_client, sample_lead):
    resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}/calls")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_lead_tasks(agent_client, sample_lead):
    resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── ASSIGN ─────────────────────────────────────────────────────────────

async def test_assign_lead_admin_only(admin_client, sample_lead, agent_user):
    resp = await admin_client.post(
        f"/api/v1/leads/{sample_lead.id}/assign",
        json={"agent_id": str(agent_user.id)},
    )
    assert resp.status_code == 200


async def test_assign_lead_agent_forbidden(agent_client, sample_lead, agent_user):
    resp = await agent_client.post(
        f"/api/v1/leads/{sample_lead.id}/assign",
        json={"agent_id": str(agent_user.id)},
    )
    assert resp.status_code == 403


async def test_assign_lead_invalid_agent(admin_client, sample_lead):
    resp = await admin_client.post(
        f"/api/v1/leads/{sample_lead.id}/assign",
        json={"agent_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 400


# ── BULK ASSIGN ────────────────────────────────────────────────────────

async def test_bulk_assign_admin_only(admin_client, sample_lead, agent_user):
    resp = await admin_client.post("/api/v1/leads/bulk-assign", json={
        "lead_ids": [str(sample_lead.id)],
        "agent_id": str(agent_user.id),
    })
    assert resp.status_code == 200
    assert "message" in resp.json()


async def test_bulk_assign_agent_forbidden(agent_client, sample_lead, agent_user):
    resp = await agent_client.post("/api/v1/leads/bulk-assign", json={
        "lead_ids": [str(sample_lead.id)],
        "agent_id": str(agent_user.id),
    })
    assert resp.status_code == 403


# ── SOURCES ────────────────────────────────────────────────────────────

async def test_list_lead_sources(admin_client, sample_lead_source):
    resp = await admin_client.get("/api/v1/leads/sources/list")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


async def test_create_lead_source_admin_only(admin_client):
    resp = await admin_client.post("/api/v1/leads/sources", json={
        "name": f"New Source {uuid.uuid4().hex[:6]}",
        "source_type": "manual",
    })
    assert resp.status_code == 201


async def test_create_lead_source_agent_forbidden(agent_client):
    resp = await agent_client.post("/api/v1/leads/sources", json={
        "name": "Agent Source",
        "source_type": "manual",
    })
    assert resp.status_code == 403


# ── PHONE / EMAIL IDENTITY ON UPDATE ───────────────────────────────────
#
# The whole lead-identity model is one lead per phone per tenant. Create
# enforced it; PUT /leads/{id} did not, so the lead-edit form could
# silently produce two live leads for one person. These pin the fix.
#
# Uses only admin_client (no agent_* fixtures — those are broken by the
# stale UserRole.PRE_COUNSELLOR reference in conftest).

def _phone() -> str:
    return f"+91{uuid.uuid4().int % 10**10:010d}"


async def _mk_lead(client, **kw) -> dict:
    body = {"full_name": "Identity Test", **kw}
    resp = await client.post("/api/v1/leads", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_update_lead_normalises_phone(admin_client):
    """A trunk-prefixed edit must land in the same +91 form create uses,
    or it dodges the unique index entirely."""
    lead = await _mk_lead(admin_client, phone=_phone())
    national = f"0{uuid.uuid4().int % 10**10:010d}"

    resp = await admin_client.put(
        f"/api/v1/leads/{lead['id']}", json={"phone": national}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["phone"] == f"+91{national[1:]}"


async def test_update_lead_rejects_duplicate_phone_after_normalisation(admin_client):
    """The exact bug: editing lead B to the 0-prefixed form of lead A's
    number used to create a second live lead for one person."""
    digits = f"{uuid.uuid4().int % 10**10:010d}"
    existing = await _mk_lead(admin_client, phone=f"+91{digits}")
    other = await _mk_lead(admin_client, phone=_phone())

    resp = await admin_client.put(
        f"/api/v1/leads/{other['id']}", json={"phone": f"0{digits}"}
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["existing_lead_id"] == existing["id"]
    assert body["duplicate_field"] == "phone"


async def test_update_lead_rejects_exact_duplicate_phone(admin_client):
    """Previously an uncaught IntegrityError → 500. Must be a clean 400."""
    phone = _phone()
    existing = await _mk_lead(admin_client, phone=phone)
    other = await _mk_lead(admin_client, phone=_phone())

    resp = await admin_client.put(
        f"/api/v1/leads/{other['id']}", json={"phone": phone}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["existing_lead_id"] == existing["id"]


async def test_update_lead_may_resave_its_own_phone(admin_client):
    """Self-collision guard — re-saving an unchanged phone must not 400."""
    phone = _phone()
    lead = await _mk_lead(admin_client, phone=phone)

    resp = await admin_client.put(
        f"/api/v1/leads/{lead['id']}", json={"phone": phone, "city": "Pune"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["city"] == "Pune"


async def test_update_lead_rejects_duplicate_email_case_insensitively(admin_client):
    """The unique index is on lower(email); the check must match it."""
    local = uuid.uuid4().hex[:8]
    existing = await _mk_lead(admin_client, email=f"{local}@example.com")
    other = await _mk_lead(admin_client, email=f"other-{local}@example.com")

    resp = await admin_client.put(
        f"/api/v1/leads/{other['id']}", json={"email": f"{local.upper()}@EXAMPLE.COM"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["existing_lead_id"] == existing["id"]
    assert resp.json()["duplicate_field"] == "email"


async def test_update_lead_can_clear_phone(admin_client):
    """Clearing can't collide — must not be caught by the new gate."""
    lead = await _mk_lead(admin_client, phone=_phone())
    resp = await admin_client.put(
        f"/api/v1/leads/{lead['id']}", json={"phone": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["phone"] is None


async def test_create_lead_rejects_duplicate_email_case_insensitively(admin_client):
    """The unique index is on lower(email), so an exact-match service check
    let differing case through and the collision surfaced from the index as
    a 500. Must be the same readable 400 an exact-case duplicate gets —
    a 500 is indistinguishable from an outage to a retrying client."""
    local = uuid.uuid4().hex[:8]
    existing = await _mk_lead(admin_client, email=f"{local}@example.com")

    resp = await admin_client.post("/api/v1/leads", json={
        "full_name": "Case Variant",
        "email": f"{local.upper()}@EXAMPLE.COM",
    })
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["existing_lead_id"] == existing["id"]
    assert body["duplicate_field"] == "email"


async def test_create_lead_rejects_exact_duplicate_email(admin_client):
    """Guard the path that already worked, so the lower() change can't
    silently break exact-match detection."""
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    existing = await _mk_lead(admin_client, email=email)

    resp = await admin_client.post("/api/v1/leads", json={
        "full_name": "Exact Dup", "email": email,
    })
    assert resp.status_code == 400, resp.text
    assert resp.json()["existing_lead_id"] == existing["id"]


async def test_create_lead_allows_distinct_emails(admin_client):
    """The lower() comparison must not over-match genuinely different
    addresses that share a prefix."""
    local = uuid.uuid4().hex[:8]
    await _mk_lead(admin_client, email=f"{local}@example.com")

    resp = await admin_client.post("/api/v1/leads", json={
        "full_name": "Different Person", "email": f"{local}x@example.com",
    })
    assert resp.status_code == 201, resp.text


# ── Format-insensitive phone dedup ─────────────────────────────────────
#
# The duplicate check compared a NORMALISED incoming phone against the
# RAW stored column, so any row saved in a non-canonical format was
# invisible to it: creating "+917004428198" when "7004428198" was already
# on file produced a second lead for one person. Live data had 5 such
# pairs. These pin the format-insensitive match.

async def _seed_raw_phone(db_session, company_id, raw_phone: str, name="Legacy Row"):
    """Insert a lead with a deliberately un-normalised phone, bypassing
    the service so it lands exactly as legacy/CSV rows did."""
    from app.models.lead import Lead
    lead = Lead(
        company_id=company_id, full_name=name, phone=raw_phone,
        current_stage="created",
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


async def test_create_detects_duplicate_of_bare_10_digit_row(admin_client, db_session, admin_user):
    """The exact reported bug: existing row stored bare, new lead sent
    with +91."""
    digits = f"{uuid.uuid4().int % 10**10:010d}"
    legacy = await _seed_raw_phone(db_session, admin_user.company_id, digits)

    resp = await admin_client.post("/api/v1/leads", json={
        "full_name": "Same Person", "phone": f"+91{digits}",
    })
    assert resp.status_code == 400, resp.text
    assert resp.json()["existing_lead_id"] == str(legacy.id)


async def test_create_detects_duplicate_of_spaced_row(admin_client, db_session, admin_user):
    """Live data contained '80556 29775' — spaces must not defeat it."""
    digits = f"{uuid.uuid4().int % 10**10:010d}"
    legacy = await _seed_raw_phone(db_session, admin_user.company_id, f"{digits[:5]} {digits[5:]}")

    resp = await admin_client.post("/api/v1/leads", json={
        "full_name": "Same Person", "phone": f"+91{digits}",
    })
    assert resp.status_code == 400, resp.text
    assert resp.json()["existing_lead_id"] == str(legacy.id)


async def test_create_detects_duplicate_of_zero_prefixed_row(admin_client, db_session, admin_user):
    digits = f"{uuid.uuid4().int % 10**10:010d}"
    legacy = await _seed_raw_phone(db_session, admin_user.company_id, f"0{digits}")

    resp = await admin_client.post("/api/v1/leads", json={
        "full_name": "Same Person", "phone": f"+91{digits}",
    })
    assert resp.status_code == 400, resp.text
    assert resp.json()["existing_lead_id"] == str(legacy.id)


async def test_update_detects_duplicate_of_bare_10_digit_row(admin_client, db_session, admin_user):
    """Same hole existed on the edit path."""
    digits = f"{uuid.uuid4().int % 10**10:010d}"
    legacy = await _seed_raw_phone(db_session, admin_user.company_id, digits)
    other = await _mk_lead(admin_client, phone=_phone())

    resp = await admin_client.put(
        f"/api/v1/leads/{other['id']}", json={"phone": f"+91{digits}"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["existing_lead_id"] == str(legacy.id)


async def test_distinct_numbers_still_create(admin_client):
    """The 10-digit comparison must not collapse genuinely different
    numbers — a false duplicate blocks a real lead."""
    a = f"{uuid.uuid4().int % 10**10:010d}"
    b = f"{(int(a) + 1) % 10**10:010d}"
    await _mk_lead(admin_client, phone=f"+91{a}")

    resp = await admin_client.post("/api/v1/leads", json={
        "full_name": "Different Person", "phone": f"+91{b}",
    })
    assert resp.status_code == 201, resp.text
