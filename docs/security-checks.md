# Security checks

The separate [security workflow](../.github/workflows/security.yml) runs on
pull requests and weekly (Monday at 04:23 UTC) on an Ubuntu runner with
Python 3.13. Scheduled runs use the default branch once the workflow is
merged. The workflow needs no repository secrets, requests only read access
to repository contents, and does not persist checkout credentials.

## What is checked

| Check | Command | Purpose |
| --- | --- | --- |
| Dependency vulnerabilities | `python -m pip_audit -r requirements.txt` | Resolve the application requirements and look for published vulnerabilities in direct and transitive dependencies. |
| Static security analysis | `python -m bandit -r app -ll` | Scan application Python source for potentially unsafe patterns at medium or high severity. |

The workflow first installs the application requirements and the two
scanners. Both scans can run even when the dependency scan fails, provided
installation succeeded and the run was not cancelled. Neither scanner's
failure is suppressed. The existing test workflow remains independent.

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
python -m pip install -r requirements.txt pip-audit bandit
python -m pip_audit -r requirements.txt
python -m bandit -r app -ll
```

The dependency scan requires access to public package and advisory services.
It does not need application database credentials. The static scan reads
application source; it does not run the application.

## Scope and limitations

These checks do not replace tests, manual security review, authentication
and authorization checks, or safe deployment configuration. They cannot
guarantee the absence of unknown vulnerabilities. The static scan is
limited to the application tree and the configured severity threshold.
The workflow does not audit operating-system packages or container images.

The dependency lock and its hashes are maintained separately from this
workflow change. Installing pinned direct requirements alone does not
guarantee a fully reproducible dependency tree, and the scanners themselves
are not pinned by this workflow. Review scanner output and the actual
resolved versions when investigating a failure.
