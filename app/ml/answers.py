"""Useful first answers per problem type, filled from what the user told us.

Kept concrete and short. These are the "act" step once the question budget is used up
or nothing critical is missing.
"""

from typing import Callable


def _g(slots: dict[str, str], key: str, default: str) -> str:
    return slots.get(key) or default


def business_slow(s: dict, hi: bool) -> tuple[list[str], str]:
    biz = _g(s, "business_type", "business")
    if hi:
        return ([f"Pehle pata karo customer kahan se aate hai: apne last 20 customers se poocho unhe aapke {biz} ka kaise pata chala.",
                 "Google Business Profile check karo: sahi phone, timing, photos aur reviews hone chahiye. Zyada log wahin se dhoondte hai.",
                 "Purane customers ko ek offer bhejo. Naya customer laana mehenga hai, purana wapas laana sasta.",
                 "Har hafte 3 numbers likho: kitne log aaye, kitne ne kharida, kitni kamai. Isse asli problem dikhegi."],
                "Agla step: in 3 numbers se pata chalega problem customer laane mein hai ya bechne mein.")
    return ([f"Find out where customers come from: ask your last 20 customers how they found your {biz}.",
             "Check your Google Business Profile: correct phone, hours, photos and reviews. Most people search there first.",
             "Send an offer to past customers. Bringing them back is cheaper than finding new ones.",
             "Write down 3 numbers every week: visitors, buyers, revenue. They show where the real problem is."],
            "Next step: those numbers show whether the problem is getting customers or converting them.")


def no_customers(s: dict, hi: bool) -> tuple[list[str], str]:
    biz, place = _g(s, "business_type", "business"), _g(s, "city", "aapke area" if hi else "your area")
    site = s.get("has_website")
    if hi:
        pts = [f"Google pe \"{biz} {place}\" search karo. Agar aap top 3 mein nahi dikhte, to log aapko dhoondh hi nahi pa rahe.",
               "Google Business Profile poora bharo: photos, timing, WhatsApp number, aur har khush customer se review maango.",
               "Har customer ka number save karo aur mahine mein ek baar offer bhejo."]
        pts.append("Website hai to check karo: phone pe 3 second mein khulti hai aur call / WhatsApp button upar dikhta hai?"
                   if site == "yes" else "Ek simple website ya page banao jismein call aur WhatsApp button ho, taaki Google se aaye log seedha contact karein.")
        return pts, "Agla step: Google pe aapki listing aur website check karna."
    pts = [f"Search Google for \"{biz} {place}\". If you're not in the top 3, people simply can't find you.",
           "Complete your Google Business Profile: photos, hours, WhatsApp number, and ask every happy customer for a review.",
           "Save every customer's number and send one offer a month."]
    pts.append("Check your website opens in 3 seconds on a phone and has a call / WhatsApp button at the top."
               if site == "yes" else "Get a simple website or page with call and WhatsApp buttons, so people from Google can contact you directly.")
    return pts, "Next step: check your Google listing and website."


def website_problem(s: dict, hi: bool) -> tuple[list[str], str]:
    symptom, url = s.get("symptom", ""), s.get("website_url")
    tips_hi = {
        "not opening": "Pehle domain aur hosting ki renewal date check karo. Zyada tar website isi wajah se band hoti hai.",
        "security warning": "\"Not secure\" ka matlab SSL certificate nahi hai ya expire ho gaya. Hosting panel mein free SSL (Let's Encrypt) on karo.",
        "slow": "Badi images sabse bada kaaran hoti hai. Images compress karo aur caching on karo.",
        "hacked / malware": "Turant saare passwords badlo (hosting, WordPress, email) aur backup se site restore karo.",
        "broken on phones": "Website ko phone pe khol ke dekho kaun se page toot rahe hai. Theme ya page builder update se aksar theek hota hai.",
        "error page": "Error aksar plugin ya PHP update ke baad aata hai. Last kiya hua update wapas lo.",
        "contact form not working": "Form khud bhar ke test karo. Email ka setup (SMTP) aksar wajah hota hai.",
        "not showing on Google": "Google Search Console mein website add karo aur sitemap submit karo.",
    }
    tips_en = {
        "not opening": "Check the domain and hosting renewal dates first. That's the most common reason a site goes down.",
        "security warning": "\"Not secure\" means the SSL certificate is missing or expired. Turn on free SSL (Let's Encrypt) in your hosting panel.",
        "slow": "Large images are the usual cause. Compress them and turn on caching.",
        "hacked / malware": "Change all passwords now (hosting, WordPress, email) and restore from a clean backup.",
        "broken on phones": "Open each page on your phone to see what breaks. Updating the theme or page builder often fixes it.",
        "error page": "Errors usually start after a plugin or PHP update. Roll back the last update.",
        "contact form not working": "Fill in the form yourself to test it. Email (SMTP) setup is usually the cause.",
        "not showing on Google": "Add the site to Google Search Console and submit the sitemap.",
    }
    first = (tips_hi if hi else tips_en).get(symptom)
    if hi:
        pts = ([first] if first else []) + [
            "Kuch bhi badalne se pehle website ka poora backup lo.",
            "Phone pe mobile data se website kholo, jaise customer kholta hai.",
        ]
        nxt = f"Agla step: {url} ko check karke exact wajah dhoondhna." if url else "Agla step: website ka address milte hi usse check karna."
        return pts, nxt
    pts = ([first] if first else []) + ["Take a full backup before changing anything.",
                                        "Open the site on a phone using mobile data, the way customers do."]
    nxt = f"Next step: check {url} to find the exact cause." if url else "Next step: check the website once we have its address."
    return pts, nxt


def need_website(s: dict, hi: bool) -> tuple[list[str], str]:
    biz = _g(s, "business_type", "business")
    if hi:
        return ([f"{biz} ke liye 1 simple page kaafi hai shuru mein: kaam, photos, location map, aur call / WhatsApp button.",
                 "Website se pehle Google Business Profile banao. Ye free hai aur sabse zyada customer wahin se aate hai.",
                 "Domain apne naam pe lo (.com ya .in), taaki baad mein kabhi phanse nahi.",
                 "Phone pe test karo. 80% log phone se hi dekhenge."],
                "Agla step: aapke business ke liye page ka plan aur ek free sample idea.")
    return ([f"For a {biz}, one simple page is enough to start: services, photos, location map, and call / WhatsApp buttons.",
             "Set up a Google Business Profile first. It's free and brings most local customers.",
             "Register the domain in your own name (.com or local), so you never get locked out.",
             "Test it on a phone. Most visitors will be on mobile."],
            "Next step: a page plan for your business and a free sample idea.")


def customer_followup(s: dict, hi: bool) -> tuple[list[str], str]:
    ch = _g(s, "channel", "WhatsApp")
    if hi:
        return ([f"Pehle saare customers ek jagah lao (Google Sheet): naam, {ch} number / email, aakhri visit ki date.",
                 "3 message fix karo: visit ke baad thank you, 30 din baad reminder, aur payment reminder.",
                 f"{ch} pe bina permission bulk message mat bhejo, number block ho sakta hai. Sirf apne customers ko bhejo.",
                 "Ye sab automatic chal sakta hai: date aate hi message apne aap chala jaaye."],
                "Agla step: aapki customer list ka format aur pehle 3 messages ka draft.")
    return ([f"Put all customers in one place (a Google Sheet): name, {ch} contact, last visit date.",
             "Set 3 messages: a thank-you after a visit, a reminder after 30 days, and a payment reminder.",
             f"Don't bulk-message on {ch} without permission; numbers get blocked. Message only your own customers.",
             "All of this can run automatically: when a date comes, the message goes out by itself."],
            "Next step: your customer list format and drafts of the first 3 messages.")


def find_work(s: dict, hi: bool) -> tuple[list[str], str]:
    sk, wt = _g(s, "skill", "aapki skill" if hi else "your skill"), s.get("work_type", "both")
    if hi:
        pts = [f"Ek clear line banao aap kya karte ho, jaise \"{sk} developer jo slow ya broken websites theek karta hai\".",
               "2-3 kaam ka chhota portfolio banao: pehle aur baad ka screenshot, aur result."]
        if wt in ("freelance", "both"):
            pts.append("Freelance ke liye: roz 5 businesses ko seedha message karo jinki website mein problem hai. Platform pe sirf bids se zyada kaam milta hai.")
        if wt in ("job", "both"):
            pts.append("Job ke liye: LinkedIn pe har din 5 relevant posts pe comment aur 5 recruiters ko short message.")
        return pts, "Agla step: aapki skill ke hisaab se pehle 10 target clients ya companies ki list."
    pts = [f"Write one clear line about what you do, e.g. \"{sk} developer who fixes slow or broken websites\".",
           "Build a small portfolio of 2-3 jobs: before/after screenshots and the result."]
    if wt in ("freelance", "both"):
        pts.append("For freelance: message 5 businesses a day whose websites have visible problems. Direct outreach beats bidding.")
    if wt in ("job", "both"):
        pts.append("For a job: comment on 5 relevant LinkedIn posts and message 5 recruiters every day.")
    return pts, "Next step: a list of the first 10 target clients or companies for your skill."


def vehicle_problem(s: dict, hi: bool) -> tuple[list[str], str]:
    sym = s.get("symptom", "")
    if hi:
        tips = {"won't start": ["Chabi ghumane pe 'click' ki awaz aaye ya lights dim ho to battery down hai: jump start ya battery check karwao.",
                                "Engine ghoomta hai par start nahi hota to fuel ya spark ki problem ho sakti hai: fuel level check karo."],
                "overheating": ["Turant gadi band karo aur engine thanda hone do. Coolant level check karo (garam mein cap mat kholo)."],
                "brake problem": ["Brake ki problem mein gadi mat chalao. Seedha mechanic ko bulao."]}
        pts = tips.get(sym, ["Problem kab hoti hai likh lo: thanda engine, garam engine, ya chalte hue. Mechanic ko ye batane se jaldi pakda jaata hai."])
        return pts + ["Agar safety ka sawaal hai (brake, steering, dhuaan), to gadi mat chalao."], "Agla step: paas ke bharosemand mechanic se check karwana."
    tips = {"won't start": ["A 'click' or dim lights when you turn the key usually means a weak battery: jump-start it or test the battery.",
                            "If the engine cranks but won't start, it may be fuel or spark: check the fuel level first."],
            "overheating": ["Stop and let the engine cool. Check the coolant level (never open the cap when hot)."],
            "brake problem": ["Don't drive with a brake problem. Call a mechanic to you."]}
    pts = tips.get(sym, ["Note when it happens: cold engine, hot engine, or while driving. It helps a mechanic find it fast."])
    return pts + ["If it's a safety issue (brakes, steering, smoke), don't drive it."], "Next step: get it checked by a trusted nearby mechanic."


def marketing_social(s: dict, hi: bool) -> tuple[list[str], str]:
    pf = _g(s, "platform", "Google")
    if hi:
        return ([f"{pf} pe ek hi cheez regular karo: hafte mein 3 post, har post mein ek kaam ya customer ki asli photo.",
                 "Har khush customer se review maango. Reviews se zyada bharosa aata hai, likes se nahi.",
                 "Ads se pehle profile poora karo: sahi phone, timing, location aur WhatsApp button.",
                 "Har mahine dekho kahan se sabse zyada call / message aaye. Wahin zyada mehnat karo."],
                f"Agla step: {pf} ke liye 4 hafte ka simple post plan.")
    return ([f"Do one thing consistently on {pf}: 3 posts a week, each with a real photo of your work or a customer.",
             "Ask every happy customer for a review. Reviews build trust more than likes.",
             "Before ads, complete your profile: phone, hours, location, and a WhatsApp button.",
             "Each month, check where most calls and messages came from, and put more effort there."],
            f"Next step: a simple 4-week post plan for {pf}.")


def payments_collection(s: dict, hi: bool) -> tuple[list[str], str]:
    issue = s.get("payment_issue", "")
    if hi:
        pts = ["Har payment ki due date likh ke rakho (Google Sheet ya app). Jo likha nahi, wo bhool jaata hai.",
               "Due date se 2 din pehle aur 1 din baad automatic reminder bhejo, politely.",
               "UPI QR aur payment link bill ke saath hi bhejo, taaki dena aasaan ho."]
        if issue == "late payments":
            pts.append("Naye customers se advance lo ya kaam ko hisso mein bill karo.")
        return pts, "Agla step: pending payments ki list aur reminder messages ka draft."
    pts = ["Record every due date (a Google Sheet or app). What isn't written down gets forgotten.",
           "Send polite automatic reminders 2 days before and 1 day after the due date.",
           "Send a UPI QR or payment link with every bill, so paying is easy."]
    if issue == "late payments":
        pts.append("Take an advance from new customers, or bill work in stages.")
    return pts, "Next step: a list of pending payments and reminder message drafts."


def hiring_staff(s: dict, hi: bool) -> tuple[list[str], str]:
    role = _g(s, "role", "staff")
    if hi:
        return ([f"{role} ke liye 3 line ka clear kaam likho: kya karna hai, timing, salary range.",
                 "Apne best customers aur staff se refer karne ko kaho. Reference wale log zyada tikte hai.",
                 "Pehle 1 hafte trial pe rakho, phir pakka karo.",
                 "Har mahine 10 minute har staff se baat karo. Log zyada tar isliye chhodte hai kyunki koi poochta nahi."],
                f"Agla step: {role} ke liye job post ka draft.")
    return ([f"Write a clear 3-line job for the {role}: duties, hours, salary range.",
             "Ask your best customers and staff for referrals. Referred people stay longer.",
             "Start with a 1-week paid trial before confirming.",
             "Spend 10 minutes a month with each person. Most people leave because nobody checks in."],
            f"Next step: a job post draft for the {role}.")


def learn_skill(s: dict, hi: bool) -> tuple[list[str], str]:
    tp = _g(s, "topic", "skill")
    if hi:
        return ([f"{tp} ke liye ek hi free course chuno aur poora khatam karo, 5 course adhoore chhodne se better.",
                 "Roz 45 minute fix karo, ek hi time pe.",
                 "Har hafte ek chhoti cheez bana ke dikhao. Seekhna banane se hota hai, sirf dekhne se nahi."],
                f"Agla step: {tp} ka 4 hafte ka plan.")
    return ([f"Pick one free {tp} course and finish it; that beats starting five.",
             "Fix 45 minutes a day at the same time.",
             "Build one small thing every week. You learn by making, not watching."],
            f"Next step: a 4-week {tp} plan.")


PLAYBOOKS: dict[str, Callable[[dict, bool], tuple[list[str], str]]] = {
    "business_slow": business_slow, "no_customers": no_customers, "website_problem": website_problem,
    "need_website": need_website, "customer_followup": customer_followup, "find_work": find_work,
    "vehicle_problem": vehicle_problem, "marketing_social": marketing_social,
    "payments_collection": payments_collection, "hiring_staff": hiring_staff, "learn_skill": learn_skill,
}
