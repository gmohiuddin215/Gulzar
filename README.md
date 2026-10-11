# Gulzar Nastaliq

Gulzar Nastaliq is a modified version of [Gulzar](https://github.com/googlefonts/gulzar), reworked to look more like traditional Urdu Nastaliq as it is written and printed in books. Every outline is Gulzar's own, reshaped by `scripts/nastaliq-reshape.py`, and the shaping engine is Gulzar's. It is released, like Gulzar, under the SIL Open Font License 1.1.

## What has changed

### A more traditional Nastaliq style

* Proportions closer to traditional Urdu Nastaliq: lower, rounder bowls, taller ascenders, a heavier pen with more thick-and-thin contrast, and tighter word spacing.
* Shorter kaf and gaf where they used to stand too tall (کی، گی، کیے، گئے، اگر، مگر، کم، گھنٹوں), with the upright stem shortened and the sarkash keeping its length. A medial kaf or gaf comes down only where no dots or vowel marks sit under its sarkash (فنگر، شکر), and کیا، گیا keep a taller kaf.
* A slightly lower laam before kaf and gaf (لکھا، لگا، ملک، الگ), so it no longer towers over the kaf.
* Smoother joins between letters: into laam, dal and ye, and the sad and zad joins (صحیح، صل).
* Rounded pen cuts and stroke ends instead of hard straight edges, including the flat side of the fe and qaf heads.
* Better spacing after letters that do not join (د ذ ر ز و), so words such as ذخیرہ، دین، مؤطا، دیہی read as one word.

### Dots and marks

* Many dots placed closer to their letters and clear of each other, for example in نقل، یحیی، آغاز، ادیبوں، نشیبی، مستقبل، کابینہ، بست، تربیت، نئی، تقریباً.
* Each dot change is checked against the shaping and collision tests, so dots that move closer do not run into other letters, dots or words.

### Honorifics

Fifteen honorific ligatures, drawn from the font's own letters with full harakat and sized to match each other:

| Honorific | Code points |
|---|---|
| صلی اللہ علیہ وسلم | U+FDFA |
| صلی اللہ علیہ وآلہٖ وسلم | U+FD4C |
| رضی اللہ عنہ / عنہا / عنہما / عنہم / عنہن | U+0613, U+FD41, U+FD42, U+FD44, U+FD43, U+FD45 |
| رحمۃ اللہ علیہ | U+0612 |
| عزوجل | U+FDFF |
| قدس سرہ | U+FD4B |
| علیہ السلام / علیہم / علیہما / علیہا السلام | U+0611, U+FD47, U+FD48, U+FD49, U+FD4D |
| علیہ الصلوۃ والسلام | U+FD4A |

Typed phrases turn into these glyphs automatically: write صلی اللہ علیہ وسلم (with or without harakat, with or without spaces between the words, with Urdu or Arabic ye and he) and it is shown as the single honorific. A phrase that is only part of a longer word (سلمان، اللہیار) is left alone.

### Optimised and tested

* Shaping and collision tests run over the regression suite, a large Urdu word corpus and full book pages.
* `qa/book-page.py` sets a page of text like a printed book page (justified lines, running head, verse centred), and can report lines whose ink touches, to judge the font for book printing.
* `qa/shaping-report.py` prints collision counts so two builds can be compared.

The rest of this file is Gulzar's original documentation.

Gulzar is a Nastaliq font for Urdu text with a typographic, rather than calligraphic feel.

## Note to other font designers

We hope that Gulzar will serve as a reference and model for other Nastaliq font designs, in particular in terms of its engineering and shaping rules. If you are looking to design a Nastaliq font, you will find some documentation on the Gulzar system and how it works in [engineering.md](engineering.md).

## Set up instructions

Only needs to be done once:

```
sudo pip3 install -r requirements.txt
```

## Build instructions

Don't try exporting from Glyphs! It's not going to work!

* Run `make`
* Output can be found in `fonts/ttf/GulzarNastaliq-Regular.ttf`

## Makefile targets

* `make` produces a debug version of the font.
* `make release` produces a version of the font suitable for release.
* `make test-shaping` checks the shaping rules and produces a report in `qa/shaping_tests/report.html`.
* `make test` runs a full fontbakery check on the font.
* `make proof` produces `urdu-john.pdf`, a proof of the font.
* `make testproof` produces `test-failures.pdf`, PDF containing words with collisions.

## Fixing dot collisions

If you find a colliding sequence between two "words" (i.e. final glyph -> initial glyph + dots), you need to:

* Add the sequence to `qa/shaping_tests/collisions.json` so it can be tested.
* Create a debug build of the font; the `Makefile` by default turns on `FONTTOOLS_LOOKUP_DEBUGGING=1` which creates a `Debg` table in the final font. This is stripped when building the release version.
* Strip the sequence down to the smallest possible string which collides. Paste that string into [Crowbar](http://www.corvelsoftware.co.uk/crowbar/) with the debug build of the font.
* Look down the list of trigged lookups to determine the sequence height. The list will include lookup names like `At_600_800_AtHeight600sub` and `At_600_800_AtHeight600pos` - this tells you the height-specific lookup routines you need to modify to mitigate the collision.
* Find those lookups in `anchor-attachment.fez`.
* Decide how you want to mitigate the collision. You can either:
    *  drop the dot one position (call `DropOne` in the `sub` lookup)
    *  drop it two positions (`DropTwo` in `sub`)
* and/or:
    *  open a short space (`OpenSmallSpaceBeforeKern` in `pos`) 
    *  open a large space (`OpenSpaceBeforeKern` in `pos)`
    *  bring the final glyph closer to the initial glyph (`Tighten` in `pos`)
    *  move the dots down a little further (`DropATinyBitMore`) or a lot further (`DropALotMore`, both in `pos`)
    *  raise the dots a little (`Raise` in `pos`)
    *  or do something more fancy, and create a new routine to reposition the dots elsewhere.
* Add a Chain rule call the appropriate routine(s). This is a bit of an art form. It should be as general as possible to avoid proliferating unnecessary rules, but as specific as possible to avoid interacting with other sequences in bad ways. Some hints:
    * Try to use the shape-based classes (e.g. `@dal_like`) to address the final glyph and all other glyphs like it.
    * Check out other similar sequences to see how best to address the initial glyph.
    * Consider using `DoNothing` and `DoNothingPos` (ignore rules) to create exceptions for existing situations.
* Rebuild the font and run `make test-shaping` to make sure there are no unwanted interactions with other sequences.

## Scripts

This repository also contains FEZ plugins for Nastaliq as well as scripts to build the font file:

* `dump-glyphs-rules.py` turns the Nastaliq Connection information within the `.glyphs` file into a CSV file so that it can be processed by the `nastaliqTools.NastaliqConnections` plugin.
* `find-notdefs.py` finds characters not currently mapped in the font.
* `lint-arabic.py` finds missing entry/exit anchors (and will, in the future, also detect other anchor issues.)
