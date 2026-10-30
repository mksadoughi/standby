// What's said in the story film (story.html), one line each: who says it, in which of Standby's own voices (Pocket
// TTS), and when it starts. story.html shows these as captions; tools/demo/voiceover.py speaks them into the
// soundtrack (saying "say" instead of "text" where the words are spelled differently aloud). `at` and `dur` come from each clip's length (voiceover.py --measure), so the words and the pictures agree.
// Kept as plain JSON after the "=" so voiceover.py can read it.
// The opening is held: from STORY_HOLD[0] s on, everything (these lines too) comes STORY_HOLD[1] s later (no hold now).
var STORY_HOLD = [6.9, 2.0];
var STORY_LINES = [
  {"id": "c1", "who": "Claude", "voice": "alba", "at": 15.2, "text": "Hey! Sure, let's go over it.", "dur": 2.2},
  {"id": "y2", "who": "You", "voice": "jean", "at": 17.8, "text": "The header feels heavy, and the sign-up button gets lost.", "dur": 2.7},
  {"id": "c3", "who": "Claude", "voice": "alba", "at": 21.0, "text": "Agreed. A lighter header, and the button in the accent colour?", "dur": 3.8},
  {"id": "y4", "who": "You", "voice": "jean", "at": 25.3, "text": "Yes. And let's see it in dark mode too.", "dur": 2.6},
  {"id": "c5", "who": "Claude", "voice": "alba", "at": 28.3, "text": "On it.", "dur": 0.8},
  {"id": "c6", "who": "Claude", "voice": "alba", "at": 31.8, "text": "Done. Have a look.", "dur": 1.4},
  {"id": "n1", "who": "Nadia", "voice": "fantine", "at": 41.0, "text": "The new page looks great.", "dur": 1.8},
  {"id": "n3", "who": "Nadia", "voice": "fantine", "at": 43.1, "text": "Can we launch it Friday?", "dur": 1.4},
  {"id": "t3", "who": "Theo", "voice": "marius", "at": 44.9, "text": "Only if sign-up is done.", "dur": 1.2}
];
