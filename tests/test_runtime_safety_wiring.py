from pathlib import Path

def test_noninteractive_deploy_cannot_override_a_failed_test_suite():
    source = Path("scripts/deploy.sh").read_text(encoding="utf-8")
    failure = source.index("WARNING: Some tests failed")
    prompt = source.index("Continue with deploy?")
    guard = source.index('[[ "${CI:-}" == "true" || ! -t 0 ]]')
    abort = source.index("exit 1", guard)

    assert failure < guard < abort < prompt


def test_deploy_serializes_sync_and_uses_unique_rollback_staging():
    source = Path("scripts/deploy.sh").read_text(encoding="utf-8")

    lock = source.index("==> Acquiring deployment lock")
    sync = source.index("==> Syncing files")
    assert lock < sync
    assert 'REMOTE_LOCK_DIR="/run/lock/betting-pod-shop-deploy"' in source
    assert "mkdir '${REMOTE_LOCK_DIR}'" in source
    assert "trap release_deploy_lock EXIT INT TERM" in source
    assert r'''if [ \"\$owner\" = '${DEPLOY_TOKEN}' ]; then''' in source

    assert 'REMOTE_BACKUP_DIR="${REMOTE_DIR}${BACKUP_SUFFIX}.${DEPLOY_TOKEN}"' in source
    assert "cp -a '${REMOTE_DIR}' '${REMOTE_BACKUP_DIR}'" in source
    assert "mv '${REMOTE_BACKUP_DIR}' '${REMOTE_DIR}'" in source
