"""What each problem type needs to know, how to pull it out of free text, and what to ask.

Rules that keep conversations short:
- facts already in the message are never asked again;
- only "critical" slots are ever asked, one per reply, with tap options;
- anything else falls back to a default, shown to the user as "Assumed".
"""

import re
from dataclasses import dataclass, field, replace
from typing import Callable

from app.ml.normalize import normalize


# ---------------------------------------------------------------- extractors
def _first(patterns: list[tuple[str, str]], text: str) -> str | None:
    for pattern, value in patterns:
        if re.search(pattern, text):
            return value
    return None


BUSINESS_TYPES = [
    (r"\b(kapde|kapda|clothes|clothing|garment|boutique|saree|readymade)\b", "clothing shop"),
    (r"\b(furniture)\b", "furniture shop"),
    (r"\b(restaurant|dhaba|cafe|food|hotel khana|tiffin|bakery|sweets|mithai)\b", "food business"),
    (r"\b(salon|parlour|parlor|beauty|spa|barber)\b", "salon"),
    (r"\b(clinic|doctor|dental|dentist|hospital|physio|patient)\b", "clinic"),
    (r"\b(gym|fitness|yoga)\b", "gym / fitness"),
    (r"\b(coaching|tuition|school|institute|academy|students)\b", "coaching / education"),
    (r"\b(kirana|grocery|general store|supermarket)\b", "grocery store"),
    (r"\b(mobile shop|mobile repair|electronics|computer shop)\b", "electronics / mobile shop"),
    (r"\b(hotel|homestay|guest house|resort)\b", "hotel / stay"),
    (r"\b(property|real estate|plots|flats)\b", "real estate"),
    (r"\b(ca|accountant|tax|gst consultant)\b", "accounting / tax"),
    (r"\b(jewellery|jewelry|jeweller)\b", "jewellery shop"),
    (r"\b(pharmacy|medical store|chemist)\b", "pharmacy"),
    (r"\b(travel|tour|taxi|cab)\b", "travel / taxi"),
    (r"\b(factory|manufacturing|manufacturer)\b", "manufacturing"),
    (r"\b(agency|software|it company|services company)\b", "services company"),
    (r"\b(ecommerce|online store|online business)\b", "online store"),
]

CITIES = [
    "panchkula", "chandigarh", "mohali", "zirakpur", "ambala", "delhi", "new delhi", "noida", "gurgaon",
    "gurugram", "faridabad", "ghaziabad", "mumbai", "pune", "bangalore", "bengaluru", "hyderabad",
    "chennai", "kolkata", "jaipur", "lucknow", "ludhiana", "amritsar", "jalandhar", "patiala", "karnal",
    "kurukshetra", "panipat", "sonipat", "rohtak", "hisar", "dehradun", "shimla", "solan", "indore",
    "bhopal", "ahmedabad", "surat", "vadodara", "nagpur", "kochi", "coimbatore", "patna", "ranchi",
    "bhubaneswar", "guwahati", "kanpur", "agra", "varanasi", "meerut", "london", "manchester",
    "birmingham", "new york", "los angeles", "chicago", "houston", "toronto", "vancouver", "sydney",
    "melbourne", "brisbane", "dubai", "abu dhabi", "singapore", "berlin", "dublin", "auckland",
]
_CITY_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in sorted(CITIES, key=len, reverse=True)) + r")\b")
_URL_RE = re.compile(r"\b((?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|co\.in|net|org|co|io|biz|info|store|shop|online|co\.uk|com\.au|ae)(?:/\S*)?)\b")
_NUM_WORDS = r"(\d+|ek|do|teen|char|chaar|paanch|panch|chhe|saat|aath|one|two|three|four|five|six|few|kuch|kai)"
_SINCE_RE = re.compile(_NUM_WORDS + r"\s*(mahine|mahina|month|months|saal|sal|year|years|hafte|hafta|week|weeks|din|days)")
_BUDGET_RE = re.compile(r"((?:₹|rs\.?|inr|\$|£|€)\s?\d[\d,]*(?:\s?(?:k|lakh|hazaar|thousand))?|\d[\d,]*\s?(?:rupees|rupaye|rs|hazaar|thousand|lakh|k\b|dollars|usd))")


def business_type(text: str) -> str | None:
    found = _first(BUSINESS_TYPES, text)
    if found:
        return found
    m = re.search(r"\b([a-z]{3,20}) (?:ki dukaan|ka business|ka kaam|shop|store|business)\b", text)
    if m and m.group(1) not in {"mera", "meri", "humara", "apna", "my", "our", "the", "accha", "online", "chhota", "small"}:
        return f"{m.group(1)} business"
    return None


def city(text: str) -> str | None:
    m = _CITY_RE.search(text)
    return m.group(1).title() if m else None


def website_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(1) if m else None


def has_website(text: str) -> str | None:
    if website_url(text):
        return "yes"
    if re.search(r"(website nahi|no website|don ?t have a website|do not have a website|koi website nahi|website bhi nahi)", text):
        return "no"
    if re.search(r"(website hai|have a website|my website|meri website|humari website|our website)", text):
        return "yes"
    return None


def since(text: str) -> str | None:
    # "pichle 6 mahine se" / "for 6 months" beats a plain duration elsewhere in the text
    m = (re.search(r"(?:pichle|last|past)\s+" + _SINCE_RE.pattern, text)
         or re.search(_SINCE_RE.pattern + r"\s+se\b", text) or _SINCE_RE.search(text))
    return f"{m.group(1)} {m.group(2)}" if m else None


def budget(text: str) -> str | None:
    m = _BUDGET_RE.search(text)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(?:budget|kharcha|spend)\D{0,12}(\d[\d,]*(?:\s?(?:k|lakh|hazaar|thousand))?)", text)
    return m.group(1).strip() if m else None


def channel(text: str) -> str | None:
    return _first([(r"whatsapp", "WhatsApp"), (r"\b(email|mail)\b", "Email"), (r"\b(sms|text message)\b", "SMS")], text)


SKILLS = ["php", "laravel", "wordpress", "python", "django", "react", "angular", "vue", "node", "java",
          "android", "flutter", "ios", "javascript", "design", "ui", "seo", "data", "sql", "devops",
          "graphic", "video editing", "content writing", "marketing", "excel", "accounts", "sales"]


def skill(text: str) -> str | None:
    found = [s for s in SKILLS if re.search(rf"\b{re.escape(s)}\b", text)]
    return ", ".join(found[:3]) if found else None


def work_type(text: str) -> str | None:
    return _first([(r"\b(dono|both)\b", "both"), (r"\b(freelance|freelancing|freelancer|projects?|upwork|fiverr|clients?)\b", "freelance"),
                   (r"\b(job|full time|fulltime|company|office)\b", "job")], text)


def vehicle_type(text: str) -> str | None:
    return _first([(r"\b(car|gadi)\b", "car"), (r"\b(bike|motorcycle)\b", "bike"), (r"\b(scooty|scooter|activa)\b", "scooter"),
                   (r"\b(truck|tempo)\b", "truck"), (r"\bauto\b", "auto-rickshaw")], text)


def vehicle_symptom(text: str) -> str | None:
    return _first([
        (r"(start nahi|not starting|won t start|wont start|start hi nahi|does not start|doesn t start)", "won't start"),
        (r"(awaz|aawaz|noise|sound)", "strange noise"), (r"(garam|overheat|heat)", "overheating"),
        (r"brake", "brake problem"), (r"battery", "battery problem"), (r"\bac\b", "AC not cooling"),
        (r"mileage", "low mileage"), (r"engine light", "engine warning light"), (r"puncture", "puncture"),
    ], text)


def website_symptom(text: str) -> str | None:
    return _first([
        (r"(nahi khul|not opening|not loading|down|band hai|khulti hi nahi|khul nahi)", "not opening"),
        (r"(not secure|ssl|https|padlock)", "security warning"), (r"(hack|virus|malware|spam)", "hacked / malware"),
        (r"(slow|time lagta|load hone)", "slow"), (r"\berror\b", "error page"),
        (r"(mobile|phone)", "broken on phones"), (r"\bform\b", "contact form not working"),
        (r"(google pe|on google|seo|search)", "not showing on Google"),
    ], text)


def platform(text: str) -> str | None:
    return _first([(r"instagram|insta\b", "Instagram"), (r"facebook|\bfb\b", "Facebook"), (r"youtube", "YouTube"),
                   (r"google", "Google"), (r"linkedin", "LinkedIn")], text)


def payment_issue(text: str) -> str | None:
    return _first([(r"(udhaar|udhar|late|time pe nahi|don t pay|nahi dete|pending|phans)", "late payments"),
                   (r"(online|upi|gateway|razorpay|stripe|card)", "accept online payments"),
                   (r"(invoice|bill|billing)", "invoicing / billing"), (r"(hisaab|track|record|owe)", "tracking dues")], text)


def staff_role(text: str) -> str | None:
    m = re.search(r"\b(salesman|sales person|delivery boy|receptionist|developer|accountant|driver|cook|chef|"
                  r"helper|manager|designer|teacher|nurse|technician|kaarigar|karigar|tailor|staff)\b", text)
    return m.group(1) if m else None


TOPICS = ["coding", "programming", "python", "web development", "english", "digital marketing", "ai",
          "machine learning", "excel", "design", "data analysis", "trading", "communication", "java", "accounts"]


def topic(text: str) -> str | None:
    found = [t for t in TOPICS if re.search(rf"\b{re.escape(t)}\b", text)]
    return found[0] if found else None


def customer_source(text: str) -> str | None:
    return _first([(r"(excel|sheet)", "Excel / Google Sheet"), (r"(register|diary|copy)", "paper register"),
                   (r"(phone contacts|contacts|whatsapp)", "phone / WhatsApp contacts"),
                   (r"(software|crm|app)", "existing software")], text)


MAIN_ISSUE_REDIRECT = [
    (r"(customer|sales|log nahi|log aate hi nahi|aate hi nahi|koi nahi aata|footfall|enquiry|order|leads|patient|students)", "no_customers"),
    (r"(website|site)", "website_problem"),
    (r"(paise|payment|udhaar|udhar)", "payments_collection"),
    (r"(staff|employee|banda|worker)", "hiring_staff"),
    (r"(marketing|instagram|facebook|ads|promotion)", "marketing_social"),
]


def main_issue(text: str) -> str | None:
    return _first(MAIN_ISSUE_REDIRECT, text)


# ---------------------------------------------------------------- slot + intent specs
@dataclass(frozen=True)
class Slot:
    key: str
    label: str                          # shown in "Known facts"
    extract: Callable[[str], str | None]
    question_hi: str = ""               # Hinglish
    question_en: str = ""
    options: tuple[str, ...] = ()       # tap answers
    critical: bool = False              # only critical slots are ever asked
    free_text: bool = False             # a short free answer is accepted as the value
    default: str = ""                   # used when skipped or never asked


BUSINESS = Slot("business_type", "Business", business_type,
                "Aapka kaam / business kis cheez ka hai?", "What kind of business is it?",
                ("Shop", "Clinic", "Restaurant", "Salon", "Coaching", "Online store"), free_text=True,
                default="small business")
CITY = Slot("city", "City", city, "Kis shehar mein hai?", "Which city is it in?", (), free_text=True,
            default="your city")
HAS_SITE = Slot("has_website", "Has a website", has_website, "Kya aapki website hai?", "Do you have a website?",
                ("Haan, hai", "Nahi hai"), default="not sure")
SITE = Slot("website_url", "Website", website_url, "Website ka address kya hai?", "What's the website address?",
            (), free_text=True, default="")
BUSINESS_NEEDED = replace(BUSINESS, critical=True)   # only where the answer really depends on it
SITE_NEEDED = replace(SITE, critical=True)
SINCE = Slot("since", "Since", since, default="")
BUDGET = Slot("budget", "Budget", budget, default="")

SPECS: dict[str, dict] = {
    "business_slow": {
        "vague": True,
        "title": "Get the business growing again",
        "problem": "The business isn't doing well, but the exact cause isn't clear yet.",
        "goal": "More customers and steady income",
        "slots": [Slot("main_issue", "Main issue", main_issue, "Sabse badi problem kya lagti hai?",
                       "What feels like the biggest problem?",
                       ("Customer kam aate hai", "Website ki problem", "Payment nahi aata", "Staff problem",
                        "Marketing nahi hoti"), critical=True),
                  BUSINESS, CITY, SINCE, BUDGET],
    },
    "no_customers": {
        "title": "Get more customers",
        "problem": "Not enough people find or choose the business.",
        "goal": "More enquiries and customers",
        "slots": [BUSINESS, CITY, HAS_SITE, SITE, SINCE, BUDGET],
    },
    "website_problem": {
        "title": "Fix the website",
        "problem": "The website has a problem that may be costing customers.",
        "goal": "A website that opens fast, looks right and brings enquiries",
        "slots": [SITE_NEEDED, Slot("symptom", "Problem", website_symptom, "Website mein kya dikkat aa rahi hai?",
                             "What's wrong with the website?",
                             ("Khul nahi rahi", "Bahut slow hai", "Not secure dikhata hai", "Phone pe kharab dikhti hai",
                              "Hack ho gayi"), critical=True, free_text=True),
                  BUSINESS, SINCE],
    },
    "need_website": {
        "title": "Get a website for the business",
        "problem": "The business has no website, or the current one doesn't work for it.",
        "goal": "A simple website that brings calls and enquiries",
        "slots": [BUSINESS_NEEDED, CITY, BUDGET, HAS_SITE],
    },
    "customer_followup": {
        "title": "Automatic customer follow-ups",
        "problem": "Follow-ups and reminders depend on remembering to send them manually.",
        "goal": "Customers get the right message at the right time without manual work",
        "slots": [Slot("channel", "Channel", channel, "Message kis pe jaana chahiye?", "Where should messages go?",
                       ("WhatsApp", "Email", "SMS"), critical=True),
                  Slot("customer_source", "Customer list", customer_source, default="not sure"),
                  BUSINESS],
    },
    "find_work": {
        "title": "Find paid work",
        "problem": "Skills are there, but work isn't coming in.",
        "goal": "Steady paid work",
        "slots": [Slot("skill", "Skills", skill, "Aap kis kaam mein expert ho?", "What are your main skills?",
                       ("PHP / Laravel", "WordPress", "Python", "Design", "Marketing"), critical=True, free_text=True),
                  Slot("work_type", "Looking for", work_type, "Freelance kaam chahiye ya job?",
                       "Freelance work or a job?", ("Freelance", "Job", "Dono"), critical=True, default="both"),
                  CITY],
    },
    "vehicle_problem": {
        "title": "Fix the vehicle",
        "problem": "The vehicle has a fault.",
        "goal": "Vehicle running safely again",
        "slots": [Slot("vehicle_type", "Vehicle", vehicle_type, default="vehicle"),
                  Slot("symptom", "Problem", vehicle_symptom, "Kya problem aa rahi hai?", "What exactly happens?",
                       ("Start nahi hoti", "Awaz aati hai", "Garam hoti hai", "Brake problem"), critical=True,
                       free_text=True),
                  CITY],
    },
    "marketing_social": {
        "title": "Get noticed online",
        "problem": "The business isn't visible enough online.",
        "goal": "More people discover the business online",
        "slots": [Slot("platform", "Platform", platform, "Kahan pe dikhna hai sabse zyada?", "Where do you most want to be seen?",
                       ("Google", "Instagram", "Facebook", "YouTube"), critical=True, default="Google"),
                  BUSINESS, CITY, BUDGET],
    },
    "payments_collection": {
        "title": "Get paid on time",
        "problem": "Money is stuck or hard to collect.",
        "goal": "Payments collected on time with less chasing",
        "slots": [Slot("payment_issue", "Payment problem", payment_issue, "Payment mein kya problem hai?",
                       "What's the payment problem?", ("Log late dete hai", "Online payment lena hai", "Bill banana hai"),
                       critical=True),
                  BUSINESS],
    },
    "hiring_staff": {
        "title": "Find good staff",
        "problem": "The right people are hard to find or keep.",
        "goal": "Reliable staff in place",
        "slots": [Slot("role", "Role", staff_role, "Kaun sa staff chahiye?", "Which role do you need?",
                       ("Salesman", "Helper", "Receptionist", "Delivery", "Developer"), critical=True, free_text=True),
                  BUSINESS, CITY],
    },
    "learn_skill": {
        "title": "Learn a new skill",
        "problem": "Wants to learn something new, needs a clear path.",
        "goal": "Learn the skill well enough to use it",
        "slots": [Slot("topic", "Topic", topic, "Kya seekhna hai?", "What do you want to learn?",
                       ("Coding", "English", "Digital marketing", "AI"), critical=True, free_text=True)],
    },
}

INTENT_LABELS = {
    "no_customers": "Customer nahi aa rahe",
    "website_problem": "Website mein problem",
    "need_website": "Website banwani hai",
    "customer_followup": "Customer ko automatic message",
    "find_work": "Kaam / job chahiye",
    "marketing_social": "Online marketing",
    "payments_collection": "Payment ki problem",
    "hiring_staff": "Staff chahiye",
    "vehicle_problem": "Gadi ki problem",
    "learn_skill": "Kuch seekhna hai",
    "business_slow": "Business accha nahi chal raha",
}

# A new message of one of these types, while working on the key goal, is a supporting task, not a new goal.
RELATED = {
    "no_customers": {"marketing_social", "need_website", "website_problem", "customer_followup"},
    "need_website": {"marketing_social", "no_customers", "payments_collection"},
    "website_problem": {"no_customers", "marketing_social"},
    "marketing_social": {"no_customers", "need_website"},
    "customer_followup": {"payments_collection", "no_customers"},
    "payments_collection": {"customer_followup"},
}


def extract_all(intent: str, raw_text: str) -> dict[str, str]:
    text = normalize(raw_text)
    lowered = raw_text.lower()
    found: dict[str, str] = {}
    for slot in SPECS[intent]["slots"]:
        value = slot.extract(text) or (slot.extract(lowered) if slot.key == "website_url" else None)
        if value:
            found[slot.key] = value
    return found
