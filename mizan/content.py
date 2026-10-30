ROASTS = [
    ("يَا مَلِكَ الْأَعْذَارِ!", "O king of excuses!", "عُذْرٌ = excuse · أَعْذَارٌ = excuses"),
    ("أَيْنَ أَنْتَ؟ حَتَّى كِتَابُكَ يَسْأَلُ عَنْكَ!", "Where are you? Even your book is asking about you!", "أَيْنَ = where"),
    ("حَضَرَ عُذْرُكَ، وَأَنْتَ غَائِبٌ!", "Your excuse showed up, and you're absent!", "غَائِبٌ = absent"),
    ("الدَّرْسُ يَنْتَظِرُ، وَالنَّوْمُ يَنْتَصِرُ!", "Class is waiting, and sleep is winning!", "النَّوْمُ = sleep"),
]
NUDGE = "حَتَّى جَوَابُكَ غَائِبٌ! — Even your reply is absent. Please RSVP."
RESOURCES = [
    ("Chapters 1–3 cumulative", "https://quizlet.com/1207451326/chapter-1-2-3-cumulative-flash-cards/"),
    (
        "Chapter 4 vocabulary",
        "https://quizlet.com/1202716889/chapter-04-vocabulary-ihsan-hammad-flash-cards/",
    ),
    ("IACC official prayer schedule", "https://planomasjid.org/iacc-prayer-timings-2026/"),
]


def roast(index):
    arabic, english, note = ROASTS[index % len(ROASTS)]
    return f"**{arabic}**\n||{english}||\n{note}"
