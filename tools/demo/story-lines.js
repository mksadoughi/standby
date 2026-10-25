// What's said in the story film (story.html), one line each: who says it, in which of Standby's own voices (Pocket
// TTS), and when it starts. story.html shows these as captions; tools/demo/voiceover.py speaks them into the
// soundtrack (saying "say" instead of "text" where the words are spelled differently aloud). `at` and `dur` come from each clip's length (voiceover.py --measure), so the words and the pictures agree.
// Kept as plain JSON after the "=" so voiceover.py can read it.
// The opening is held: from STORY_HOLD[0] s on, everything (these lines too) comes STORY_HOLD[1] s later (no hold now).
var STORY_HOLD = [6.9, 2.0];
var STORY_LINES = [
  {"id": "c1", "who": "Claude", "voice": "alba", "at": 15.2, "text": "Hey! Good idea, it's easier to talk it through.", "dur": 3.2},
  {"id": "y2", "who": "You", "voice": "jean", "at": 18.55, "text": "Yes. I'm thinking guests could share the route cache. What do you think?", "dur": 4.2},
  {"id": "c3", "who": "Claude", "voice": "alba", "at": 22.95, "text": "I'd keep one per region. Sharing it would leak fares between countries.", "dur": 4.1},
  {"id": "y4", "who": "You", "voice": "jean", "at": 27.25, "text": "Good point. Okay, build that and run the tests.", "dur": 2.2},
  {"id": "c5", "who": "Claude", "voice": "alba", "at": 29.65, "text": "On it.", "dur": 0.8},
  {"id": "n1", "who": "Nadia", "voice": "fantine", "at": 41.0, "text": "Guests pay by region.", "dur": 1.5},
  {"id": "n3", "who": "Nadia", "voice": "fantine", "at": 42.9, "text": "Is the region in the fares key?", "dur": 2.0},
  {"id": "t3", "who": "Theo", "voice": "marius", "at": 45.2, "text": "I don't think so, but I'd check.", "say": "I don't think so. But I would check.", "dur": 1.9}
];
