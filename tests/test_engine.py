import json

from app.ai.providers import ProviderError
from tests.conftest import FakeProvider, interpretation


def test_new_goal_is_created_from_hinglish(client, use_provider):
    use_provider(FakeProvider(answers=[interpretation()]))
    res = client.post("/api/message", json={"text": "Meri website hai but customer nahi aa rhe."})
    assert res.status_code == 200
    data = res.json()
    assert data["relation"] == "new_goal"
    goal = data["goal"]
    assert goal["original_request"] == "Meri website hai but customer nahi aa rhe."
    assert goal["detected_language"] == "Hinglish"
    assert goal["known_facts"] == ["Has a website"]
    assert goal["missing_info"][0]["question"] == "What does your business sell?"
    assert goal["next_action"]["type"] == "ask"
    assert data["reply"].startswith("Samajh gaya")


def test_follow_up_updates_same_goal_and_keeps_original_request(client, use_provider):
    fake = use_provider(FakeProvider(answers=[
        interpretation(),
        interpretation(relation="update_goal", goal_ref=1, known_facts=["Has a website", "Sells furniture in Panchkula"],
                       missing_info=[], confidence=0.8,
                       next_action={"type": "investigate", "description": "Check the website and Google listing"}),
    ]))
    client.post("/api/message", json={"text": "Meri website hai but customer nahi aa rhe."})
    res = client.post("/api/message", json={"text": "furniture bechte hain Panchkula mein"})
    data = res.json()
    assert data["relation"] == "update_goal"
    assert data["goal"]["id"] == 1
    assert data["goal"]["original_request"] == "Meri website hai but customer nahi aa rhe."
    assert "Sells furniture in Panchkula" in data["goal"]["known_facts"]
    assert data["goal"]["missing_info"] == []
    assert data["goal"]["updates"] == 2
    assert len(data["goals"]) == 1
    # the existing goal state was given to the AI, not just the chat
    assert '"id": 1' in fake.prompts[1] and "Has a website" in fake.prompts[1]


def test_separate_problem_becomes_its_own_goal(client, use_provider):
    use_provider(FakeProvider(answers=[
        interpretation(),
        interpretation(relation="new_goal", title="Car not starting", detected_language="English",
                       known_facts=["Car does not start"]),
    ]))
    client.post("/api/message", json={"text": "Mera business acha nahi chal raha."})
    data = client.post("/api/message", json={"text": "My car is not starting."}).json()
    assert data["goal"]["id"] == 2
    assert data["goal"]["title"] == "Car not starting"
    assert len(data["goals"]) == 2


def test_goal_ref_to_someone_elses_goal_is_never_updated(client, use_provider):
    use_provider(FakeProvider(answers=[
        interpretation(),
        interpretation(relation="update_goal", goal_ref=1, title="Hijack"),
    ]))
    client.post("/api/message", json={"text": "Mera business acha nahi chal raha."})
    client.cookies.clear()  # a different visitor
    data = client.post("/api/message", json={"text": "update goal 1"}).json()
    assert data["relation"] == "new_goal"
    assert data["goal"]["id"] == 2
    assert client.get("/api/goals/1").status_code == 404


def test_chit_chat_does_not_create_a_goal(client, use_provider):
    use_provider(FakeProvider(answers=[interpretation(relation="chit_chat", reply="Namaste! Batao kya problem hai?")]))
    data = client.post("/api/message", json={"text": "hello"}).json()
    assert data["goal"] is None and data["goals"] == []


def test_list_goals_answers_without_ai(client, use_provider):
    fake = use_provider(FakeProvider(answers=[]))
    data = client.post("/api/message", json={"text": "show my goals"}).json()
    assert data["provider"] == "app" and fake.prompts == []
    data = client.post("/api/message", json={"text": "meri goals dikhao"}).json()
    assert data["provider"] == "app"


def test_falls_back_to_second_provider_and_rejects_bad_json(client, use_provider):
    first = FakeProvider("gemini", answers=["not json at all"])
    second = FakeProvider("groq", answers=[interpretation()])
    use_provider(first, second)
    data = client.post("/api/message", json={"text": "help"}).json()
    assert data["provider"] == "groq"


def test_fenced_json_is_accepted(client, use_provider):
    use_provider(FakeProvider(answers=["```json\n" + json.dumps(interpretation()) + "\n```"]))
    assert client.post("/api/message", json={"text": "help"}).status_code == 200


def test_all_providers_down_gives_friendly_error(client, use_provider):
    use_provider(FakeProvider("gemini", answers=[ProviderError("HTTP 500")]),
                 FakeProvider("groq", answers=[ProviderError("HTTP 429")]))
    res = client.post("/api/message", json={"text": "help"})
    assert res.status_code == 503
    assert "try again" in res.json()["error"]
    assert client.get("/api/state").json()["remaining_today"] == 5  # failed call not counted


def test_no_keys_configured_is_503_not_crash(client, use_provider):
    use_provider()
    assert client.post("/api/message", json={"text": "help"}).status_code == 503


def test_empty_and_too_long_messages_rejected(client, use_provider):
    use_provider(FakeProvider(answers=[]))
    assert client.post("/api/message", json={"text": "   "}).status_code == 400
    assert client.post("/api/message", json={"text": "x" * 10001}).status_code == 400   # long pasted queries (up to 10,000) are fine


def test_visitor_daily_cap(client, use_provider):
    use_provider(FakeProvider(answers=[interpretation(relation="chit_chat")] * 10))
    codes = [client.post("/api/message", json={"text": f"hi {i}"}).status_code for i in range(6)]
    assert codes == [200] * 5 + [429]


def test_global_daily_cap(client, use_provider):
    use_provider(FakeProvider(answers=[interpretation(relation="chit_chat")] * 20))
    codes = []
    for i in range(9):
        client.cookies.clear()
        codes.append(client.post("/api/message", json={"text": f"hi {i}"}).status_code)
    assert codes == [200] * 8 + [429]


def test_status_change_and_state(client, use_provider):
    use_provider(FakeProvider(answers=[interpretation()]))
    client.post("/api/message", json={"text": "Mera business acha nahi chal raha."})
    res = client.post("/api/goals/1/status", json={"status": "done"})
    assert res.json()["goal"]["status"] == "done"
    state = client.get("/api/state").json()
    assert state["goals"][0]["status"] == "done"
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"]
    assert state["remaining_today"] == 4


def test_confidence_clamped_and_questions_capped(client, use_provider):
    many = [{"question": f"q{i}", "why": ""} for i in range(6)]
    use_provider(FakeProvider(answers=[interpretation(confidence=3, missing_info=many)]))
    goal = client.post("/api/message", json={"text": "help"}).json()["goal"]
    assert goal["confidence"] == 1.0
    assert len(goal["missing_info"]) == 3


def test_page_and_health(client, use_provider):
    use_provider(FakeProvider(answers=[]))
    assert "Say what you need" in client.get("/").text
    assert client.get("/api/health").json()["ok"] is True


def test_another_website_keeps_the_website_agent(client, ml_mode):
    from tests.test_ml import say

    r = say(client, "meri website slow hai")
    goal_id = r["goal"]["id"]
    r = say(client, "koi or website check kr skte hai abi", goal_id)
    assert r["goal"]["id"] == goal_id
    assert "address" in r["reply"].lower(), r["reply"]
