# Word-level issues found in Gulzar Nastaliq 2.0

This list comes from setting a large list of real Urdu word forms in the font and looking at them one by one, zoomed in. It is a worklist for later fixing; nothing here has been changed in the font yet.

## What was checked

* **17,546 connected word forms** (every run of joined letters in the word list, one to eleven letters long) were set in the font.
* **2,457 of them were reviewed by eye, sheet by sheet** (205 sheets of 12 words). The 2,457 were chosen so that every one of the 3,733 distinct letter-to-letter joins that occur appears at least once, so a faulty join cannot hide.
* **All 17,546 were also checked automatically** for dots that touch other dots and dots that cover a letter body.
* Suspicious spots were opened at high zoom (see `issues/body-shape-examples.png`).

## Summary

* **Letter bodies and joins are in good shape.** Across all reviewed words there are no broken connections, no hairline gaps and no squashed or deformed letters. The only body-shape defects are the four small ones in section A.
* **Almost all remaining problems are dot and mark placement** (section B): dots land on neighbouring letters or on each other.

## A. Letter-body defects (few, confirmed at zoom)

| # | Where | What | Example words |
|---|---|---|---|
| A1 | gaf followed by mim (GAFi9 and gaf + MIMm*) | The gaf's vertical stem outline is faceted (stair-stepped) and there is a small knob where it meets the mim. | گم گما گمر گمو گمی گمگا گمے گمتا گمشد |
| A2 | gaf joined to gaf (GAFm*, GAFisd3, GAFi8) | Two stacked top bars crowd into a double slash and a thin sliver or wedge sticks up at the join. | گگ |
| A3 | medial ہ (HAYCm2, HAYCm3, HAYCm6) | A thin pointed spike hangs down at the cusp of the join. A sharp wedge also appears at HAYCm3 into GAFi2 and at HAYCm2 into TEm6. | کنہیا کیہل فیہا کمہا گیہا ہپہپا |
| A4 | final mim (MIMf1) | The tail drops straight down as a long thin stem with a blunt tip, much longer and straighter than the other final tails. Seen in every word ending in mim after a joined letter. | بضم بکم بم تبسم تحکیم جم تیم سم |

## B. Dots and marks

Counts are from the automatic check over all 17,546 forms: **395 words** have a mark covering a letter body and **272 words** have marks touching each other. Per-pair tables are in `issues/dots-on-letter-bodies.txt` and `issues/dots-touching-dots.txt`; every affected word is listed in `issues/all-affected-words.txt`. Pictures: `issues/dots-on-letter-bodies.png`, `issues/dots-touching-dots.png`.

### B1. Dots land on the black body of the next letter

| # | Marks and letters | Words | Examples |
|---|---|---|---|
| B1a | two dots below medial ی (ddb on TEm*/TEmsd*) hit the next letter: final ی (CH_YEf), medial ہ (HAYAm), ج (JIMm/JIMf), ہ (HAYCm), final م (MIMf) | about 190 | بنینی بھیبھا سیٹھا سینچو تیہا تیمم |
| B1b | three dots of ش (tda on SINi) sit on the stem of a following ک or گ | about 47 | شکتی شگتے شکنا تشکک |
| B1c | two dots of ق/ت (dda on TEm) sit on the stem of a following ل, گ, ک | about 25 | قتلو قتلے قتلہ فتگی ستمگر ختک |
| B1d | single dot of ن/ب/ج/غ/ظ (sda, sdb) over a following ک, گ, ط, ع | about 15 | غمگین بطخا لمبلع |
| B1e | toeda over ٹ (TEm) reaches into the next ک or ج | about 10 | چٹکا بٹھک |
| B1f | dots of initial پ (tdb on TEi*) land on or touch the next letter (ج, س, ر, ک, ل, ہ) | many; clear in about 12 sheets | چپچپے پنچم |

### B2. Dots touching each other

| # | Pair | Words | Examples |
|---|---|---|---|
| B2a | ی dots under ی dots (ddb + ddb) | 68 | بہیمیت تسمیتین تعلیمیا |
| B2b | ی dots with پ dots (ddb + tdb) | 21 | بغچیا بقچیا |
| B2c | ب dot with ی dots (sdb + ddb) | 20 | فیبر کیبنٹ عیبیو |
| B2d | haydb (the small mark under ہ) with ی dots | 18 | بیہقی بیہو بیہڑ |
| B2e | upper dots of ت/ق with a single dot (dda + sda) | 35 | ستفا بتنگ جتنا |
| B2f | ب dot with پ dots (sdb + tdb) | 11 | پبل پبلشر |
| B2g | two single dots (sda + sda) | 10 | تغذ جننگ |
| B2h | toeda with a single dot | 12 | بٹنیو کٹنیا |
| B2i | ش dots with ق dots | few | لقشیر قشی قشقے |

### B3. Other mark placement

* **B3a. haydb floats far below and to the right of its ہ**, and then sits inside the neighbouring dot groups. The owner of the mark becomes unclear and it touches dots in many words (بیہقی, علیہا, فیہا, ہپنا, سعہم). In قہقہے it touches the underside of the endOfBariYe tail.
* **B3b. toeda floats high and far left of its ٹ/ڈ/ڑ** in final positions, so it is unclear which letter owns it (پلڑ, پینڑ, پپڑ, پگڈ, ہمپٹن, علیگڑ, غنگڑ); in منٹگمر it sits on the gaf body.
* **B3c. HAMZA_ABOVE floats loose** at the edge of the letter in some ئ words (قعئے).
* **B3d. Dots of چ and پ sit far to the lower right** of the body and look detached (قمچی, کپچہ, کپے); the pe dots crowd the REf/VAO bodies (کٹپر, کپلر, کپو).
* **B3e. Crowded dot chains** in long words with three or more dotted letters in a row (کمپیٹیبلیٹی, کمنیٹیٹر, پپیہے, چینگے, پیغمبر).

## Suggested order for fixing

1. B1a, B1b, B1c, B1f: the largest groups, and they cover letters on a body.
2. B2a to B2d: dots on dots.
3. A1 and A3: the visible spurs.
4. A4: shorter, curved final mim tail.
5. B3: haydb, toeda, hamza, and the long dot chains.

Each fix should be checked with the shaping and collision tests and by setting the affected word lists again.
