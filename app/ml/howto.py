"""Step-by-step explanations for follow-up "how do I...?" questions.

When someone asks how to do one of the steps we suggested, they get that step explained,
not the same list again.
"""

import re
from dataclasses import dataclass

from app.ml.normalize import normalize


QUESTION_RE = re.compile(r"\b(kaise|kese|how|kya hai|kya hota|matlab|samjhao|samjha do|batao|bata do|bataiye|"
                         r"explain|kahan|kidhar|where|steps?|tarika)\b")


@dataclass(frozen=True)
class HowTo:
    key: str
    label: str                 # tap button text
    pattern: str               # matched against the normalised message
    steps_hi: tuple[str, ...]
    steps_en: tuple[str, ...]


HOWTOS: list[HowTo] = [
    HowTo("google_listing", "Google listing kaise banaye / check kare",
          r"(listing|business profile|my business|\bgmb\b|google maps|maps pe|map pe|profile)",
          ("Phone mein Google Maps app kholo aur apni dukaan ka naam search karo.",
           "Agar dikhe to uspe tap karke \"Claim this business\" / \"Own this business?\" dabao. Na dikhe to google.com/business pe jaake \"Add your business\" karo.",
           "Naam, category (jaise \"Clothing store\"), address, phone, WhatsApp aur timing bharo.",
           "Google verification maangega (call, SMS ya video). Ye karke listing live ho jaati hai.",
           "Live hone ke baad kam se kam 10 photos daalo: dukaan ke andar, bahar, aur products."),
          ("Open Google Maps on your phone and search for your business name.",
           "If it shows up, tap it and choose \"Claim this business\". If not, go to google.com/business and \"Add your business\".",
           "Fill in name, category, address, phone, WhatsApp and opening hours.",
           "Google asks you to verify (call, SMS or video). After that the listing goes live.",
           "Then add at least 10 photos: inside, outside and your products.")),
    HowTo("google_check", "Google pe kaise check kare ki main dikhta hoon",
          r"(google|gugal|gugl).*(dekh|check|chek|search|dhoondh|khoj|dikh)|(dekh|check|chek|search|dhoondh|khoj).*(google|gugal)",
          ("Phone mein Chrome ka Incognito tab kholo (taaki purani history asar na kare).",
           "Wahi likho jo customer likhega, jaise \"kapde ki dukaan Panchkula\" ya \"clothing shop near me\".",
           "Dekho upar ke 3 naam aur map mein aap ho ya nahi. Agar nahi ho, to aapki listing kamzor hai.",
           "Upar wale 3 dukaano ko dekho: unke kitne reviews aur photos hai. Aapko unke barabar pahunchna hai."),
          ("Open an Incognito tab in Chrome on your phone (so your own history doesn't affect results).",
           "Type what a customer would type, e.g. \"clothing shop Panchkula\" or \"clothing shop near me\".",
           "Check whether you appear in the top 3 and on the map. If not, your listing is weak.",
           "Look at the top 3: how many reviews and photos they have. That's the level to reach.")),
    HowTo("reviews", "Reviews kaise badhaye",
          r"review",
          ("Google listing kholo aur \"Ask for reviews\" / \"Share review form\" se apna review link copy karo.",
           "Har khush customer ko bill ke baad WhatsApp pe ye link bhejo: \"Aapko service kaisi lagi? 1 minute ka review de dijiye.\"",
           "Counter pe QR code laga do jo isi link pe le jaaye.",
           "Har review ka jawab do, khaas kar kharab review ka, politely."),
          ("Open your Google listing and copy your review link (\"Ask for reviews\").",
           "Send it on WhatsApp to every happy customer after the sale: \"How was it? A 1-minute review would help a lot.\"",
           "Put a QR code for the link at the counter.",
           "Reply to every review, especially bad ones, politely.")),
    HowTo("website_speed", "Website ki speed kaise check kare",
          r"(speed|3 second|fast|slow|khulti|load).*(check|chek|dekh|test|kaise)|(check|chek|test).*(website|speed)",
          ("pagespeed.web.dev kholo aur apni website ka address daalo.",
           "\"Mobile\" wala score dekho. 50 se kam hai to website slow hai.",
           "Apne phone pe mobile data se website kholo aur gino kitne second lagte hai.",
           "3 second se zyada lage to images chhoti karni hongi aur caching on karni hogi."),
          ("Open pagespeed.web.dev and enter your website address.",
           "Look at the \"Mobile\" score. Under 50 means the site is slow.",
           "Open the site on your phone using mobile data and count the seconds.",
           "Over 3 seconds: images need compressing and caching turned on.")),
    HowTo("whatsapp_button", "Website pe WhatsApp button kaise lagaye",
          r"whatsapp.*(button|link)|(button|link).*whatsapp",
          ("Apna link banao: https://wa.me/91XXXXXXXXXX (91 ke baad apna number, bina space).",
           "WordPress hai to \"Click to Chat\" jaisa free plugin lagao aur ye number daalo.",
           "Button har page pe neeche right side dikhna chahiye.",
           "Apne phone se test karo ki tap karte hi WhatsApp khulta hai."),
          ("Make your link: https://wa.me/<country code><number>, no spaces or +.",
           "On WordPress, install a free plugin like \"Click to Chat\" and add the number.",
           "The button should show at the bottom right on every page.",
           "Test it from your phone: tapping should open WhatsApp.")),
    HowTo("customer_offers", "Customers ko offer kaise bheje",
          r"(offer|number save|customer ka number|broadcast)",
          ("Har bill pe customer ka naam aur WhatsApp number ek Google Sheet ya register mein likho.",
           "WhatsApp Business app lo aur \"Broadcast list\" banao (sirf unko jaata hai jinke paas aapka number saved hai).",
           "Mahine mein 1-2 baar hi bhejo: naya stock, tyohaar offer, ya \"aapke liye 10% off\".",
           "Har message mein likho \"STOP likh dein to aage nahi bhejenge\"."),
          ("Record each customer's name and WhatsApp number in a Google Sheet at billing.",
           "Use WhatsApp Business and create a \"Broadcast list\" (it only reaches people who saved your number).",
           "Send only 1-2 times a month: new stock, festival offer, or a personal discount.",
           "Always add \"Reply STOP and we won't message again\".")),
    HowTo("portfolio", "Portfolio kaise banaye",
          r"portfolio",
          ("Apne 2-3 best kaam chuno, chahe chhote ho.",
           "Har kaam ke liye likho: problem kya thi, aapne kya kiya, result kya aaya.",
           "Pehle aur baad ke screenshot lagao.",
           "Ek simple page pe daalo (apni website, GitHub ya Google Doc) aur har message mein link do."),
          ("Pick your 2-3 best pieces of work, even small ones.",
           "For each, write: the problem, what you did, the result.",
           "Add before/after screenshots.",
           "Put it on one simple page (your site, GitHub or a Google Doc) and link it in every message.")),
]

STEP_CHOICES: dict[str, list[str]] = {
    "no_customers": ["google_check", "google_listing", "reviews", "customer_offers"],
    "business_slow": ["google_listing", "customer_offers", "reviews"],
    "marketing_social": ["google_listing", "reviews"],
    "need_website": ["google_listing", "whatsapp_button"],
    "website_problem": ["website_speed", "whatsapp_button"],
    "find_work": ["portfolio"],
    "customer_followup": ["customer_offers"],
}
BY_KEY = {h.key: h for h in HOWTOS}
BY_LABEL = {h.label.lower(): h for h in HOWTOS}


def find_howto(text: str) -> HowTo | None:
    """A how-to question about one of the steps, or a tapped step button."""
    exact = BY_LABEL.get(text.strip().lower())
    if exact:
        return exact
    norm = normalize(text).replace("gugal", "google").replace("chek", "check")
    if not QUESTION_RE.search(norm):
        return None
    for howto in HOWTOS:
        if re.search(howto.pattern, norm):
            return howto
    return None


def render(howto: HowTo, hi: bool) -> str:
    steps = howto.steps_hi if hi else howto.steps_en
    head = f"{howto.label}:" if hi else "Here's how:"
    return head + "\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))


def choices_for(intent: str, exclude: str = "") -> list[str]:
    return [BY_KEY[k].label for k in STEP_CHOICES.get(intent, []) if k != exclude]
