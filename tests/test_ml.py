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


def test_new_chat_starts_fresh(client, ml_mode):
    say(client, "website khulti hi nahi")
    assert client.get("/api/state").json()["goals"]
    client.post("/api/new-chat")
    client.cookies.clear()  # what the browser does with the deleted cookie
    assert client.get("/api/state").json()["goals"] == []


def test_short_message_without_business_or_city_asks_for_description_first(client, ml_mode):
    first = say(client, "Meri website hai but customer nahi aa rhe.")
    assert first["goal"]["intent"] == "no_customers"
    assert first["ask_words"] == 100
    second = say(client, "furniture shop hai mohali mein, 1 saal se, online koi order nahi aata")
    assert "Business: furniture shop" in second["goal"]["known_facts"]
    assert "furniture shop Mohali" in second["reply"]


def test_how_to_follow_up_explains_the_step_instead_of_repeating(client, ml_mode):
    say(client, "Meri website hai but customer nahi aa rhe.")
    answer = say(client, "kapde ki dukaan hai Panchkula mein, log aate hi nahi")
    assert answer["reply"].startswith("Ye kijiye")
    how = say(client, "मैं गूगल पर कैसे देखूं")
    assert how["relation"] == "howto"
    assert "Incognito" in how["reply"]
    assert how["reply"] != answer["reply"]
    assert how["options"] and all("Google pe kaise check" not in o for o in how["options"])
    listing = say(client, how["options"][0])          # tap another step
    assert listing["relation"] == "howto" and listing["reply"] != how["reply"]
    assert len(listing["goals"]) == 1                   # no new goals from follow-ups


def test_same_answer_is_never_sent_twice(client, ml_mode):
    say(client, "Meri website hai but customer nahi aa rhe.")
    first = say(client, "kapde ki dukaan hai Panchkula mein, log aate hi nahi")
    again = say(client, "customer nahi aate")
    assert again["reply"] != first["reply"]
    assert again["options"]                               # offers steps to explain instead


def test_confused_gets_one_simple_step_not_the_list(client, ml_mode):
    say(client, "Meri website hai but customer nahi aa rhe.")
    answer = say(client, "kapde ki dukaan hai Panchkula mein, log aate hi nahi")
    for text in ("मुझे समझ नहीं आया", "आपने मुझे फिर से वही आंसर दे दिया", "I didn't understand"):
        res = say(client, text)
        assert res["relation"] == "howto", text
        assert res["reply"] != answer["reply"]
        assert res["options"]
        assert len(res["goals"]) == 1


def test_accuracy_on_unseen_messages_does_not_regress():
    from tests.data.holdout import HOLDOUT
    from tests.data.holdout2 import HOLDOUT2

    model = IntentModel()
    for data, min_top1, min_top3 in ((HOLDOUT, 0.80, 0.92), (HOLDOUT2, 0.75, 0.88)):
        top1 = sum(model.predict(t).intent == y for t, y in data) / len(data)
        top3 = sum(y in [i for i, _ in model.predict(t).ranking[:3]] for t, y in data) / len(data)
        assert top1 >= min_top1 and top3 >= min_top3, (top1, top3)


def test_brand_new_developer_issue_is_understood(client, ml_mode):
    text = ("मैं एक सॉफ्टवेयर डेवलपर हूं पीएचपी लैंग्वेज में काम करता हूं मेरा एक प्रोजेक्ट है "
            "जिसमें मुझे डेटाबेस की क्वेरी में ऑप्टिमाइज करवाना है क्या आप मेरी इसमें मदद करेंगे")
    res = say(client, text)
    assert res["goal"]["intent"] == "slow_db_query"
    assert "EXPLAIN" in res["reply"]
    assert any("php" in f for f in res["goal"]["known_facts"])


def test_unlearned_problem_gets_a_goal_and_plan_not_a_loop(client, ml_mode):
    first = say(client, "mere pados ka kutta raat bhar bhaukta hai aur so nahi paata koi upaay")
    assert first["goal"] is not None
    assert "Ye kijiye" not in first["reply"] or "seekh" in first["reply"]
    second = say(client, "haan roz ka hai ye")
    assert second["reply"] != first["reply"]


def test_crisis_message_gets_support_not_problem_solving(client, ml_mode):
    res = say(client, "mujhe ab jeena nahi chahta, sab khatam karna hai")
    assert res["relation"] == "safety" and "14416" in res["reply"] and res["goal"] is None


def test_professional_topics_carry_a_caution(client, ml_mode):
    res = say(client, "teen credit card aur do personal loan hai, emi nahi bhar pa raha, recovery wale call karte hai")
    assert res["goal"]["intent"] == "debt_emi"
    assert "professional" in res["reply"]


def test_wrong_guess_can_be_corrected_with_one_tap(client, ml_mode):
    first = say(client, "parents ko kashmir ghumana hai")
    if first["goal"]["intent"] == "travel_plan":
        return  # guessed right this time; nothing to correct
    assert first["options"], first
    trip = next((o for o in first["options"] if o == "Plan a trip"), None)
    assert trip, first["options"]
    fixed = say(client, trip)
    assert fixed["relation"] == "corrected"
    assert fixed["goal"]["id"] == first["goal"]["id"]
    assert fixed["goal"]["intent"] == "travel_plan"
    assert len(fixed["goals"]) == 1
