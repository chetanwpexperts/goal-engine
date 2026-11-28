# Security checks

The separate [security workflow](../.github/workflows/security.yml) runs on
pull requests and weekly (Monday at 04:23 UTC) on an Ubuntu runner with
Python 3.13. Scheduled runs use the default branch once the workflow is
merged. The workflow needs no repository secrets, requests only read access
to repository contents, and does not persist checkout credentials.

## What is checked

| Check | Command | Purpose |
| --- | --- | --- |
| Locked install | `python -m pip install --require-hashes --no-deps -r requirements.lock` | Installs exactly the versions in the lock; every downloaded file must match its recorded SHA-256 hash, so a swapped or tampered package fails. |
| Lock matches requirements | small script in the workflow | Every pin in `requirements.txt` has the same version in `requirements.lock`. |
| Dependency vulnerabilities | `python -m pip_audit --disable-pip --no-deps -r requirements.lock` | Looks up every locked package (direct and transitive) in the published advisory databases. |
| Static security analysis | `python -m bandit -r app -ll -b .bandit-baseline.json` | Scans application source for unsafe patterns at medium or high severity. Findings already reviewed are in `.bandit-baseline.json`; only new ones fail. |

The scanners are pinned (pip-audit 2.10.1, bandit 1.9.4) so a new scanner release cannot turn a run red by itself.

### The reviewed static findings

All 113 entries in `.bandit-baseline.json` are B608 ("SQL built from strings"). They are in DBaba's SQL modules and
Sheetu's compare: DBaba writes example and corrected queries as text for people to read, and runs queries only on
its own scratch SQLite copies. The two queries in `app/products/dbaba/database.py` take a table name only after
`_ident()` has checked it is a plain name. A new B608 anywhere still fails the check and needs the same review.

Findings fixed or explained in the code (Step 9):

- B307 `eval` of spreadsheet formulas (`app/products/sheetu/office/sheetdata.py`): a crafted formula could reach Python
  objects through `__class__`, indexing or calling a result. Such formulas are now refused before evaluation
  (`tests/test_formula_sandbox.py`); what is left is numbers, quoted text, Sheetu's own functions and operators.
- B324 SHA-1 in `app/engine/ml/brain.py`: used only to notice a repeated answer; marked `usedforsecurity=False`.
- B310, B104, B108: a fixed `https://docs.google.com` URL, reading a database's listen setting to warn about it, and
  the usual socket paths a database uses. Each line says why with `# nosec`.

## Reading a failure

1. Open the pull request's **Checks** tab, or open **Actions > security**
   and select the failed run.
2. Expand the failed step. If installation failed, inspect dependency
   resolution, package availability and Python compatibility before
   treating the failure as a security finding.
3. For a dependency finding, read the package name, affected version,
   advisory identifier and available fixed versions. Check whether the
   affected package is direct or transitive. Review a compatible upgrade,
   update the dependency inputs and regenerate the lock through the
   project's approved locking procedure, then run the tests and scans again.
4. For a static finding, read the file, line, test identifier, severity,
   confidence and explanation. Inspect the surrounding code and actual
   execution path; replace unsafe behaviour with a safe equivalent.
   A scanner report is a prompt for investigation, not proof that a
   vulnerability is exploitable.
5. If a finding is disputed or has no available fix, document the evidence
   and proposed mitigation for review. Do not silence a finding merely to
   make the check green. Network or advisory-service errors also require
   investigation and are not evidence that the dependencies are safe.

## Reproduce the checks

From the repository root, in a clean Python 3.13 virtual environment:

```sh
python -m pip install --require-hashes --no-deps -r requirements.lock
python -m pip install pip-audit==2.10.1 bandit==1.9.4
python -m pip_audit --disable-pip --no-deps -r requirements.lock
python -m bandit -r app -ll -b .bandit-baseline.json
```

## Changing a dependency

1. Change the pin in `requirements.txt`.
2. Regenerate the lock (Linux, Python 3.13):
   `pip install pip-tools && pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url -o requirements.lock requirements.txt`
3. Run the tests and the checks above. Commit both files together.

To update the static baseline after reviewing a new finding (only when it is a reviewed false positive, with the
reason added here): `python -m bandit -r app -ll -f json -o .bandit-baseline.json`.

## Scope and limitations

These checks do not replace tests, manual security review, authentication
and authorization checks, or safe deployment configuration. They cannot
guarantee the absence of unknown vulnerabilities. The static scan is
limited to the application tree and the configured severity threshold.
The workflow does not audit operating-system packages or container images.

The lock is made on Linux with Python 3.13, the platform the site and CI run on. The on-server installers still
install from `requirements.txt`; moving them to the lock is a separate change.
