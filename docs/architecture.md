# Goal Engine architecture

This is the target architecture, introduced in small reviewed steps. Existing
behaviour and external interfaces stay unchanged unless a step explicitly adds
a security feature. The current debt baseline records exceptions during the
transition; it is not permission to introduce new exceptions.

## Layers

The order below runs from bottom to top.

| Layer | Responsibility |
| --- | --- |
| app/core | Settings, engine and session factory, logging and small utilities. No imports from other application layers. |
| app/domain | SQLAlchemy models and Pydantic schemas. |
| app/data | Repositories and all database session access and query execution. |
| app/security | Authentication, permissions, secret access, encryption, audit logging, rate limits, login lockout, security headers and crawler blocking. |
| app/engine | Pure intent classification, normalization, reasoning, learning and goal logic. |
| app/products | Product-specific logic and shared product helpers. |
| app/services | Use cases coordinating products, engine, repositories and security. |
| app/api/routes | Thin routers that declare permissions, validate schemas and call one service function. |
| app/main.py | App construction, middleware and router registration only. |

A module may import its own layer or lower layers, never higher layers.
The engine has a stricter rule: it may use only its own layer, domain and
core. It must not import data, security, products, services or API code.
Services supply the engine with plain data or small callables. Reasoning
logic remains technology-neutral.

Only the data layer runs database queries or uses database sessions; the
core database module owns session and engine construction. Routes never
access repositories or sessions directly. Products may use security helpers,
not bypass authorization. Static assets remain under app/static.

## Products

- **dbaba:** database agents, checks, knowledge and self-hosted runtime.
- **sheetu:** spreadsheet and document agents, office logic and knowledge.
- **shared:** reusable product helpers, website checks, automation, mail,
  planning, blocks, safety and specifications.

DBaba and Sheetu never import each other. To add a product, create a new
folder under app/products with its own logic and knowledge, use shared
helpers and lower layers, and expose its use cases through services and
routes. Do not modify another product to implement the new one.

### Where the products live now (after Step 5)

- `app/products/dbaba/`: the SQL agents (`sql*.py`), `database.py`, the knowledge in `dbknow/` (core, dialects,
  issues, errors, questions), and `local/` (DBaba installed on a client's server; `python -m app.local serve` still
  works through the forwarding package `app/local`).
- `app/products/sheetu/`: `office_agent.py`, `office/` (reading and fixing workbooks) and `officeknow/`.
- `app/products/shared/`: the agents both use (core, automation, website checker, mailer, planner, blocks, safety,
  direct, learn) and `bridge.py`.
- **DBaba and Sheetu never import each other.** When one needs something from the other, the owner registers it
  in `app/products/shared/bridge.py` (`provide("dbaba.concept_match", fn)`) and the other calls it
  (`call("dbaba.concept_match", ..., default=...)`). Plain shared constants live in `app/products/shared/`.
  A new product registers its own names the same way and touches nothing else.
- Knowledge files exist once, next to the code that reads them; code finds them with `Path(__file__)`, so after
  moving a module the number of `.parent` steps must be rechecked.
- Old import paths (`app/agents/...`, `app/office`, `app/officeknow`, `app/dbknow`, `app/local`) are forwarding
  modules only. They never alias a package or change a package's search path. Step 6 removes the ones nothing uses.
- Still to do (small follow-up PR): split `app/products/dbaba/database.py` and `app/products/sheetu/office_agent.py`
  (1,300-1,600 lines each) into smaller modules by topic.

## Security model

The target is default deny. Each route declares exactly one permission
through require(...) in app/security. Missing declarations fail the guard
test once the transition is complete. Permissions do not replace ownership
checks or existing authorization rules.

| Permission | Who may call |
| --- | --- |
| public | Anyone, subject to applicable request safeguards. |
| visitor | A visitor with the required visitor identity. |
| account | An authenticated account. |
| admin | A caller with valid administrator authorization. |
| api_key | A caller with a valid API key. |
| local_screen | An authenticated local screen session. |
| local_setup | A caller satisfying the local setup authorization rules. |

Keep existing host-header protection, cookie flags, login lockout,
JSON-only cookie POST checks, loopback-only setup token restrictions,
hashed API keys, read-only connections and preview, approval and undo
safeguards. Secrets come through settings or environment access, not code.

The audit log records login, logout, failed login lockout, account deletion,
personal data deletion, admin actions, API key creation and revocation,
database address changes and settings changes. Record who, what, when and
a hashed source IP; never record passwords, tokens, keys or message text.

The crawler policy preserves the closed-site robots policy, disallows
AI-training and answer crawlers when open, blocks matching user agents
except the robots.txt request, and adds noai/noimageai response headers
and HTML metadata. This is a request policy, not a guarantee against
crawlers that ignore it or disguise their identity.

The supply-chain step adds a pinned, hashed dependency lock and automated
dependency vulnerability and static security checks.

## Architecture debts

tests/architecture_baseline.json is the explicit baseline for existing
import, database-session and undeclared-route debts. The lists may only
shrink. Guard tests reject both new violations and stale entries for
violations that have been fixed. Do not add exceptions to make a test pass.

After a step removes debts, run:

```sh
python -m tests.arch_scan
```

Replace the three lists with the corresponding sorted output lists,
preserving the baseline metadata. Review the diff to ensure only resolved
entries disappear. The current route scan inventories existing routes;
Step 7 adds recognition of permission declarations. Maintain scanner
coverage as code moves so that relocation alone cannot hide a violation.

## Migration plan

- Step 1: Document the architecture and establish guard tests and the debt baseline.
- Step 2: Move settings, database construction, models and schemas into core and domain.
- Step 2b: Extract database access into data repositories.
- Step 3: Split routes and services, leaving main as app wiring.
- Step 4: Move reasoning and learning into a pure engine.
- Step 5: Separate DBaba, Sheetu and shared product modules.
- Step 6: Clear remaining debts and remove only unused compatibility shims.
- Step 7: Centralize permissions and authentication, add audit logging and clear undeclared routes.
- Step 8: Add crawler blocking, robots rules and response metadata.
- Step 9: Lock dependencies and add supply-chain and static security checks.

Keep legacy import paths and the local command entry points working during
the migration. Do not weaken existing tests. Each step is a separate
reviewed branch and pull request; do not merge or advance before approval.
