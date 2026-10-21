"""The ML engine: short conversations, no question loops, separate goals stay separate."""

from sqlalchemy import select

from app.db import get_db
from app.ml.intent import IntentModel
from app.ml.normalize import detect_language, normalize
from app.ml.slots import INTENT_LABELS, extract_all
from app.models import Message


def say(client, text, goal_id=None):
    body = {"text": text}
    if goal_id:
        body["goal_id"] = goal_id
    res = client.post("/api/message", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_normalizer_and_language():
    assert normalize("mera business acha nai chal rha hai") == "mera business accha nahi chal raha hai"
    assert detect_language("mera business acha nai chal rha hai") == "Hinglish"
    assert detect_language("My car is not starting") == "English"
    assert detect_language("मेरा बिज़नेस अच्छा नहीं चल रहा") == "Hindi"


def test_classifier_on_real_style_messages():
    model = IntentModel()
    cases = {
        "mera business acha nai chal rha hai": "business_slow",
        "mai ek developer hu muje kam nai mil rha hai kaise milega": "find_work",
        "My car is not starting.": "vehicle_problem",
        "meri website hai but customer nahi aa rhe": "no_customers",
        "customer ko khud msg jana chahiye": "customer_followup",
        "website khulti hi nahi": "website_problem",
        "mujhe python sikhna hai": "learn_skill",
    }
    for text, intent in cases.items():
        assert model.predict(text).intent == intent, text


def test_tap_labels_are_understood():
    model = IntentModel()
    for intent, label in INTENT_LABELS.items():
        assert model.predict(label).intent == intent, label


def test_slot_extraction():
    found = extract_all("no_customers", "kapde ki dukaan hai Panchkula mein, 6 mahine se log nahi aate, website nahi hai, budget 10000 rupees")
    assert found["business_type"] == "clothing shop"
    assert found["city"] == "Panchkula"
    assert found["has_website"] == "no"
    assert found["since"] == "6 mahine"
    assert "10000" in found["budget"]
    assert extract_all("website_problem", "My site example.co.in shows Not Secure")["website_url"] == "example.co.in"


def test_vague_message_gets_one_brief_request_then_an_answer(client, ml_mode):
    first = say(client, "mera business acha nai chal rha hai")
    assert first["relation"] == "new_goal"
    assert first["ask_words"] == 100
    assert "100 words" in first["reply"] and "🎤" in first["reply"]

    brief = ("kapde ki dukaan hai Panchkula mein. 2 saal se chal rahi thi par pichle 6 mahine se log aate hi nahi. "
             "Website nahi hai, sirf Instagram page hai. Budget 10000 tak hai.")
    second = say(client, brief)
    goal = second["goal"]
    assert second["relation"] == "update_goal"
    assert goal["intent"] == "no_customers"               # the vague goal got refined
    assert goal["id"] == first["goal"]["id"]              # same goal, not a new one
    assert "Business: clothing shop" in goal["known_facts"]
    assert "Since: 6 mahine" in goal["known_facts"]
    assert "City: Panchkula" in goal["known_facts"]
    assert goal["constraints"] == ["Budget: 10000"]
    assert second["reply"].startswith("Ye kijiye")         # acted, no more questions
    assert goal["missing_info"] == []


def test_never_more_than_two_questions(client, ml_mode):
    replies = [say(client, "business nahi chal raha")]
    replies.append(say(client, "bas aise hi"))
    replies.append(say(client, "pata nahi"))
    replies.append(say(client, "kuch bhi"))
    questions = sum(1 for r in replies if r["goal"] and r["goal"]["next_action"]["type"] == "ask")
    assert questions <= 2
    assert replies[-1]["goal"]["next_action"]["type"] != "ask"
    assert len({r["goal"]["id"] for r in replies if r["goal"]}) == 1


def test_question_has_tap_options_and_skip_assumes_default(client, ml_mode):
    first = say(client, "website khulti hi nahi")
    assert first["goal"]["intent"] == "website_problem"
    assert first["goal"]["next_action"]["type"] == "ask"
    second = say(client, "pata nahi")
    assert second["goal"]["next_action"]["type"] != "ask"
    assert "Ye kijiye" in second["reply"]


def test_answers_with_tap_option_switch_intent(client, ml_mode):
    say(client, "business nahi chal raha")
    second = say(client, "bas")                       # vague brief -> asks the main issue with buttons
    assert second["options"], second
    third = say(client, second["options"][0])         # "Customer kam aate hai"
    assert third["goal"]["intent"] == "no_customers"


def test_developer_flow(client, ml_mode):
    first = say(client, "mai ek developer hu muje kam nai mil rha hai kaise milega")
    assert first["goal"]["intent"] == "find_work"
    second = say(client, "php laravel me 8 saal ka experience hai, freelance aur job dono chahiye")
    goal = second["goal"]
    assert any("php" in f for f in goal["known_facts"])
    assert "Looking for: both" in goal["known_facts"]
    assert "Ye kijiye" in second["reply"]


def test_separate_problem_becomes_new_goal(client, ml_mode):
    say(client, "mera business acha nai chal rha hai")
    say(client, "kapde ki dukaan hai Panchkula mein, customer nahi aate, website nahi hai")
    car = say(client, "My car is not starting.")
    assert car["relation"] == "new_goal"
    assert car["goal"]["intent"] == "vehicle_problem"
    assert len(car["goals"]) == 2
    assert car["reply"].startswith("Here's what to do")   # English in, English out


def test_related_question_becomes_supporting_task(client, ml_mode):
    say(client, "meri dukaan pe customer nahi aate, kapde ki dukaan hai Panchkula mein, website nahi hai")
    res = say(client, "instagram pe followers kaise badhaye")
    assert res["relation"] == "supporting_task"
    assert res["goal"]["sub_goals"]
    assert len(res["goals"]) == 1


def test_greeting_and_unknown(client, ml_mode):
    hello = say(client, "hello")
    assert hello["relation"] == "chit_chat" and hello["goal"] is None
    odd = say(client, "zxqv blorp")
    assert odd["relation"] in ("unknown", "new_goal")
    if odd["relation"] == "unknown":
        assert len(odd["options"]) == 3 and odd["ask_words"] == 100


def test_messages_store_intent_for_learning(client, ml_mode):
    say(client, "website khulti hi nahi")
    db = next(get_db())
    row = db.scalar(select(Message).where(Message.role == "user"))
    assert row.intent == "website_problem" and row.confidence > 0


def test_cannot_focus_someone_elses_goal(client, ml_mode):
    first = say(client, "website khulti hi nahi")
    client.cookies.clear()
    other = say(client, "pata nahi", goal_id=first["goal"]["id"])
    assert other["goal"] is None or other["goal"]["id"] != first["goal"]["id"]


def test_answer_to_a_different_question_is_not_forced_into_the_asked_slot(client, ml_mode):
    say(client, "mera business acha nai chal rha hai")
    second = say(client, "mai ek developer hu muje kam nai mil rha hai kaise milega")
    assert second["goal"]["intent"] == "find_work"
    third = say(client, "dono mil jae t acha hoga")
    facts = third["goal"]["known_facts"]
    assert "Looking for: both" in facts
    assert not any(f.startswith("Skills: dono") for f in facts)
    fourth = say(client, "php me")
    assert fourth["reply"].startswith("Ye kijiye")       # stays Hinglish
    assert "Skills: php" in fourth["goal"]["known_facts"]


def test_goal_from_ai_mode_without_type_does_not_crash(client, ml_mode):
    from app.models import Goal

    say(client, "hello")  # creates the visitor cookie
    db = next(get_db())
    visitor = db.scalar(select(Message.visitor_id))
    db.add(Goal(visitor_id=visitor, title="Old AI goal", original_request="x"))
    db.commit()
    res = say(client, "website khulti hi nahi")
    assert res["goal"]["intent"] == "website_problem"
