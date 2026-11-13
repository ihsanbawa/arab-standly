import unicodedata


ROASTS = [
    ("يَا مَلِكَ الْأَعْذَارِ!", "O king of excuses!", "عُذْرٌ = excuse · أَعْذَارٌ = excuses"),
    ("أَيْنَ أَنْتَ؟ حَتَّى كِتَابُكَ يَسْأَلُ عَنْكَ!", "Where are you? Even your book is asking about you!", "أَيْنَ = where"),
    ("حَضَرَ عُذْرُكَ، وَأَنْتَ غَائِبٌ!", "Your excuse showed up, and you're absent!", "غَائِبٌ = absent"),
    ("الدَّرْسُ يَنْتَظِرُ، وَالنَّوْمُ يَنْتَصِرُ!", "Class is waiting, and sleep is winning!", "النَّوْمُ = sleep"),
]
ROASTS += [
    ("عُذْرُكَ أَطْوَلُ مِنْ دَرْسِ الْيَوْمِ!", "Your excuse is longer than today’s lesson!", "أَطْوَلُ = longer · عُذْرٌ = excuse"),
    ("نَحْتَاجُ إِلَى أَرْبَعَةِ طُلَّابٍ، لَا ثَلَاثَةٍ وَوِسَادَةٍ!", "We need four students, not three students and a pillow!", "وِسَادَةٌ = pillow · طُلَّابٌ = students"),
    ("الْكِتَابُ لَا يَعَضُّ. اِفْتَحْهُ!", "The book doesn’t bite. Open it!", "يَعَضُّ = bites · اِفْتَحْ = open!"),
    ("حَفِظْتَ جَدْوَلَ نَوْمِكَ وَنَسِيتَ جَدْوَلَ الدَّرْسِ!", "You memorised your sleep schedule and forgot the class schedule!", "جَدْوَلٌ = schedule · نَسِيتَ = you forgot"),
    ("هَلْ تَنْتَظِرُ دَعْوَةً مَلَكِيَّةً؟ الزِّرُّ أَمَامَكَ!", "Waiting for a royal invitation? The button is right there!", "دَعْوَةٌ = invitation · زِرٌّ = button"),
    ("رَدُّكَ يَحْتَاجُ إِلَى بَعْثَةِ بَحْثٍ!", "Your reply needs a search expedition!", "رَدٌّ = reply · بَحْثٌ = search"),
    ("الْمِنَبِّهُ يَعْمَلُ، لَكِنَّكَ فِي إِجَازَةٍ!", "The alarm is working. You’re the one on holiday!", "مُنَبِّهٌ = alarm · إِجَازَةٌ = holiday"),
    ("أَرْسَلْنَا الْمَوْعِدَ، لَا لُغْزًا!", "We sent you a time, not a riddle!", "مَوْعِدٌ = appointment · لُغْزٌ = riddle"),
    ("كُرْسِيُّكَ أَكْثَرُ حُضُورًا مِنْكَ!", "Your chair has better attendance than you!", "كُرْسِيٌّ = chair · حُضُورٌ = attendance"),
    ("لَوْ كَانَتِ الْأَعْذَارُ وَاجِبًا، لَأَخَذْتَ الدَّرَجَةَ الْكَامِلَةَ!", "If excuses were homework, you’d get full marks!", "وَاجِبٌ = homework · دَرَجَةٌ = mark"),
    ("حَتَّى السُّلَحْفَاةُ ضَغَطَتْ عَلَى الزِّرِّ قَبْلَكَ!", "Even the tortoise pressed the button before you!", "سُلَحْفَاةٌ = tortoise · قَبْلَكَ = before you"),
    ("كِتَابُكَ يَظُنُّ أَنَّهُ قِطْعَةُ أَثَاثٍ!", "Your book thinks it’s a piece of furniture!", "أَثَاثٌ = furniture · يَظُنُّ = thinks"),
    ("حَضَرَتِ الْقَهْوَةُ. أَيْنَ الطَّالِبُ؟", "The coffee showed up. Where’s the student?", "قَهْوَةٌ = coffee · طَالِبٌ = student"),
    ("مَا شَاءَ اللهُ، عِنْدَكَ مَكْتَبَةٌ مِنَ الْأَعْذَارِ!", "You’ve built an entire library of excuses!", "مَكْتَبَةٌ = library · أَعْذَارٌ = excuses"),
    ("هَذَا دَرْسُ عَرَبِيَّةٍ، لَا مُسَابَقَةُ اخْتِفَاءٍ!", "This is Arabic class, not a disappearing competition!", "اِخْتِفَاءٌ = disappearing · مُسَابَقَةٌ = competition"),
    ("الْغِيَابُ لَيْسَ طَرِيقَةً جَدِيدَةً لِلْمُرَاجَعَةِ!", "Being absent isn’t a new revision technique!", "غِيَابٌ = absence · مُرَاجَعَةٌ = revision"),
    ("جَوَابٌ مِنْ كَلِمَةٍ وَاحِدَةٍ، وَالِانْتِظَارُ رِوَايَةٌ!", "A one-word answer. A whole novel’s worth of waiting!", "كَلِمَةٌ = word · رِوَايَةٌ = novel"),
    ("مَخَدَّتُكَ لَنْ تَحُلَّ التَّمَارِينَ عَنْكَ!", "Your pillow isn’t going to do the exercises for you!", "مَخَدَّةٌ = pillow · تَمَارِينُ = exercises"),
    ("نَحْنُ نَتَعَلَّمُ الْعَرَبِيَّةَ، وَأَنْتَ تَتَقِنُ التَّأْجِيلَ!", "We’re learning Arabic. You’re mastering postponement!", "تَأْجِيلٌ = postponement · تَتْقِنُ = you master"),
    ("الْحُرُوفُ ثَمَانِيَةٌ وَعِشْرُونَ، وَأَعْذَارُكَ بِلَا عَدَدٍ!", "Twenty-eight letters. Your excuses have no limit!", "حُرُوفٌ = letters · عَدَدٌ = number"),
]
NUDGE = "حَتَّى جَوَابُكَ غَائِبٌ! — Even your reply is absent. The button is right there, professor. Please RSVP."
RESOURCES = [
    ("Chapters 1–3 cumulative", "https://quizlet.com/1207451326/chapter-1-2-3-cumulative-flash-cards/"),
    (
        "Chapter 4 vocabulary",
        "https://quizlet.com/1202716889/chapter-04-vocabulary-ihsan-hammad-flash-cards/",
    ),
    ("IACC official prayer schedule", "https://planomasjid.org/iacc-prayer-timings-2026/"),
]


def roast(index,store=None,uid=0):
    if store is not None:
        key=f'roast:cursor:{uid}'
        cursor=store.get(key,index % len(ROASTS))
        index=cursor % len(ROASTS)
        store.set(key,cursor+1)
    arabic, english, note = ROASTS[index % len(ROASTS)]
    return f"**{arabic}**\n||{english}||\n{note}"


# Second-text insults. Plain ASCII on purpose: one curly quote turns a cheap SMS into three segments.
BURNS = [
    "Three people managed one button. You are being outperformed by a thumb.",
    "Your phone works. We checked. The problem is you.",
    "You left the class on read. The class noticed. The class is judging.",
    "Even the duck has given up on you, and the duck is a cartoon.",
    "At this rate Arabic will be a dead language before you reply.",
    "Toddlers learn alif ba ta faster than you answer one yes-or-no question.",
    "You are the reason this group has a telephone department.",
    "Somewhere a pillow is very proud of you. Nobody else is.",
    "Your streak is not the only thing in here that looks dead.",
    "Your excuses have better attendance than you do.",
    "A first-week student reads faster than you reply.",
    "This is the second text. A third would waste a perfectly good insult.",
]


def _rotate(store, key, items):
    cursor = store.get(key, 0)
    store.set(key, cursor + 1)
    return items[cursor % len(items)]


def ascii_sms(text):
    for fancy, plain in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("—", "-"), ("–", "-"), ("…", "...")):
        text = text.replace(fancy, plain)
    return text


def plain_roast(store, uid):
    """English half of a classroom roast, for the first text."""
    return ascii_sms(_rotate(store, f'roast:sms:{uid}', ROASTS)[1])


def burn(store, uid):
    """A harsher line for the follow-up text."""
    return _rotate(store, f'roast:burn:{uid}', BURNS)


def strip_harakat(text):
    """Remove Arabic vowel marks for SMS; keep letters and the hamza/maddah marks that are spelling."""
    keep = {"\u0653", "\u0654", "\u0655"}
    return "".join(
        ch for ch in unicodedata.normalize("NFC", text)
        if not ("\u0600" <= ch <= "\u06ff" and unicodedata.category(ch) == "Mn" and ch not in keep)
    )


def sms_roast(store, uid):
    """Next roast as (Arabic without vowel marks, ASCII English) for a text; the vocabulary note is left out."""
    arabic, english, _note = _rotate(store, f'roast:sms:{uid}', ROASTS)
    return strip_harakat(arabic), ascii_sms(english)
