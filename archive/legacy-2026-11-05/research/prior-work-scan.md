# NFL Combine Fatigue — Prior-Work / Literature Reconnaissance

**Prepared for:** Conor
**Date:** 2026-10-08
**Purpose:** Scoping scan before deciding whether to pursue a "combine fatigue signal" research project (e.g., as an entry angle for the **2027 NFL Big Data Bowl**).
**Author:** Research subagent (read-only web research).

---

## 0. Method, scope and confidence conventions

**What I searched.** OpenAlex and Crossref REST APIs (peer-reviewed + preprints), arXiv, the DDG-lite / Bing / `r.jina.ai` search proxies for grey literature, Kaggle, `media.nfl.com`, `operations.nfl.com`, Pro-Football-Reference, nflverse docs, GitHub mirrors, and sports-science outlets.

**Tooling caveats (read this).**
- The configured `web_search` tool was **disabled** in this environment. I substituted: (a) DuckDuckGo-lite/Bing via `web_fetch` and the `r.jina.ai` reader proxy, and (b) direct OpenAlex/Crossref/arXiv API queries. Coverage is therefore good for indexed academic work and mainstream web pages, but I could **not** systematically crawl Google Scholar, X/Twitter threads, or paywalled journals (e.g. *The Athletic*, several JSCR papers are abstract-only). Treat "not found" as "not found by these methods," not "provably nonexistent."
- I did not install packages or modify existing project files. This file is the only artifact created.

**Confidence tags used below:**
- **[Established]** — multiple independent, peer-reviewed/primary sources agree.
- **[Primary-1]** — a single primary/official source (still authoritative).
- **[Secondary]** — aggregator/blog/journalism; directionally useful, verify before citing.
- **[Gap/uncertain]** — no direct evidence found; either genuinely unstudied or unindexed.

---

## 1. NFL Combine performance and its predictive validity

### 1.1 The core empirical picture

The headline finding across 20+ years is **consistent and unflattering**: combine physical tests are weak-to-null predictors of NFL game performance, while *college production* predicts better. This is now a genuinely settled body of work, not a single study.

- **Kuzmits & Adams (2008, *J. Strength & Conditioning Research*)** — 1999–2004 draftees at QB/RB/WR; "no consistent statistical relationship between combine tests and professional football performance, with the notable exception of sprint tests for running backs." [Established]. https://doi.org/10.1519/jsc.0b013e318185f09d
- **Lyons, Hoffman, Michel & Williams (2011, *Human Performance*)** — past (college) performance better predicts NFL performance than physical-ability tests; 40-yd, vertical, 20-yd shuttle and 3-cone have "limited validity." [Established]. https://doi.org/10.1080/08959285.2011.555218
- **Cook, Ryan, Snarr & Rossi (2020, *JSCR*)** — 2013–2017 combine attendees (n = 1,537) vs. next-year NFL performance. Of 35 correlations, most were weak (r < 0.3); three tests (vertical, bench, pro) explained only **≈2.6% of the variance** in year-1 game performance. [Established]. https://doi.org/10.1519/jsc.0000000000003676
- **Rishis, Johnston & Baker (2023, *Journal of Sports Sciences*)** — PRISMA **systematic review**: 1,954 records screened → 68 retained. "Mixed results of combine measures on future success… highlights the need for more research." This is the best single citation for "the literature is inconclusive." [Established]. https://doi.org/10.1080/02640414.2023.2207853
- **Szekely, Sinnott, Halow & Gregory (2023, arXiv:2303.05774)** — ML models predict **matriculation** (playing ≥1 NFL snap) with ~83% accuracy, but **fail to predict later success** (RMSE ≈ 1,210 snaps, R² = 0.17). [Primary-1]. https://doi.org/10.48550/arxiv.2303.05774
- **Doyle, Stanelle, Riechman & Mann (2026, *JSCR*)** — n = 3,681; PCA finds four components (momentum ≈26.5–36.1% variance, power ≈22.1–29.5%, COD ≈15.5–19.7%, strength ≈10–11.5%) that explain **within-position variance**, but the combine is "a **poor predictor of draft outcomes**" (weak AUC). [Primary-1]. https://doi.org/10.1519/jsc.0000000000005654

### 1.2 Where the combine *does* carry signal

- **Career longevity / roster status (defensive players).** Pollock et al. (2021, *Sports Medicine International Open*), n = defensive players 2005–2015: several drills associate with 1-year and 5-year status, strongest for linebackers and cornerbacks; safeties almost nothing. [Primary-1]. https://doi.org/10.1055/a-1485-0031
- **Draft position (not NFL success).** Applied work consistently finds athleticism gates *drafting* more than *production*. A Berkeley Sports Analytics study (Naini, 2023) using Relative Athletic Score (RAS) reports ~**9 draft picks per RAS point** and that nearly all Pro Bowlers score in the top ~10% of RAS — i.e. athleticism looks like a **minimum threshold** ("disqualifier") rather than a ranking metric, and elite athleticism correlates with *ceiling*, not floor. [Secondary]. https://sportsanalytics.berkeley.edu/articles/nfl-combine.html
- **Drill inter-correlation / redundancy.** Agar-Newman et al. (2024, *JSCR*), n = 4,149 (1999–2020): vertical + broad jump + height + mass predict 40-yd and split times with adjusted R² = 0.74–0.84. Useful if you need to *impute* opt-out drills. [Primary-1]. https://doi.org/10.1519/jsc.0000000000004799
- **Confounds exist beyond ability.** A PMC study ("State Population Influences Athletic Performance Combine Test Scores," 2019) shows combine scores correlate with non-ability factors. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6355118/ [Primary-1]

### 1.3 Critiques / known limitations

- **Terenzi (2019, *Research & Investigations in Sports Medicine*)** — critical review; recommends changing the battery (isometric mid-thigh pull, med-ball put, drop jump) and upgrading jump-testing technology; concludes single-event physical assessment may only partially predict NFL performance. [Primary-1]. https://doi.org/10.31031/rism.2019.05.000612
- **Voluntary participation / selection bias.** Testing is voluntary and opt-outs are common (Topend Sports; nflverse/PFR nulls). Any combine analysis inherits a **missing-not-at-random** problem — likely correlated with draft stock. [Established]. https://www.topendsports.com/sport/gridiron/nfl-draft.htm
- **Construct validity.** "Workout warrior" critiques and the reorganisation of weigh-ins (now right before on-field work to stop water-loading) show the measures are gameable. [Secondary]. https://www.essentiallysports.com/nfl-active-news-flagship-inside-the-nfl-combine-the-grueling-six-day-schedule-prospects-must-survive/
- Also relevant: *Frank et al. (2023, Journal of Expertise)* discriminant-function study over 20 years ("largest, most comprehensive") — https://www.journalofexpertise.org/articles/volume6_issue2/JoE_6_2_Frank_etal.pdf [Primary-1]; *The Sport Journal* (2024) re-test of 2022 data — https://thesportjournal.org/article/the-predictive-ability-of-the-physical-skills-used-at-the-nfl-combine-to-predict-draft-status/ [Secondary].
- *The Athletic* (Feb 2026) "What metrics actually matter at the NFL Scouting Combine?" reviews studies and 2017–2025 data — paywalled, headline confirms the framing. https://www.nytimes.com/athletic/7073861/ [Secondary/paywalled].

**Bottom line for §1:** the *predictive-validity* question is heavily worked. There is little room for novelty in "does the combine predict the NFL?" — but the *why/how* (mechanism) angle is under-explored.

---

## 2. Fatigue / order / sequence effects *within* the combine itself

**This is the crux, and the direct literature is essentially empty.** Repeated, differently-phrased searches (OpenAlex, Crossref, DDG/Bing, `r.jina.ai`) surfaced **no peer-reviewed study that models within-combine fatigue, drill-order effects, time-of-day effects, or repeated-attempt decrement.** The only "order-adjacent" hits were about *in-game* or *general* testing batteries, not the combine. [Gap/uncertain — but consistently empty across many queries.]

What *is* documented about the combine's structure (the raw material a fatigue study would exploit):

- **Multi-day, position-staggered structure.** ~320 invited prospects; on-field workouts across **four days**, each day a different set of position groups (2026: Feb 26–Mar 1). Because players are grouped by position, "combine day" is **confounded with position** — a key design constraint. [Primary-1]. https://www.topendsports.com/sport/gridiron/nfl-draft.htm ; https://www.essentiallysports.com/nfl-active-news-flagship-inside-the-nfl-combine-the-grueling-six-day-schedule-prospects-must-survive/
- **A gruelling six-day week** per prospect (interviews, medicals, media, workouts), with little rest — the qualitative basis for an accumulated-fatigue hypothesis. [Secondary]. Same EssentiallySports piece.
- **Repeated attempts within drills.** The 20-yd shuttle allows **three attempts, best selected** (Wikipedia); the 40-yd is run multiple times with best time accepted (commonly reported; exact attempt count varies by year). The bench press is explicitly a **strength-endurance** test (max reps at 225 lb) — itself a fatigue-decrement measurement, but nobody has analysed the *rep-to-rep* velocity decay from public data. [Established for shuttle/Wikipedia → https://en.wikipedia.org/wiki/NFL_Scouting_Combine ; bench "strength endurance" via Topend Sports.] **Flag:** exact current attempt counts and the precise within-day drill order are **not consistently documented in the sources I could fetch** — verify against the official NBDL data dictionary.
- **Weigh-in timing changed** (now immediately pre-workout), which materially affects acute hydration/body-mass state at testing — a plausible moderator of any fatigue signal. [Secondary].

**Important enabler (not a prior work):** the **2027 Big Data Bowl dataset contains the exact fields needed to study this for the first time** — `combine_tracking.csv` includes an `attempt` index per prospect per drill, a per-frame ISO-8601 `time` timestamp (10 Hz), and `event_id` linking frames within a single drill attempt (§3, §7). So the *measurement substrate for a within-combine fatigue/order study now exists publicly for the first time in 2026–27.*

> **Novelty signal:** "Does performance degrade as the combine progresses?" appears to be **unasked** in the published/visible record. That is the single most encouraging finding for this project — and simultaneously the biggest risk (see §6, §8).

---

## 3. Prior NFL Big Data Bowl entries touching combine / fatigue / load / tracking

### 3.1 The decisive fact: **2027 is the first combine-themed BDB**

- **2027 (9th annual) — theme: "Combine to NFL Performance."** Official: "build an analytical framework that uncovers non-obvious, actionable linkages between **10 Hz Combine sensor tracking data** and future regular-season NFL game performance." Start **Oct 6 2026**; **final deadline Jan 6 2027**; results ~Jan 26 2027. Two tracks (Open $45k, University $45k) + $10k grand prize; finalists present at the 2027 combine. Rubric: Football 30 / Data Science 30 / Writeup 20 / Viz 20, with two pass/fail gates (explicit Combine→NFL linkage; public Kaggle notebook + writeup). Positions: WR, TE, OL, DL, DB. [Primary-1]. https://www.kaggle.com/competitions/nfl-big-data-bowl-2027 ; https://frontofficesports.com/article/nfl-data-competition-will-focus-on-what-matters-at-scouting-combine/
- Therefore **no prior BDB entry used combine data at all.** The combine is an untouched dataset in the contest's history. That is a structural novelty advantage.

### 3.2 BDB history 2019–2026 (topic → data → winner)

Source for this table: the **chasko-labs/nfl-big-data-bowl-2027** community repo (`docs/history.md`, `docs/winners.md`), a **secondary aggregator** (compiled 2026-09-27, cross-referenced to primary sources in its `docs/references.md`). It should be treated as **[Secondary]** and spot-verified. https://github.com/chasko-labs/nfl-big-data-bowl-2027 — corroborating primary: https://operations.nfl.com/programs-initiatives/innovation/big-data-bowl

| Year | Theme | Winner (grand prize) | Trophy → broadcast lineage |
|---|---|---|---|
| 2019 | Open (pass offence / routes) | Sterken (RNN route ID); SFU "Routes to Success" | none documented [Secondary] |
| 2020 | Rushing yards at handoff | Singer & Gordeev ("The Zoo") | → **Expected Rushing Yards** on NGS within ~6 months; spawned RYOE [Secondary] |
| 2021 | Defensive pass coverage | Peng & Richards | → coverage classification / coverage responsibility lineage (attribution murky) [Secondary] |
| 2022 | Special teams (punts/kickoffs) | SFU (Ritchie et al.) "RAYE" punt-return metric | none documented [Secondary] |
| 2023 | Pass rush | Inayatali, White, Hocevar "Between the Lines" | → **Pressure Probability** [Secondary] |
| 2024 | Tackling | Chang, Dai, Jiang, Cheng | → **Tackle Probability** [Secondary] |
| 2025 | Pre-snap (coverage tells) | Bajaj & Sandwar (NYU) | TBD [Secondary] |
| 2026 | Player-movement prediction (ball in air) | Ferraz (Rice) "Ghostbusters" | first leaderboard/prediction year [Primary-1: https://operations.nfl.com/.../big-data-bowl] |
| **2027** | **Combine → NFL transfer** | — (open) | — |

### 3.3 Entries/metrics *adjacent* to "fatigue / load" (but not fatigue)

- **STRAIN (2023 finalist lineage → peer-reviewed).** Nguyen, Yurko & Matthews (2023, *The American Statistician*), "Here Comes the STRAIN." **Critically: "strain" here means materials-science strain-rate** (distance to QB + rate of closure) for pass-rush pressure — **not** physiological fatigue. Cite carefully so as not to conflate terminology. https://doi.org/10.1080/00031305.2023.2242442 ; https://ecommons.luc.edu/math_facpubs/47/ [Primary-1]
- **"Momentum" fractional tackles**, idpi, pre-snap motion, tackling/pursuit metrics — all **performance/quality** metrics, not within-session fatigue. [Secondary, chasko repo]
- **No BDB entry (2019–2026) addressed in-game physical load, player fatigue, or injury risk.** The chasko repo's own 2027 forecast even listed "injury / load / player safety" as *unlikely* for an open contest — **and its 2027 prediction was wrong** (it forecast ghost-player/counterfactual; the actual topic is combine). [Secondary]

**Bottom line for §3:** combine = virgin territory in the BDB; fatigue/load = virgin territory in the BDB. But because 2027 is *the* combine year, **the combine-tracking space will be crowded by Jan 2027** — novelty must come from a *specific, defensible angle* (fatigue/order is a strong candidate), not from "using combine data" per se.

---

## 4. Transfer / generalisation: do combine attributes (or fatigue proxies) persist into NFL game/tracking data?

- **Direct combine → NFL game performance.** Cook et al. (2020) is the cleanest test: ~2.6% of year-1 game-performance variance explained. [Established] (§1.1).
- **Combine → draft / longevity** (see §1.2) — real but modest, position-specific. [Primary-1]
- **The 2027 BDB is, by design, the first large-scale public attempt at combine-sensor → NFL-tracking transfer.** The task's own "Examples to consider" are essentially a research agenda: route-running/COD vs in-game separation; trench first-step vs pass-rush/run-block; sensor metrics vs stopwatch times; position-specific mechanics vs rookie performance. [Primary-1] https://www.kaggle.com/competitions/nfl-big-data-bowl-2027
- **Established baseline for in-game load (the "target" side).** Sánchez, Weiss, Williams, Ward, Peterson & Wellman (2023, *Applied Sciences*) quantify 2018–2020 NFL in-game movement demands by position (distance, max velocity, high-velocity and accel/decel efforts): WR/DB fastest/highest distance; LB most high-velocity efforts; linemen lowest. This gives position-specific velocity thresholds and a defensible **"expected in-game load"** benchmark. [Primary-1]. https://doi.org/10.3390/app13169278
- **Athlete-monitoring in American football.** Nocera et al. (2023, *Sensors*) PRISMA scoping review of physiological/biomechanical monitoring in American football; notes it is *injury-concussion-dominated* and explicitly flags a **gap in monitoring internal load**. [Primary-1]. https://doi.org/10.3390/s23073538
- **Load management in the NFL.** Short et al. (2025, *Int. J. Strength & Conditioning*) apply reverse acute:chronic workload ratio to NFL data — shows load-monitoring is entering NFL practice, but as training-load, not combine. [Primary-1]. https://doi.org/10.47206/hmrmy739
- **Speed persistence (ad hoc).** NGS publishes "top speed at 10 yards in 40-yard dash" and in-game top-speed leaderboards, but these are **illustrative, not validated transfer studies**. https://www.nfl.com/videos/top-speed-at-10-yards-in-40-yard-dash-next-gen-stats [Secondary]

**Bottom line for §4:** transfer evidence is thin and mostly predictive-validity (outcome), not mechanistic (movement signature). A study linking a *sensor-derived combine movement/fatigue signature* to *in-game tracking* would be genuinely new — and is exactly what 2027 rewards.

---

## 5. Adjacent sports: within-session fatigue measurement & modelling (methods you can borrow)

The combine-fatigue idea is a **within-session fatigue** problem. Other sports have mature methods:

**Repeated-sprint ability (RSA) & the fatigue-index debate — the closest analogue.**
- Girard, Mendez-Villanueva & Bishop (2011, *Sports Medicine*) "Repeated-Sprint Ability – Part I"; Bishop, Girard & Mendez-Villanueva (2011) "Part II." Foundational reviews of how sprint performance decays across repeated efforts and what physiological factors drive it. https://doi.org/10.2165/11590550-000000000-00000 ; https://doi.org/10.2165/11590560-000000000-00000 [Primary-1]
- **Oliver (2007, *J. Sci. Med. Sport*)** — "Is a fatigue index a worthwhile measure of repeated sprint ability?" Directly relevant **methodological caution**: simple percent-decrement indices are unreliable/confounded; argue for modelling the *shape* of decay, not a single index. https://doi.org/10.1016/j.jsams.2007.10.010 [Primary-1]
- Morin et al. (2011) on appropriate sprint dose; Thomas et al. (2018) on neuromuscular fatigue/recovery after resistance/jump/sprint; Mosier (2018/2024) on jump- and sprint-fatigue biomechanics; Staiano et al. (2023) mental fatigue impairs sprint/jump; Barte et al. (2018) motivation offsets fatigue decrements — all useful covariates/confounders. https://doi.org/10.1249/mss.0000000000001733 ; https://doi.org/10.1016/j.jsams.2023.10.016 ; https://doi.org/10.1080/02640414.2018.1548919 [Primary-1]

**Soccer (GPS/within-match).**
- Pimenta et al. (2025, *German J. Exercise & Sport Research* / *Frontiers*) — "Should GPS data be normalized for performance and fatigue monitoring in soccer?" Discusses sprint definition/normalisation for fatigue detection. https://doi.org/10.1007/s12662-025-01048-7 [Primary-1]
- Harper et al. (2019, *Sports Medicine*) — systematic review/meta of high-intensity accel/decel demands in elite team sports (a template for defining "effort" events). https://doi.org/10.1007/s40279-019-01170-1 [Primary-1]
- Acute/residual soccer match fatigue review (Springer) — https://link.springer.com/article/10.1007/s40279-017-0798-8 [Primary-1]

**Basketball (within-game).**
- Gómez et al. (2018, *J. Human Kinetics*) — "Shaq is Not Alone: Free-Throws in the Final Moments" — within-game fatigue/psych effects on a closed skill. https://doi.org/10.1515/hukin-2017-0165 [Primary-1]
- Pliauga et al. (2015, *J. Human Kinetics*) — simulated game effects on sprint & jump performance (pre/post design). https://doi.org/10.1515/hukin-2015-0045 [Primary-1]

**Rugby (conceptual grounding for "fatigue").**
- Naughton et al. (2023, *PLoS ONE*) — Delphi consensus **definition**: fatigue = "reduction in performance-related task ability underpinned by time-dependent negative changes within and between cognitive, neuromuscular, perceptual, physiological, emotional and technical domains." Excellent framing/measurement vocabulary. https://doi.org/10.1371/journal.pone.0282390 [Primary-1]
- Naughton et al. (2021, *Frontiers in Physiology*) — quantifying fatigue via collisions + neuromuscular/biochemical/self-report. https://doi.org/10.3389/fphys.2021.711634 [Primary-1]

**Tennis (within-session sequence — closest *analytical* analogue).**
- Fernández-Fernández et al. (2020, *IJERPH*) — "Within-Session Sequence of the Tennis Serve Training" — models how performance changes **across an ordered session**, i.e. exactly the drill-order logic you'd apply to a combine day. https://doi.org/10.3390/ijerph18010244 [Primary-1]

**Interpretation.** The dominant adjacent-sport lesson: **don't reduce fatigue to a single decrement index** (Oliver 2007); model the *trajectory* of performance across repeated efforts/attempts, condition on load, and separate neuromuscular vs. metabolic drivers (Wu et al., 2019, *PLoS ONE*, https://doi.org/10.1371/journal.pone.0219295). These map cleanly onto the 2027 combine-tracking data (per-attempt, per-frame speed/accel over an ordered session).

---

## 6. Gaps — what has NOT been done, novelty risk, and the opportunity

**Genuinely open (the opportunity):**
1. **No published within-combine fatigue/order effect analysis.** No study (found) tests whether 40-yd times, jump heights, or drill kinematics degrade with position in a session, time-of-day, or attempt number. [Gap]
2. **No combine *sensor* data has ever been analysed publicly before 2027.** Pre-2027 combine analysis was all summary numbers (stopwatch times, jumps). The 10 Hz optical tracking of *drill execution* (accel profiles, COD, deceleration into breaks) is brand new. [Established]
3. **No mechanistic combine→in-game movement-transfer study.** Prior transfer work is outcome-prediction (draft/snaps/salary), not movement-signature mapping. [Established]
4. **Bench press rep-velocity decay is unstudied from public data** — a ready-made within-drill fatigue probe (velocity loss across reps is a validated neuromuscular-fatigue marker in strength science). [Gap]

**Crowded / de-risked (avoid over-claiming novelty):**
- "Does the combine predict NFL success?" → **heavily saturated** (§1). Do not build the project here.
- "Combine numbers vs. draft position" → saturated.
- "Combine data + ML to predict outcomes" → saturated (Szekely, Doyle, many theses).

**Novelty risk / biggest threats:**
- **Timing race.** 2027 is *the* combine BDB; other teams will analyse combine-tracking. If the fatigue angle is obvious, a competing entry may reach it. The defensible moat is **rigour + a specific mechanism** (e.g., "velocity-based fatigue index from combine sensor data predicts NFL in-game high-intensity deceleration capacity"), not the broad theme.
- **Confounding is severe and is the real scientific risk.** Combine day = position group; drill order may be fixed (so *order* = *drill*, perfectly collinear); time-of-day correlates with position/schedule; opt-outs are non-random. Isolating a *fatigue* effect from *drill-type/difficulty* and *selection* effects is the hard part — and reviewers (NFL analytics staffers) will know it.
- **"Not it."** Absence of prior literature could mean (a) untapped, or (b) people quietly looked and found nothing actionable. The small n per drill-year and the fixed protocol make order-vs-drill-type identification genuinely hard — that is the likely reason it's unstudied. **This is the main reason to prototype before committing.**

**Opportunity statement (hypothesis to test):** *A within-combine fatigue/order signal exists in 10 Hz sensor kinematics (e.g., attempt-to-attempt 40-yd time and split decay, deceleration and COD degradation, bench velocity loss), it is partially separable from drill/position confounds, and its magnitude/persistence carries information about an athlete's in-game high-intensity capacity (from the paired NFL tracking) beyond static combine numbers.*

---

## 7. Data availability (public) and limits

**7a. The 2027 BDB dataset (the primary asset) — [Primary-1]** https://www.kaggle.com/competitions/nfl-big-data-bowl-2027/data
- `players.csv` — 510 rookie prospects, draft years 2023–2025; `nfl_id` key; position, college, draft slot.
- `combine_results.csv` — anthropometrics + **electronic** drill splits (`ten_yd_split`, `forty`, `vertical`, `broad_jump`, `three_cone`, `short_shuttle`, `bench_reps`) + NGS athleticism/college-production/prospect scores. **Nulls where prospects opted out.**
- `combine_tracking.csv` — **10 Hz optical sensor tracking across 6,310 drill attempts**; fields: `event_id` (links frames of one attempt), `nfl_id`, `entity_type` (PLAYER/BALL), `time` (ISO-8601 timestamps), `drill_type` (`FORTY_YARD_DASH`, `THREE_CONE_DRILL`, `SHORT_SHUTTLE`, `SKILL_DRILLS_WR/DB/OL/DL/TE`), `drill_name`, **`attempt`** (attempt index within drill), `x`, `y`, `s` (speed yd/s), `a` (accel), `dis`, `dir`. **This is what makes a within-session fatigue/order study feasible.**
- `player_career_successes.csv` — cumulative snaps (off/def/ST), games active/started, All-Pro/Pro-Bowl counts.
- `player_play.csv` — 316,338 in-game player-play NGS records (2023–2025).
- `game_tracking_2023/2024/2025.csv` — frame-level NGS tracking (2025 file ≈975 MiB, ~4.3M rows, 12 cols).
- **Limits:** only ~510 players and 5 position groups; 3 combine years; several drills opt-out-prone; exact data dictionary should be re-read on Kaggle; **evaluation metric for 2027 is not a numeric leaderboard** (it's a judged writeup), so success is subjective.

**7b. General combine data (long history, for context/pre-2027).**
- **nflverse / nflreadr `load_combine()`** — PFR-sourced combine data **since 2000**; R + Python (`nflreadpy`). https://nflreadr.nflverse.com/reference/load_combine.html ; https://github.com/nflverse/nflreadpy/blob/main/src/nflreadpy/load_combine.py [Primary-1]
- **Pro-Football-Reference** per-year combine pages — https://www.pro-football-reference.com/draft/2026-combine.htm [Primary-1]
- **Kaggle community datasets** — e.g. https://www.kaggle.com/datasets/thomasshaw/nfl-combine-performance-dataset ; https://www.kaggle.com/datasets/savvastj/nfl-combine-data [Secondary]
- **Limits:** these are *summary* metrics only (no sensor tracking); historical attempt-level raw times are not generally public.

**7c. In-game tracking (the "target" side).**
- **NGS / NFL operations** — tracking tech (Zebra RFID, 10 Hz, 500M+ data points/season). https://operations.nfl.com/game-operations-logistics/technology/performance-tracking-data-next-gen-stats ; https://nextgenstats.nfl.com/ [Primary-1]
- **SumerSports SportsTrackingTransformer dataset release** — a widely used community tracking resource. https://github.com/SumerSports/SportsTrackingTransformer/releases/tag/data-v1.0 [Primary-1]
- **Prior BDB datasets** (2019–2026) available on Kaggle/GitHub for method transfer. [Secondary]

**7d. Reference models/how-to (for methodology choices).** Andrew Patton's 20 tips; Yurko's method discipline (base rates, simple baselines, CV with SEs); SumerSports transformer; rubric Football 30 / DS 30 / Writeup 20 / Viz 20. [Secondary, chasko repo]

---

## 8. Annotated source list

*(Author | Year | Title | Venue | URL | relevance. **Bold** = cited multiple times.)*

**Combine predictive validity**
1. Kuzmits & Adams | 2008 | The NFL Combine: Does It Predict Performance in the NFL? | *JSCR* | https://doi.org/10.1519/jsc.0b013e318185f09d | Foundational null result; RB sprint exception.
2. Lyons, Hoffman, Michel & Williams | 2011 | On the predictive efficiency of past performance and physical ability | *Human Performance* | https://doi.org/10.1080/08959285.2011.555218 | College production > combine tests.
3. **Cook, Ryan, Snarr & Rossi** | 2020 | Relationship between the NFL Combine and game performance over 5 years | *JSCR* | https://doi.org/10.1519/jsc.0000000000003676 | ≈2.6% variance explained; weak r's.
4. Pollock et al. | 2021 | Can NFL Combine Results Estimate Defensive Player Longevity? | *Sports Med Int Open* | https://doi.org/10.1055/a-1485-0031 | Drills ↔ roster status/longevity (LB, CB).
5. **Rishis, Johnston & Baker** | 2023 | On the predictive validity of the NFL combine: does it forecast future success? | *J. Sports Sciences* | https://doi.org/10.1080/02640414.2023.2207853 | PRISMA systematic review; "mixed results."
6. **Szekely, Sinnott, Halow & Gregory** | 2023 | NFL Career Success as Predicted by NFL Scouting Combine | arXiv | https://doi.org/10.48550/arxiv.2303.05774 | ML; matriculation yes, success no (R²=0.17).
7. Doyle, Stanelle, Riechman & Mann | 2026 | The NFL Scouting Combine Explains Within-Position… But Is a Poor Predictor of Draft Outcomes | *JSCR* | https://doi.org/10.1519/jsc.0000000000005654 | PCA components; poor draft prediction.
8. Terenzi | 2019 | The NFL Combine: A Scientific-Based Analysis and Critical Review | *RISM* | https://doi.org/10.31031/rism.2019.05.000612 | Battery-validity critique.
9. Agar-Newman et al. | 2024 | Predicting Sprint Performance from Jumps in NFL Combine Athletes | *JSCR* | https://doi.org/10.1519/jsc.0000000000004799 | Jump→sprint imputation (R² .74–.84).
10. Naini (UC Berkeley Sports Analytics) | 2023 | How Important are NFL Combine Performances? | blog | https://sportsanalytics.berkeley.edu/articles/nfl-combine.html | RAS ≈ 9 picks/pt; athleticism as threshold.
11. Frank et al. | 2023 | Discriminant Function Analysis… | *Journal of Expertise* | https://www.journalofexpertise.org/articles/volume6_issue2/JoE_6_2_Frank_etal.pdf | 20-yr, largest combined dataset.
12. The Sport Journal | 2024 | Predictive Ability of Physical Skills… to Predict Draft Status | online | https://thesportjournal.org/article/the-predictive-ability-of-the-physical-skills-used-at-the-nfl-combine-to-predict-draft-status/ | 2022 re-test.
13. State Population Influences Athletic Performance Combine Test Scores | 2019 | *PMC* | https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6355118/ | Non-ability confound example.

**Combine structure / fatigue context**
14. **Wikipedia** | n.d. | NFL Scouting Combine | https://en.wikipedia.org/wiki/NFL_Scouting_Combine | Drills; shuttle = 3 attempts best-of.
15. EssentiallySports | 2026 | Inside the NFL Combine: the grueling six-day schedule | news | https://www.essentiallysports.com/nfl-active-news-flagship-inside-the-nfl-combine-the-grueling-six-day-schedule-prospects-must-survive/ | Multi-day/week structure; weigh-in timing.
16. Topend Sports | n.d. | NFL Draft Combine Testing | reference | https://www.topendsports.com/sport/gridiron/nfl-draft.htm | 4-day workouts by position; bench = strength-endurance; voluntary.
17. The Sports Cast | 2026 | Full Breakdown of Every Test | blog | https://thesportscast.net/2026/02/25/nfl-combine-workouts-drills-full-breakdown-of-every-test-at-the-nfl-scouting-combine/ | Drill taxonomy.
18. SimpliFaster (Haggerty) | 2023 | Tracking Athletes Through the NFL Combine Training Experience | blog | https://simplifaster.com/articles/tracking-training-athletes-nfl-combine/ | Longitudinal monitoring during combine *prep* (loading logic).

**Big Data Bowl**
19. **Kaggle/NFL** | 2026 | NFL Big Data Bowl 2027 (overview/data/rules) | https://www.kaggle.com/competitions/nfl-big-data-bowl-2027 ; /data | Official 2027 theme, timeline, prizes, rubric, dataset schema.
20. **NFL Football Operations** | 2026 | Big Data Bowl hub + 2026 winner | https://operations.nfl.com/programs-initiatives/innovation/big-data-bowl | Official history; Ferraz winner.
21. Front Office Sports | 2026 | NFL Wants to Know Which Combine Measurements Actually Matter | https://frontofficesports.com/article/nfl-data-competition-will-focus-on-what-matters-at-scouting-combine/ | Confirms combine focus; positions; data years.
22. Chasko (chasko-labs) | 2026 | nfl-big-data-bowl-2027 (history/winners/references) | GitHub | https://github.com/chasko-labs/nfl-big-data-bowl-2027 | **[Secondary]** BDB 2019–2026 topics/winners/tv lineage.
23. Nguyen, Yurko & Matthews | 2023 | Here Comes the STRAIN | *The American Statistician* | https://doi.org/10.1080/00031305.2023.2242442 ; https://ecommons.luc.edu/math_facpubs/47/ | BDB-lineage metric; "strain" = materials-science, NOT fatigue.

**Transfer / load**
24. **Sánchez, Weiss, Williams, Ward, Peterson & Wellman** | 2023 | Positional Movement Demands during NFL Games | *Applied Sciences* | https://doi.org/10.3390/app13169278 | In-game load baselines/thresholds by position.
25. Nocera et al. | 2023 | Physiological & Biomechanical Monitoring in American Football: Scoping Review | *Sensors* | https://doi.org/10.3390/s23073538 | Flags internal-load monitoring gap.
26. Short et al. | 2025 | Reverse ACWR to Improve Movement Capacity/Roster Availability in the NFL | *IJSC* | https://doi.org/10.47206/hmrmy739 | NFL load management.
27. NFL NGS | n.d. | Top speed at 10 yards in 40-yd dash | NFL.com | https://www.nfl.com/videos/top-speed-at-10-yards-in-40-yard-dash-next-gen-stats | Illustrative combine↔in-game speed link.

**Adjacent-sport within-session fatigue methods**
28. Girard, Mendez-Villanueva & Bishop | 2011 | Repeated-Sprint Ability – Part I | *Sports Medicine* | https://doi.org/10.2165/11590550-000000000-00000 | RSA foundations.
29. Bishop, Girard & Mendez-Villanueva | 2011 | Repeated-Sprint Ability – Part II | *Sports Medicine* | https://doi.org/10.2165/11590560-000000000-00000 | RSA physiology.
30. **Oliver** | 2007 | Is a fatigue index a worthwhile measure of RSA? | *J Sci Med Sport* | https://doi.org/10.1016/j.jsams.2007.10.010 | Methodological caution on decrement indices.
31. Morin et al. | 2011 | Performance and Fatigue During Repeated Sprints: Appropriate Dose | *JSCR* | https://doi.org/10.1519/jsc.0b013e3181e075a3 | Sprint-dose effects.
32. Thomas et al. | 2018 | Neuromuscular Fatigue & Recovery after Resistance/Jump/Sprint | *MSSE* | https://doi.org/10.1249/mss.0000000000001733 | Fatigue/recovery signatures.
33. Wu et al. | 2019 | Predicting fatigue using CMJ force-time signatures (PCA) | *PLoS ONE* | https://doi.org/10.1371/journal.pone.0219295 | Neuromuscular vs metabolic fatigue discrimination.
34. Staiano et al. | 2023 | Mental fatigue impairs repeated sprint and jump | *J Sci Med Sport* | https://doi.org/10.1016/j.jsams.2023.10.016 | Cognitive confounder.
35. Barte et al. | 2018 | Motivation counteracts fatigue-induced performance decrements | *J. Sports Sciences* | https://doi.org/10.1080/02640414.2018.1548919 | Motivational confounder.
36. Pimenta et al. | 2025 | Should GPS data be normalized for performance/fatigue monitoring in soccer? | *Ger. J. Exerc. Sport Res.* | https://doi.org/10.1007/s12662-025-01048-7 | Normalisation of sprint/effort data.
37. Harper et al. | 2019 | High-Intensity Accel/Decel in Elite Team Sports (meta) | *Sports Medicine* | https://doi.org/10.1007/s40279-019-01170-1 | Defining "effort" events.
38. Gómez et al. | 2018 | Shaq is Not Alone: Free-Throws in Final Moments | *J. Human Kinetics* | https://doi.org/10.1515/hukin-2017-0165 | Within-game fatigue/psych on closed skill.
39. Pliauga et al. | 2015 | Simulated Basketball Game → Sprint/Jump/Temp/Muscle Damage | *J. Human Kinetics* | https://doi.org/10.1515/hukin-2015-0045 | Pre/post performance decay.
40. **Naughton et al.** | 2023 | Defining and quantifying fatigue in the rugby codes | *PLoS ONE* | https://doi.org/10.1371/journal.pone.0282390 | Delphi definition of fatigue; multi-domain.
41. Naughton et al. | 2021 | Quantifying Fatigue in the Rugby Codes | *Front. Physiology* | https://doi.org/10.3389/fphys.2021.711634 | Collision + neuromuscular + biochemical.
42. Fernández-Fernández et al. | 2020 | Within-Session Sequence of the Tennis Serve Training | *IJERPH* | https://doi.org/10.3390/ijerph18010244 | Ordered within-session performance modelling.

**Data**
43. nflverse/nflreadr | n.d. | load_combine (PFR, since 2000) | https://nflreadr.nflverse.com/reference/load_combine.html | Canonical free combine dataset.
44. Pro-Football-Reference | n.d. | Combine results by year | https://www.pro-football-reference.com/draft/2026-combine.htm | Primary public combine numbers.
45. SumerSports | 2024 | SportsTrackingTransformer data release | GitHub | https://github.com/SumerSports/SportsTrackingTransformer/releases/tag/data-v1.0 | Community tracking dataset/method.

---

## 9. Bottom line for Conor

- **The idea is novel, but the novelty is the *fatigue/order* angle — not the combine itself.** I found **no published study of within-combine fatigue, drill-order, time-of-day, or repeated-attempt decrement**, and **no prior BDB entry used combine data at all.** Meanwhile "does the combine predict the NFL?" is saturated (§1). So: **yes pursue, but frame it as a mechanism/measurement paper, not a predictive-validity paper.**
- **Biggest prior art is adjacent, not overlapping.** RSA/fatigue-index work (Oliver 2007; Girard/Bishop 2011) and within-session tennis/soccer modelling (Fernández-Fernández 2020; Pimenta 2025) give you borrowable methods — but none is applied to the combine. STRAIN (Nguyen/Yurko/Matthews 2023) is a trap: "strain" there is *materials-science strain-rate*, **not** fatigue; cite it correctly or avoid the word.
- **Biggest risks are (1) confounding and (2) the clock.** Combine *day* is confounded with *position group*; drill *order* may be perfectly collinear with *drill type*; opt-outs are non-random. Isolating a true fatigue effect is the make-or-break. And because **2027 is THE combine BDB (deadline Jan 6 2027)**, the space will be crowded — the defensible moat is identification strategy + a specific mechanism (e.g. velocity-based fatigue index from sensor kinematics linked to in-game high-intensity deceleration).
- **Standout data opportunity:** `combine_tracking.csv` in the 2027 set has a per-attempt index, per-frame 10 Hz speed/accel, and ISO timestamps — i.e. the *first public substrate* for a within-session fatigue/order study — paired to NGS in-game tracking for the transfer half. The pair is unique.
- **Standout method opportunity:** model the **trajectory of decay across ordered attempts** (not a single decrement index, per Oliver 2007), conditioned on position/drill/body-mass and opt-out selection, with velocity-loss (bench) and deceleration/COD degradation as candidate signals.
- **Recommended next step (cheap de-risk):** before committing, run a *prototype identification check* on the 2027 combine data — (a) confirm whether drill order/time-of-day is recoverable from `time`/`event_id`, (b) measure crude attempt-to-attempt 40-yd decay, and (c) test how much of it survives controlling for drill and position. If order ≈ drill (perfectly collinear), the fatigue signal may be unidentifiable — that is the single biggest threat to the project and should be tested first.

---

### Verification note
Every major claim above carries a source. Where evidence is thin I have flagged it: **§2 (within-combine fatigue) and §6 (gaps) rest on absence of evidence across many queries, not on a positive null study**; **§3.2's year-by-year BDB history is a secondary aggregator** (spot-verify before publication); and **the precise within-day drill order and current attempt counts could not be authoritatively confirmed** from fetched sources. `web_search` was unavailable, so Google Scholar/X were not directly searched — a manual Scholar pass on "combine + fatigue/order/sequence" is recommended as a final sanity check before committing.
