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
- The key lives only in `.env` (`AIRTABLE_TOKEN`, file mode `600`). `.env` is listed in `.gitignore`. Only `.env.example` (placeholder values) is committed.
- The key is **never printed** into notes, code, README, transcript summaries or any generated output. Scripts read it from the environment and log status codes, counts and record IDs only.
- The raw data cache (`data/raw/`) is git-ignored too. It is a customer's production data, and it should be shared only as part of the submission if needed.
- We never write to the base. The only write-shaped request we plan is a no-op probe (POST with an empty record list) to confirm the token is read-only. It cannot create anything.

## 10. Status

- [x] Exercise pack read in full
- [x] `.env` created (key loaded, not echoed), `.env` git-ignored, `.env.example` committed
- [x] Connectivity check script written: `scripts/check_connection.py`
- [ ] **Connectivity check blocked:** this cloud environment's network policy denies outbound access to `api.airtable.com` (proxy returned 403 on CONNECT). The failure is in the sandbox's egress policy, not in Airtable auth. We need `api.airtable.com` added to the environment's allowed domains (or a broader network access level), then re-run:
  ```bash
  python3 scripts/check_connection.py            # default table: Applications, 3 records
  ```
- [ ] Data pull + cache (not started. Waiting on connectivity and go-ahead)
- [ ] D1–D5 (not started, per instructions)
