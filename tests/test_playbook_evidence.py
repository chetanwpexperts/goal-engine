from app import dbknow
from app.dbknow.diagnosis import evaluate


NEW_PLAYBOOK_IDS = ['app-connection-leak', 'app-laravel-slow-relations', 'app-long-transaction', 'app-migration-locks-table', 'app-n-plus-one-queries', 'app-orm-select-star', 'app-pool-waiting', 'app-wordpress-autoload-bloat', 'app-wordpress-postmeta-slow', 'backup-dump-locks-writes', 'backup-dump-packet-too-large', 'backup-dump-permission-denied', 'backup-no-recent-copy', 'backup-point-in-time-not-ready', 'config-buffer-cache-small', 'config-buffer-cache-too-large', 'config-max-connections-high', 'config-slow-log-off', 'config-strict-mode-surprise', 'config-timezone-mismatch', 'config-work-memory-risky', 'config-work-memory-small', 'maintenance-autovacuum-behind', 'maintenance-index-bloat', 'maintenance-index-invalid', 'maintenance-stale-statistics', 'maintenance-table-bloat', 'maintenance-table-corruption', 'maintenance-vacuum-disabled', 'maintenance-xid-wraparound', 'replication-duplicate-key', 'replication-lag-growing', 'replication-missing-row', 'replication-replica-writable', 'replication-slot-retains-wal', 'restore-character-set-mismatch', 'restore-definer-missing', 'restore-foreign-key-order', 'schema-case-sensitive-names', 'schema-date-as-text', 'schema-duplicate-indexes', 'schema-large-text-hot-table', 'schema-missing-foreign-keys', 'schema-mixed-character-sets', 'schema-money-float', 'schema-nullable-required-fields', 'schema-overwide-varchar', 'schema-table-without-primary-key', 'schema-too-many-indexes', 'schema-unused-indexes', 'security-anonymous-users', 'security-default-public-connect', 'security-file-privilege', 'security-grant-option', 'security-no-tls', 'security-old-auth-plugin', 'security-password-never-expires', 'security-port-public', 'security-public-schema-create', 'security-remote-root', 'security-user-without-password']


def test_all_new_playbooks_have_evidence_paths():
    issues = {
        issue["id"]: issue
        for issue in dbknow.load("issues")
    }

    assert len(NEW_PLAYBOOK_IDS) == 61

    for iid in NEW_PLAYBOOK_IDS:
        assert iid in issues

        issue = issues[iid]

        check_ids = {
            check["id"]
            for check in issue["diagnose"]
            if check.get("id")
        }

        assert check_ids, iid

        for cause in issue["causes"]:
            signals = cause.get("signals") or []

            assert signals, (
                iid,
                cause.get("id"),
                "cause has no evidence signals",
            )

            for signal in signals:
                assert (
                    signal.get("check")
                    in check_ids
                ), (
                    iid,
                    cause.get("id"),
                    signal,
                )

                assert str(
                    signal.get(
                        "evidence",
                        "",
                    )
                ).strip(), (
                    iid,
                    cause.get("id"),
                    "signal has no evidence text",
                )


def test_manual_evidence_never_auto_diagnoses():
    issue = {
        "causes": [
            {
                "id": "manual-only",
                "title_en": "Manual only",
                "title_hi": "Manual only",
                "signals": [
                    {
                        "check": "check_1",
                        "manual": True,
                        "evidence": (
                            "Operator must confirm "
                            "this evidence"
                        ),
                    }
                ],
                "minimum_signals": 1,
            }
        ],
        "fixes": [],
        "verify": {
            "en": "verify",
            "hi": "verify",
        },
    }

    checks = [
        {
            "id": "check_1",
            "look_for": "manual evidence",
        }
    ]

    raw_results = [
        {
            "columns": ["value"],
            "rows": [[999]],
        }
    ]

    result = evaluate(
        issue,
        checks,
        raw_results,
    )

    assert result["likely_causes"] == []
    assert result["found"] == []
