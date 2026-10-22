# 0013: People's records in the app, and checks the record can't support (Phase 5c.1)

- Status: proposed (awaiting the user's review)
- Date: 2026-10-22

## Context

ADR 0010 planned Phase 5c: uploads, confirmations and deletion in the local app. Until now the app served only Synthea cohorts, read-only (ADR 0011). Phase 5c is split in two, each with its own review:
- **5c.1 (this ADR):** adding people's records, deleting them, and the app's access control.
- **5c.2:** lab reports, background model jobs, and confirmations of dates and analyte names.

**The user decided** (2026-10-22):
- **Access:** a secret token for everything, since every account on this Mac can reach 127.0.0.1.
- **Confirmed analyte names** (5c.2) stay in this installation; the committed name table does not change.
- **No label fetching:** medicines outside the committed label lock get no label, and nothing is sent to NLM.
- **No model explanations** for people's records in 5c. Model text comes later, if at all.

Planning 5c.1 exposed a safety problem that uploads would turn into wrong flags, described first.

## Decision

### Checks the record can't support (`rules/unread.py`, ruleset `rules-v3`)

**The problem:**
- **Codes from dev-1000 only:** the rules recognise diagnoses, procedures and medicines only by the codes listed in `rules/diagnoses.py` and `rules/medications.py`, built from dev-1000's own codes. Lab results are recognised by the LOINC codes of the analyte registry.
- **Missing things:** a check that flags something missing ("no kidney diagnosis is recorded", "no GFR test in 12 months") would contradict a record that codes that diagnosis or test differently. An Italian record coding chronic kidney disease in ICD-9-CM would be flagged as having no diagnosis.
- **Present things:** a check that flags something present (metformin and a low eGFR) would miss it, or take an older value for the latest.

**The guard:** before a result is kept, the rules look for records they may be failing to read. A record dated up to the evaluation date, or undated, is unread when its code is not in the lists and:
- **kidney:** a condition whose name mentions kidney disease, albuminuria or dialysis, or a procedure whose name mentions dialysis or a kidney transplant;
- **anaemia:** a condition whose name mentions anaemia;
- **medicines:** a medication request whose name mentions metformin, epoetin, or one of the ACE inhibitors, ARBs and NSAIDs of the RAS-inhibitor note;
- **lab:** a laboratory result coded outside LOINC, or any result coded outside LOINC whose name the report name table maps to an analyte in scope;
- **no name:** a record of these kinds with no name at all, only an unlisted code.

**How the name patterns work:**
- **Languages:** the patterns cover English and Italian forms ("renale", "nefropatia", "dialisi", "anemia", "metformina", "naprossene"), after folding case and accents.
- **Coverage:** a test checks that they match the display of every listed code. A code added to a list therefore needs a name the guard recognises.

**Its effect:**
- **Which checks:** each flag and note rule names the topics it reads (`DEPENDS`); every rule reads lab results. A rule whose topics have an unread record is not done.
- **What happens:** the rule raises no flag or note, and the result lists it (`PatientRules.unchecked`) with the records that held it back (`PatientRules.unread`, each with why, in English and Italian).
- **Only ever subtracts:** names are matched only to hold a check back, never to raise one. A test checks, on several records, that the guarded flags are a subset of the unguarded ones and that the assessments are unchanged.
- **On the pages:**
  - the personal view shows a card, "Some checks could not be done", with the checks and the entries;
  - the clinician view shows a panel with the rules, the entries, their codes and why;
  - the print sheet has the same section.
- **The all-clear text** says "the checks that could be done found nothing" when some checks were not done.

**Two choices that came from dev-1000:**
- **Code systems:** holding a check back for any condition in another code system was the first idea. It fails: Synthea codes its dental and oral-cancer conditions in ICD-10, and real records often mix systems. The guard goes by names instead.
- **Dental codes:** Synthea's dental procedures are coded in CDT (Current Dental Terminology, the ADA's dental code system), with no display name. The first `make flags` run counted them as nameless and held back the CKD checks for 151 patients. CDT holds nothing a check looks for, so it is listed as out of scope (`OUT_OF_SCOPE`).

**On dev-1000** (`make flags`, rules-v3):
- **No flag changed:** 122 patients have a flag, with the same count for every rule as under rules-v2.
- **Nine patients' CKD checks are not done:** each has a kidney transplant recorded only as a procedure ("Transplant of kidney", "Transplantation of kidney regime"), under codes the lists don't have. None of the nine had a flag before.
- **Explanations unaffected:** cached explanations match flags by content, not ruleset, so they all still apply.

### People's records (`api/people.py`)

**Two ways to add a person:**
- **Upload** a FHIR R4 bundle with one patient, JSON only, up to 100 MB (the largest dev-1000 bundle is 68 MB).
- **Enter** a label, a birth date and an administrative gender, for someone who has lab reports but no FHIR record (the eGFR equation needs age and sex). The form writes a minimal FHIR bundle whose `Patient.meta.source` is `medgraph:entered-in-app`.

**Checks on adding:** either way the record goes through the steps of `make graphs`: parse, build, every invariant and coverage check, store. A record that fails a graph check is not added: that would be a fault in medgraph, and the log has the count. Problems the parser reports are listed on the result page, by kind.

**Storage:**
- **One folder per person:** `$MEDGRAPH_DATA_DIR/people/<id>/` holds `person.json` (label, how and when added, the record's SHA-256 and its own patient ID), `record.json` (byte for byte as uploaded, or as the form wrote it) and `graph.sqlite`.
- **Random IDs:** `<id>` is random (12 hex digits). It names the folder, the page and log lines. The record's own patient ID, which could identify the person (a tax code, for example), stays inside the folder.
- **Labels:** the label the user gives is the only name the app shows.

**Replacing:**
- **Same file:** uploading the identical file again changes nothing.
- **Same patient, new file:** the app asks whether to replace that person's record, keeping the label and the ID, or to keep it. The upload waits in `people/.pending/` for at most an hour.

**Deleting:**
- **The whole folder:** deleting a person removes their folder, and with it everything derived from their record.
- **The confirmation page** shows the folder and says that the drive is not wiped: a removed file can stay recoverable with disk tools.

**Interrupted writes:**
- **How a write is done:** adding and replacing write a new folder and then rename it into place.
- **On start,** the app undoes or finishes what a stopped server left half done: a half-added record is dropped, and a half-replaced record is restored.

**Checks run as of today** on this machine's clock, and the page says so.

**What people's pages leave out:**
- **No knowledge graph:** it is built per cohort from that cohort's label lock, and a lock for uploads would list a person's medicines.
- **No explanations:** these follow the user's decision. The rules' own sources and quotes are still shown.

### Access (`api/access.py`)

**The key:**
- **Where it lives:** a secret is created on the first start at `~/.config/medgraph/app-token`, mode 0600 (`MEDGRAPH_APP_TOKEN_PATH`), in the home folder because the exFAT data volume has no file permissions. A key file that other accounts can read is made private again.
- **The link:** `make app` prints a link that carries the key. The app answers that link with an HttpOnly, SameSite=Strict cookie and a redirect to the same page without the key.
- **Without the cookie,** every request gets a short page saying to open that link.

**Forms are accepted only:**
- with the cookie;
- from the app's own origin (the `Origin` header must be `http://` plus the request's own `Host`; a missing origin or `null` is refused);
- with a form token derived from the key.

**Headers:**
- **CSP:** the response header lets forms post to the app only (`form-action 'self'`). Patient pages keep `form-action 'none'` in their own CSP, and both apply.
- **Referrer policy:** `same-origin` instead of `no-referrer`. With no referrer, browsers send `Origin: null` on a form post, and the origin check could not work. Pages link nowhere outside the app.

### Forms (`api/forms.py`)

- **Read into memory:** a body is read into memory up to its limit, and parsed there with `python-multipart`, which is a new dependency.
- **Why:** frameworks spool large uploads to the system's temporary folder, and uploads may be real records, which must stay in the data folder.
- **Tested:** a test checks that a 3 MB upload leaves the temporary folder empty.

### The app's own pages in English and Italian

- **Which pages:**
  - the home page, with people and cohorts;
  - adding, replacing and deleting a person;
  - the error pages and the locked page;
  - a cohort's patient list;
  - the concept map's waiting page.
- **The language** is in the address (`?lang=it`), carried on every link, and passed to the patient page's own switch.
- **The texts** are in `strings.json` (305 keys per language, including the add errors). The Italian is mine.
- This ends ADR 0012's limitation "English only: the cohort and patient lists". The concept map stays in English.

## Checks

- **`make check`:** 964 passed, 46 skipped (opt-in).
- **Tests:**
  - **The guard** (`tests/rules/test_unread.py`, 32 tests):
    - records coded in ICD-9-CM, ICD-10, unlisted SNOMED, local lab codes, ATC and unlisted RxNorm;
    - dental and unrelated conditions are read ("adrenal" is not "renal");
    - the guard only ever subtracts;
    - every rule lists its topics;
    - the patterns cover every listed display.
  - **The texts:** the new plain texts and reasons pass the wording and language checks.
  - **The app** (`tests/api/test_people.py`, 36 tests):
    - access, the key file's mode, and forms from other origins or without the token;
    - uploads, byte-for-byte storage and refusals;
    - the temporary folder staying empty, replacing, entering by hand, escaping and Italian errors;
    - deletion leaving nothing in the data folder;
    - recovery from interrupted writes;
    - pending uploads expiring;
    - every app string key existing.
- **End to end,** on a second app instance with a temporary data folder:
  - **Access:** the app answered 401 without the key, and the link set the cookie.
  - **Adding:** three dev-1000 bundles were uploaded through HTTP (1.0 to 7.3 MB, at most 0.2 s each), and one person was entered by hand.
  - **Replacing and deleting:** one record was replaced and one person deleted. Afterwards no file in the data folder named that person or held their record.
  - **The log** holds only the random IDs.
- **Page check in Node:**
  - **People's pages:** the four pages passed, including the hand-entered person with no data and the patient with held-back checks.
  - **Earlier pages:** the eight Phase 5b pages and two pages with held-back checks also pass.
  - **The 4b explanation check** fails on one page, as it did before this phase: that page shows explanations cached with prompt `explain-v1`, which predates the closing sentence added by code.

## Limitations

- **What the guard doesn't cover:**
  - pregnancy under an unlisted code, which lowers the anaemia cutoff;
  - smoking status coded outside LOINC;
  - the brand names of the checked medicines;
  - a record's second coding, because the graph keeps only the first (`graph/build.py`).
- **Report rows** whose names the table doesn't map are left to 5c.2's confirmations.
- **Kidney transplant procedures** hold back the CKD checks instead of making them "not assessable". Listing them as a kidney-failure state is a code-list change, with a test, for the user to decide.
- **Upload memory:** an upload is held in memory twice while it is read: the body and the file. A 100 MB upload takes a few hundred MB while its JSON is parsed, as `make graphs` does for a large bundle.
- **Other accounts on this Mac** can't use the app, but can still read the data drive's files directly: exFAT has no permissions.
- **Deleting doesn't wipe the drive.**
- **The texts** of the guard card, the app pages and the Italian are mine, and need the user's review.
- **Not checked:** the look, which headless browsers can't render in the assistant's session, and screen readers.

## Consequences

- **5c.2** adds lab reports to a person's folder, with background transcription by the local model and the confirmations. A confirmed name or date is stored in that folder, so deleting the person deletes it too.
- **A new flag or note rule** needs its entry in `DEPENDS` and its check name in `plain.CHECK_NAMES` (tests fail until it has them).
- **A code added to a list** needs a display the name patterns match (a test).
- **Phase 6 (MIMIC-IV)** brings ICD-9/ICD-10 data. The guard will hold back many checks there until the code lists cover those systems, and the MIMIC reports will count how many.
