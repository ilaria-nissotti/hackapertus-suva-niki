from test_flow_review_app import case, client, page  # noqa: F401


def test_triage_feedback_validates_and_updates(client, case):
    assert client.post("/api/triage-feedback", json={"case": case, "status": "Maybe"}).status_code == 400
    assert client.post("/api/triage-feedback", json={"case": "8C_9/2000@2000-01-01",
                                                     "status": "Review"}).status_code == 404
    client.post("/api/triage-feedback", json={"case": case, "status": "Review"})
    client.post("/api/triage-feedback", json={"case": case, "status": "Low relevance", "reason": "Nur Kosten."})
    feedback = page(client, case=case)["selected"]["triage_feedback"]
    assert (feedback["status"], feedback["reason"]) == ("Low relevance", "Nur Kosten.")
    assert feedback["rule_points"] is not None
