# eSchool integration notes

Everything here was observed on the public site at `https://eschool.ug` on 30 October 2026, by
sending requests and comparing results. The internals of the site were not available, so causes
are inferred from behaviour and marked as such. `tests/test_live.py` re-checks the basic contract.

## How the integration works

`/find-schools/search` is a server-rendered page driven by a GET form, so a search is an ordinary
URL. The app builds that URL, parses the returned HTML, and reads each candidate's public profile
at `/find-schools/?slug=<slug>`.

The site's JavaScript also calls `admin/main/ajax.php`. This app does not use it. The brief excludes
internal APIs, and an admin path is not a public contract.

`robots.txt` allows general access and declares `search=yes, ai-train=no, use=reference`, while
blocking named AI crawlers. The app identifies itself honestly, fetches only on a parent's request,
limits concurrency, caches for an hour, and never sends school content to the model.

## Search parameters

| Parameter | Values | Notes |
| --- | --- | --- |
| `location` | 67 uppercase district names, e.g. `KAMPALA` | Case-insensitive. Unknown values return 0. |
| `county` | Depends on `location`, e.g. `KAMPALA CENTRAL DIVISION` | Works alone. Options appear on the page once `location` is set. |
| `min_fees`, `max_fees` | Plain digits | See the fee findings below. |
| `gender` | `girls`, `boys`, `mixed` | A clean partition: 20 + 6 + 804 = 830 for `location=KAMPALA`. |
| `accomadation` | `Boarding`, `Day`, `Day & Boarding` | Spelled this way on the site. A clean partition: 44 + 213 + 573 = 830. |
| `levels[]` | `Nursery & Kindergaten`, `Nursery & Primary`, `Primary`, `Secondary`, `International` | Repeatable, combined with OR. Spelling is the site's. |
| `admission_status` | `open`, `closed` | Unknown values are silently ignored. |
| `curriculums[]` | `UNEB`, `Cambridge`, `IB`, `Montessori`, `Islamic`, `Other` | Repeatable, OR. |
| `facilities[]` | 8 values, e.g. `Library`, `School Transport` | Repeatable, OR. |
| `religions[]` | 7 values, e.g. `Catholic Founded` | Repeatable. |
| `q` | Free text | Searches school names (`q=ntinda` returns 2 schools with Ntinda in the name). |
| `sorting` | `Popular`, `FeesLow`, `Newest` | |
| `page` | Integer | 24 schools per page. |

## Findings that shaped the design

**1. `accomadation` needs two requests for one idea.** "Boarding" and "Day & Boarding" are separate
categories, and the field takes one value. A parent wanting boarding can use either, so the app sends
one request for each and merges them. The array form `accomadation[]=...` returns 0.

**2. `mixed` is labelled misleadingly.** The form calls it "Day & Boarding / Mixed", but it behaves as
co-educational only. Day versus boarding is unrelated to it.

**3. The site's own fee form is broken for the combined range.** The form formats values with commas.
For Kampala, `min_fees=400,000&max_fees=1,000,000` returns 0 schools, while the same range in plain
digits returns 13. `min_fees=1,000,000&max_fees=10,000,000` returns 2 schools whose values look
unrelated. The likely cause is the server reading `1,000,000` as `1`. The app always sends digits.
Parents using the slider are affected, so this should be fixed on eSchool's side.

**4. The fee filter is not a reliable statement about tuition.** About 170 of 1,109 schools appear in a
wide fee search (`min_fees=1&max_fees=100000000`), so any fee filter hides most of the directory.
It also admits schools with no published tuition: Mbogo High School passes `max_fees=1000000`, but its
profile says "Contact School for Fees" and lists only a UGX 50,000 admission fee. What the filter
actually compares is unknown. The app therefore treats it as a retrieval hint, reads tuition from the
profile, and reports "unconfirmed" where none is published.
It also searches again without the fee filter when few candidates remain, so unpublished-fee schools
are not silently lost.

**5. `location=KAMPALA` is not limited to Kampala.** It returns 830 schools, with cards listing Wakiso,
Mukono and Buikwe. The eight Kampala divisions total 285 schools through `county`. Other districts
appear cleaner, but `location=GULU` returned one school listed in Iganga. The app checks the district
shown on each school and ranks or removes those that differ.

**6. `admission_status=open` currently matches nothing.** It returns 0 nationally, and `closed` returns
all 1,109. Either nothing is open at this point in the year or the data is not maintained, and this
app cannot tell which. It reports what the site says. So "currently admitting" cannot be met today.

**7. Profiles contradict themselves on admissions.** The profiles sampled show a green "Open for 2026"
badge beside an "Admissions Closed" panel. The app uses the status on the search card and panel, which
agree with the filter, and ignores the badge. Parents should confirm with the school.

**8. Multi-value filters are OR.** `facilities[]=Library&facilities[]=School Transport` returns more
schools than either alone. A parent wanting both needs each one checked, so the app checks each facility
against the profile individually.

**9. Search cards are sparse.** They show name, level, rating, district and admission status only.
Gender, section, county, curriculum, religion and fees exist only on profiles. Profile requests are
therefore capped at 8 per search.

**10. The site can be slow.** Pages took up to 15 seconds in testing, and one exceeded a 25-second read
timeout. The app uses a 45-second read timeout, two retries with backoff, at most three concurrent
requests, and a disk cache.

## Limitations

- Admission by class is not published, so "admitting Senior One" is always shown as unconfirmed.
- Tuition is a single estimated range. It does not separate day from boarding or one class from another.
- Results come from at most 3 pages per request. Larger result sets are disclosed in the response. A broad
  `location=KAMPALA` search can leave many true Kampala schools unconsidered, because the filter also
  returns neighbouring districts.
- The parser depends on the current HTML. A layout change raises an error and is reported as a failed
  search, not as "no schools", but the parser would then need updating.
- Neighbourhoods such as Ntinda are not filters. Gemini can suggest the containing division, which the app
  accepts only if the division exists on the site, and it tells the parent about the assumption.
