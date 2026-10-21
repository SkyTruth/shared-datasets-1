# October 7–21 alert counterfactual

The reviewed inventory contains 19 GitHub bot parents representing 18 distinct
episodes: catalog run `39278377548/1` acquired both workflow and deployment
parents. The compact checked-in fixture preserves original parent IDs, exact
source attempts, revisions, verified terminal times, classifications and required
actions. It contains no raw Slack conversation or credentials.

The replay has two explicit scenarios:

- **Unattended:** all 18 episodes still require concrete action when unresolved
  beyond the safe recovery budget. Historical green times do not establish when
  GitHub, Google or npm first became available. The replay therefore does not
  invent successful automated retries or claim those outages would disappear.
- **Accountable owner:** assuming exact ownership registered before failure and
  an explicit check-in every nine minutes, 8 episodes reach their recorded
  terminal recovery before the original two-hour deadline and create no public
  parent. The other 10 require one actionable parent at that deadline. This is
  a counterfactual ownership assumption, not evidence those agents existed then.

| Source attempt | Reviewed cause and action | Owner scenario |
|---|---|---|
| `37647645882/1` | Usage bucket prerequisite outside the constrained IAM plan; provision it through its reviewed protected path. | One request for action |
| `37653170615/1` | Invalid multiline selector output; repair actual CLI serialization. Post-apply attestation uncertainty remains a separate inspection boundary. | One request for action |
| `37680552840/1` | Google IAM read `503` before CDN apply; recover safe reads without replaying possible writes. | One request for action |
| `37721458560/1` | Repeated selector serialization defect; emit valid single-line JSON. | Owner-held recovery |
| `37895980054/1` | Accepted npm version not yet visible to the verifier; check exact package bytes and repair only its receipt. | One request for action |
| `38033531700/1` | Missing retirement permissions; bootstrap the reviewed prerequisite before the constrained plan. | One request for action |
| `38262001808/1` | Hygiene contract rejects valid publication/translation receipt paths; repair the validator. | One request for action |
| `38496427534/1` | Plain GitHub `HTTP 500`, then gate-attempt evidence defects; recover reads and preserve exact gate provenance. | One request for action |
| `38495751381/2` | Plain GitHub `HTTP 500`; recognize the transport representation. | Owner-held recovery |
| `38495751381/3` | Wrong ci-ready producing attempt; resolve exact gate execution and artifact window. | Owner-held recovery |
| `38921157641/1` | Repeated accepted npm visibility delay; poll within the shared budget, never republish accepted bytes. | One request for action |
| `39003936050/1` | CORS prerequisite ordered after constrained usage plan; finish it first. | Owner-held recovery |
| `39278377548/1` | Exhausted GitHub `502`; recover metadata reads and correlate workflow/deployment observations into one episode. | Owner-held recovery; duplicate removed |
| `39350886819/1` | Credential-service `503` during catalog read preparation; retry with complete fresh snapshots. | Owner-held recovery |
| `39440389130/1` | Completed publication, then PR-comment `403`; use receipt-only recovery without data mutation or duplicate Slack delivery. | One request for action |
| `39483553569/1` | Redundant whole-CI completion requirement despite passing exact ci-ready; use the verified gate. | Owner-held recovery |
| `39479051710/1` | MPAtlas single-job allowlist cannot admit reviewed job/scheduler resources; repair constrained scope. | Owner-held recovery |
| `39497479995/1` | Completed publication, then PR-comment `403`; repair only that original receipt. | One request for action |

`tests/test_historical_alert_counterfactual.py` executes the real policy with fake
clocks against these facts. Engine tests separately prove zero public messages
when confirmed transient recovery succeeds within thirty minutes, one parent
after eligibility, no automatic attempt replies, preserved uncertain historical
claims, fixed ownership deadlines and exact target-specific recovery.

Synthetic rehearsal messages and the escaped identity-decision unit-test notice
are prevented at test/transport boundaries. Consumer warnings about the Cerulean
coordinate change and missing scenes remain reviewed actionable announcements.
The brief historical WDPA observer outage belongs to native observer-health
coverage; its persistence threshold is tested separately and is not counted as a
GitHub incident or assigned a fabricated Git revision here.

Actual cloud cutover still requires complete shadow collection, seeded independent
heartbeat coverage and live routing verification. This replay is offline evidence;
it is not approval to disable a deployed route or proof of live Slack delivery.
