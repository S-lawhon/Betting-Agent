from pathlib import Path

def test_noninteractive_deploy_cannot_override_a_failed_test_suite():
    source = Path("scripts/deploy.sh").read_text(encoding="utf-8")
    failure = source.index("WARNING: Some tests failed")
    prompt = source.index("Continue with deploy?")
    guard = source.index('[[ "${CI:-}" == "true" || ! -t 0 ]]')
    abort = source.index("exit 1", guard)

    assert failure < guard < abort < prompt
