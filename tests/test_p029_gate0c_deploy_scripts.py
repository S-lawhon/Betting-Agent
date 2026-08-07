from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCAL_DEPLOY = (ROOT / "scripts" / "deploy_p029_gate0c.sh").read_text()
HOST_UPGRADE = (ROOT / "scripts" / "upgrade_p029_gate0c.sh").read_text()


def test_local_deploy_transfers_only_sanctioned_artifacts():
    assert "data/" not in LOCAL_DEPLOY
    assert ".env" not in LOCAL_DEPLOY
    assert "scripts/p029_gate0c_checkpoint.py" in LOCAL_DEPLOY
    assert "scripts/upgrade_p029_gate0c.sh" in LOCAL_DEPLOY
    assert "src/combo_copula.py" in LOCAL_DEPLOY
    assert "combo_research/recalib/recalibrated_model.json" in LOCAL_DEPLOY
    assert "rsync -az --relative" in LOCAL_DEPLOY
    assert "--no-perms --no-owner --no-group" in LOCAL_DEPLOY
    assert "chmod 0755" in LOCAL_DEPLOY


def test_host_upgrade_uses_dropin_and_never_rewrites_main_unit():
    assert "20-gate0c-memory.conf" in HOST_UPGRADE
    assert "MemoryMax=768M" in HOST_UPGRADE
    assert ">/etc/systemd/system/p029-shadow.service" not in HOST_UPGRADE
    assert "reset --hard" not in HOST_UPGRADE
    assert "git " not in HOST_UPGRADE
    assert "NRestarts" in HOST_UPGRADE
    assert "SubState" in HOST_UPGRADE


def test_host_upgrade_enforces_tape_prerequisites():
    assert "EXPECTED_MODEL_SHA" in HOST_UPGRADE
    assert "NO DECISION" in HOST_UPGRADE
    assert "p029-archive.timer" in HOST_UPGRADE
    assert "in-zone due backlog: 0" in HOST_UPGRADE
    assert "mode=ro" in HOST_UPGRADE
    assert "post-restart resolver cycle" in HOST_UPGRADE
    assert "due_zone()" not in HOST_UPGRADE
