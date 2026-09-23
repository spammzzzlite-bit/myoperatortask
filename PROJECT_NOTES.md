# Project notes — TalentFlow Q3 roadmap exercise

Source of truth: `CANDIDATE_PACK_PM.md.docx` (candidate exercise pack), transcribed below.
This file records **what the exercise asks for**. It contains no analysis or roadmap decisions yet.

---

## 0. Setting

- **Role:** Product manager for **TalentFlow**, a recruiting-operations product.
- **Customer:** **Acme** (Acme Corp), our largest design partner. They gave us read access to their production data: job openings, candidates, applications, interviews and offers.
- **Capacity:** **6 engineer-weeks** next quarter. The job is to decide what goes into them.
- **Time box:** **2 hours**, in one sitting. "Two hours is a cap, not a target." They would rather have **three deliverables we stand behind than five** produced over an afternoon, and **they will ask how long it took**.
- **Tooling:** Claude Code + the Airtable REST API. We are not expected to write code ourselves, but we are expected to direct the tool well enough to "stake a decision on the numbers it gives you". Tooling choice is ours: raw REST, a client library, an MCP server, anything.

## 1. Exact objective

> "We are assessing how you turn a customer's real data into a defensible roadmap, using Claude Code and the Airtable REST API."

In practice: check the VP's two claims against Acme's data, then decide how to spend 6 engineer-weeks, with a number behind each decision. From section 3 of the pack: "Your CEO has said yes in principle. You have the data. **Decide what is actually true.**"

## 2. The two claims to investigate

From Acme's VP People (emailed our CEO on Friday):

> "Two things for next quarter.
> First — job boards are our biggest channel by a wide margin and they bring in 26.9% of our hires. I want the job-board integration work brought forward.
> Second — our offer acceptance rate is sitting at around 72% and the board is asking about it. I need that number moving."

| # | Claim | Stated figure | What the VP is asking for |
|---|-------|---------------|---------------------------|
| C1 | Job boards are the **biggest channel by a wide margin** and bring in **26.9% of hires** | 26.9% | Bring the **job-board integration** work forward |
| C2 | **Offer acceptance rate** is **around 72%** and the board is asking about it | ~72% | "Get that number moving" |

Note: C1 is really two claims, (a) job boards are the biggest channel *by a wide margin* and (b) they account for 26.9% of hires. Each needs checking separately.

## 3. Deliverables (D1–D5)

Weights show where the marks are. Timings are suggestions only.

| ID | Deliverable | Suggested time | Marks |
|----|-------------|----------------|-------|
| **D1** | Verdict on the VP's two claims | 15 min | **16** |
| **D2** | The 6 engineer-weeks | 20 min | **20** |
| **D3** | Metrics spec for offer acceptance rate | 15 min | **15** |
| **D4** | What in this data would you not trust? | 35 min | **30** (highest) |
| **D5** | One-page memo | 15 min | **19** |
| | **Total** | **100 min** | **100** |

**D1: Verdict on the two claims**
- For each claim: *build it*, *don't build it*, or *build something different*.
- **Every verdict carries the one number that decides it.**
- "Disagreeing with the VP is fine. Agreeing is fine. Doing either without a number is not."

**D2: The 6 engineer-weeks**
- **Three things we will build, in order**, each with a **rough size** and the **number that justifies it**.
- **Three things we will not build**, each with **why not**.
- "The not-doing list is scored as heavily as the doing list."

**D3: Metrics spec for offer acceptance rate**
- Precise enough that **two engineers would implement it identically**.
- Must cover: **numerator, denominator, edge cases, time window**, and **what the dashboard shows when the answer is ambiguous**.

**D4: What in this data would you not trust?**
- Look at the data itself, not only the questions asked.
- Cover **what is wrong**, **how much of it is wrong**, and **how that changes the answers above**.
- "Be systematic. We are more interested in how you went looking than in any single problem you happen to find."
- Highest-weighted deliverable. "If you are short on time, come here rather than polishing D2."

**D5: One-page memo**
- What we found, what we are doing about it, what we would look at next. **One page.**

## 4. Airtable access

| Item | Value |
|------|-------|
| Base name | **TalentFlow - Acme Corp** |
| Base ID | **`appYePRAI75PMbQNQ`** |
| API key | Stored in `.env` as `AIRTABLE_TOKEN`. **Not reproduced here.** |
| Access level | **Read-only.** Writes return **403**. The pack says this is expected, not a bug. |
| API docs | https://airtable.com/developers/web/api/introduction |

**Tables (8):** `Departments`, `People`, `Job Openings`, `Candidates`, `Applications`, `Interviews`, `Offers`, `Findings`.

- `Findings` is **read-only for us**, so the findings table has to be delivered as a file, not written to the base.

**Reference connectivity check from the pack:**
```bash
curl -H "Authorization: Bearer $AIRTABLE_TOKEN" \
  "https://api.airtable.com/v0/appYePRAI75PMbQNQ/Applications?maxRecords=3"
```

**API requirements:**
- Auth header: `Authorization: Bearer $AIRTABLE_TOKEN`.
- Endpoint pattern: `https://api.airtable.com/v0/{baseId}/{tableName}` (URL-encode table names with spaces, e.g. `Job%20Openings`).
- The pack says: "Put the key in an environment variable or a .env file. Do not paste it into your code."

## 5. API constraints

The pack calls these "real constraints, not hints".

1. **Rate limit: 5 requests per second, per base.** Going over returns **HTTP 429** and **locks us out for 30 seconds**.
2. **Pagination:** list endpoints return **at most 100 records per request**. Larger tables are paginated (Airtable returns an `offset` token to pass on the next request until none comes back).
3. **Expected approach:** "**Pull the data down once, cache it to disk, and do your analysis locally.**" The pack calls this the production approach, and it avoids querying the API over and over.

Implications for our tooling:
- Throttle to under 5 req/s (e.g. ≥ 0.2–0.25 s between calls) and back off on 429.
- Follow `offset` until it is exhausted, for every table.
- Cache the raw JSON to disk (`data/raw/`, git-ignored) and make every analysis step read from the cache.

## 6. Numerical figures stated in the exercise

| Figure | Context |
|--------|---------|
| **2 hours** | Time cap, one sitting |
| **3 vs 5** | "Three deliverables you stand behind" beats "five produced over an afternoon" |
| **6 engineer-weeks** | Capacity next quarter |
| **26.9%** | VP's claimed share of hires from job boards |
| **~72%** | VP's claimed offer acceptance rate |
| **5 req/s per base** | Airtable rate limit |
| **429 / 30 s** | Status code returned when the rate limit is exceeded, and how long the lockout lasts |
| **100 records** | Max records per list request (pagination) |
| **403** | Status code returned on any write (read-only access) |
| **3** | `maxRecords=3` in the sample curl |
| **8** | Tables in the base |
| **3 build / 3 not-build** | D2 structure |
| **1 page** | D5 memo length |
| **D1 15 min · 16 marks** | |
| **D2 20 min · 20 marks** | |
| **D3 15 min · 15 marks** | |
| **D4 35 min · 30 marks** | Highest weight |
| **D5 15 min · 19 marks** | |
| **100 marks / 100 min** | Totals of the above |
| **"hour"** | Section 2 says caching "will make your hour go much further". This does not match the 2-hour cap elsewhere; noted only as a wording detail |

## 7. What to send back

1. **The memo, the roadmap and the metrics spec**: D5, D2 (with D1 verdicts) and D3. **One document is fine.**
2. **A findings table** with columns **claim, metric, value, method, confidence**, as **Markdown or CSV**. It must be a file, because the base's `Findings` table is read-only.
3. **Our code or queries**, in a repo or a zip. "**We will re-run them and expect your numbers back.**" Everything has to be reproducible from the code plus a fresh pull.
4. **The Claude Code session transcript.**

Also: they will **ask how long it took**.

## 8. How we will be assessed

| Criterion | What it means |
|-----------|---------------|
| **Decision quality** | Our verdicts follow from the data, "not from the loudest voice" |
| **Sizing** | We know how big each problem is, and how certain, **before** ranking it |
| **Metric discipline** | Our definitions survive contact with an edge case |
| **Judgement about the data** | We checked whether the data could carry the decision |
| **Communication** | The memo says **what to do**, not only what is true |
| **Use of Claude Code** | How we directed the tool, judged from the transcript |

Explicit guidance:
- "A number you cannot fully stand behind is worth saying so."
- "Stating uncertainty plainly counts in your favour", including saying that **a difference is too small to act on**.
- Saying **what we would have checked with more time** also counts in our favour.

## 9. Security and credential handling

What the pack requires:
- Put the key in an **environment variable or a `.env` file**. "**Do not paste it into your code.**"
- Access is read-only, and a 403 on write is expected.

What this project does:
- The key lives either in `.env` for local runs (`AIRTABLE_TOKEN`, file mode `600`), or, in this cloud environment, as a proxy-injected API credential that the session never sees (see §10). `.env` is listed in `.gitignore`. Only `.env.example` (placeholder values) is committed.
- The key is **never printed** into notes, code, README, transcript summaries or any generated output. Scripts read it from the environment and log status codes, counts and record IDs only.
- The raw data cache (`data/raw/`) is git-ignored too. It is a customer's production data, and it should be shared only as part of the submission if needed.
- We never write to the base. The only write-shaped request we plan is a no-op probe (POST with an empty record list) to confirm the token is read-only. It cannot create anything.

## 10. Status

- [x] Exercise pack read in full
- [x] Credential: supplied as this cloud environment's **API credential** for `api.airtable.com`, injected by the egress proxy. `AIRTABLE_TOKEN` is unset and no `.env` exists; the code sends no auth header of its own when the variable is unset. `.env` stays git-ignored for local runs.
- [x] Connectivity check (`python3 scripts/check_connection.py`, 2026-09-23): read `GET Applications?maxRecords=3` → **HTTP 200**, 3 records; no-op write probe (POST empty record list) → **HTTP 403** `INVALID_PERMISSIONS_OR_MODEL_NOT_FOUND`. Access is live and read-only, as the pack says.
- [x] Ingestion pipeline (`pipeline/`): schema discovery, paginated pull at ≤4 req/s, 429 lockout handling, expired-offset restart, raw JSON cache and manifest. Tested against a local mock API (`tests/test_pipeline.py`).
- [x] **Real pull done** (`python3 -m pipeline.ingest`, fetched 2026-09-23T07:57:43Z): 15 requests in 4.1 s, no 429s.

  | Table | Records | Pages |
  |---|---:|---:|
  | Departments | 8 | 1 |
  | People | 14 | 1 |
  | Job Openings | 24 | 1 |
  | Candidates | 300 | 3 |
  | Applications | 350 | 4 |
  | Interviews | 160 | 2 |
  | Offers | 36 | 1 |
  | Findings | 0 | 1 |

  Counts and sha256 per table are in `data/raw/_manifest.json`.
- [x] **Schema metadata API returned 403** (token lacks `schema.bases:read`). The schema is inferred from records, which means: fields that are empty on every record are invisible (Airtable omits empty fields); field types (single select vs text, formula vs lookup) and primary fields are unknown; declared-but-unused select options cannot be checked.
- [x] Profile (`python3 -m pipeline.profile` → `data/profile/profile.md`) and raw-row sample (`python3 -m pipeline.show_raw`) reviewed.
- [ ] D4 data-quality audit (`pipeline/audit.py` → `outputs/data_quality.md`, `.csv`)
- [ ] D1–D3, D5 (not started, per instructions)

## 11. Data needs per deliverable (mapped to the real schema)

Mapped against the pulled data (schema inferred from records; see §10). Supersedes the provisional guesses written before the pull. This section says **where** each input lives, not what it shows.

### Structural facts that shape every deliverable

- **Source is per candidate, not per application.** `Candidates.Source` (Job Board, Agency, LinkedIn, Career Site, Referral, Campus). 50 candidates have 2 applications and both inherit one source.
- **Two competing referral signals besides `Source`:** `Applications.Referred By` → People (27 applications) and free text in `Candidates.Notes` (e.g. "Referred internally", "Sourced from a conference list").
- **"Hire" has two candidate definitions:** `Applications.Stage = "Hired"` or `Offers.Status = "Accepted"`. No start/joined date exists, so an accepted-then-reneged offer is undetectable.
- **People is Acme's staff** (Role: Recruiter / Interviewer / Hiring Manager), not hires. There is no People ↔ Candidates link; the earlier guess was wrong.
- **Offer date is stored twice:** `Offers.Offered On` and `Applications.Offered On` (profile: 92% value match).
- **`createdTime` is a bulk-load timestamp** (all records 2026-08-27), so only business date fields carry event time.
- `Findings` has 0 records and no visible fields.

### Field mapping

| Need | Used by | Real field(s) | Joins |
|---|---|---|---|
| Channel | D1-C1 | `Candidates.Source`; also `Applications.Referred By`, `Candidates.Notes` | Applications.`Candidate` → Candidates |
| Channel volume ("biggest channel") | D1-C1 | count of Candidates or Applications by `Source` (volume vs hires is a definitional choice) | as above |
| Hire | D1-C1, D3 | `Applications.Stage = "Hired"`; `Offers.Status = "Accepted"`; `Job Openings.Status = "Filled"` + `Headcount` | Offers.`Application` → Applications.`Candidate` → Candidates |
| Offer outcome | D1-C2, D3 | `Offers.Status` (Accepted / Declined / Pending; no Rescinded/Expired), `Offers.Decline Reason` (Counter Offer, Location, Compensation) | Offers → Applications |
| Offer time window | D3 | `Offers.Offered On`, `Offers.Decision On`; duplicate `Applications.Offered On` | — |
| Offer terms | D3, D4 | `Offers.Base CTC`, `Joining Bonus`, `Proposed Start Date`; `Job Openings.Salary Band Min/Max` | Offers → Applications.`Opening` → Job Openings |
| Offers per application / candidate | D3 | `Applications.Offers` (max 1 per application); a candidate can have 2 via 2 applications | Offers → Applications → Candidates |
| Pipeline state (cross-check) | D1, D3, D4 | `Applications.Stage` (Applied, Screening, Interview, Offer, Hired, Rejected, Withdrawn), `Status` (Active/Closed), `Closed On`, `Rejection Reason` | — |
| Stage dates | D2, D4 | `Applications.Applied On`, `Screened On`, `First Interview On`, `Final Interview On`, `Offered On`, `Closed On` | — |
| Segments | D2 | Job Openings `Department`, `Level`, `Location`, `Employment Type`, `Status`, `Headcount` | Applications.`Opening` → Job Openings.`Department` → Departments |
| Interview activity | D2, D4 | Interviews `Round`, `Outcome`, `Recommendation`, `Score`, `Scheduled On`, `Completed On`, `Interviewer` | Interviews.`Application` ↔ Applications.`Interviews` |
| Record IDs | D4 | `Application ID`, `Candidate ID`, `Offer ID`, `Interview ID`, `Req ID`, `Departments.Code` | — |
| Candidate identity (dedupe) | D1, D4 | `Candidates.Full Name`, `Phone`, `Email` (profile: 294 / 294 / 300 distinct of 300) | — |
| Link integrity | D4 | every link field, both directions | all |

### D4 checks this implies

Uniqueness of every ID field and of candidate identity; link integrity both ways; cross-field consistency (Stage vs Status vs Closed On vs Rejection Reason; Hired vs Accepted; Offer Status vs Decision On; the two Offered On copies; interview dates vs Interviews; Filled/Headcount vs hires; Base CTC vs band; Source vs Referred By vs Notes); date order; category hygiene; missing values in D1/D3 fields. Implemented in `pipeline/audit.py`.
